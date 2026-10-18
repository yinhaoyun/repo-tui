"""Per-project git status collection.

This is the core of the "too many files" fix: instead of relying on
`repo status` (which shells out to git per-project anyway, but gives us no
control over noise), we run git ourselves per project, in parallel across a
thread pool, and filter the result through the user's ignore rules before it
ever reaches the UI.
"""

from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, Optional

from .ignore import IgnoreRules
from .models import FileStatus, HeadChange, Project

_GIT_TIMEOUT = 15


def _run_git(cwd, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=_GIT_TIMEOUT,
    )
    return result.stdout


def _parse_numstat(output: str) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added_s, deleted_s, path = parts
        if "\t" in path or "=>" in path:
            # renamed path in the form "old => new" or "{old => new}/rest";
            # numstat with default settings already resolves this, so just
            # take the raw string as the path key.
            pass
        added = 0 if added_s == "-" else int(added_s)
        deleted = 0 if deleted_s == "-" else int(deleted_s)
        stats[path] = (added, deleted)
    return stats


def _parse_status_v2(output: str) -> tuple[str, bool, int, int, list[FileStatus]]:
    branch = ""
    detached = False
    ahead = 0
    behind = 0
    files: list[FileStatus] = []

    for line in output.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head "):]
            detached = branch == "(detached)"
            if detached:
                branch = ""
        elif line.startswith("# branch.ab "):
            rest = line[len("# branch.ab "):]
            for token in rest.split():
                if token.startswith("+"):
                    ahead = int(token[1:])
                elif token.startswith("-"):
                    behind = int(token[1:])
        elif line.startswith("1 "):
            # 1 XY sub mH mI mW hH hI path
            fields = line.split(" ", 8)
            xy = fields[1]
            path = fields[-1]
            files.append(FileStatus(path=path, index_status=xy[0], worktree_status=xy[1]))
        elif line.startswith("2 "):
            # 2 XY sub mH mI mW hH hI X<score> path<TAB>origPath
            fields = line.split(" ", 9)
            xy = fields[1]
            path, _, orig_path = fields[-1].partition("\t")
            files.append(
                FileStatus(
                    path=path, index_status=xy[0], worktree_status=xy[1], orig_path=orig_path
                )
            )
        elif line.startswith("u "):
            fields = line.split(" ", 10)
            path = fields[-1]
            files.append(FileStatus(path=path, index_status="U", worktree_status="U"))
        elif line.startswith("? "):
            path = line[2:]
            files.append(FileStatus(path=path, index_status="?", worktree_status="?"))

    return branch, detached, ahead, behind, files


def collect_project_status(project: Project, ignore: IgnoreRules) -> Project:
    """Populate git status fields on `project` in place, and return it."""
    try:
        status_out = _run_git(
            project.abs_path, ["status", "--porcelain=v2", "--branch", "--untracked-files=all"]
        )
        branch, detached, ahead, behind, files = _parse_status_v2(status_out)

        unstaged = _parse_numstat(_run_git(project.abs_path, ["diff", "--numstat"]))
        staged = _parse_numstat(_run_git(project.abs_path, ["diff", "--cached", "--numstat"]))

        for f in files:
            a1, d1 = unstaged.get(f.path, (0, 0))
            a2, d2 = staged.get(f.path, (0, 0))
            f.added, f.deleted = a1 + a2, d1 + d2
            f.ignored = ignore.matches(project.name, f.path)

        branches_out = _run_git(
            project.abs_path, ["for-each-ref", "--format=%(refname:short)", "refs/heads/"]
        )
        local_branches = [b for b in branches_out.splitlines() if b.strip()]
        shallow = _run_git(project.abs_path, ["rev-parse", "--is-shallow-repository"])

        project.current_branch = branch
        project.is_detached = detached
        project.ahead = ahead
        project.behind = behind
        project.local_branch_count = len(local_branches)
        project.local_branches = local_branches
        project.is_shallow = shallow.strip() == "true"
        project.files = files
        project.added = sum(f.added for f in files if not f.ignored)
        project.deleted = sum(f.deleted for f in files if not f.ignored)
        project.untracked_count = sum(
            1 for f in files if f.is_untracked and not f.ignored
        )
        project.error = ""
    except subprocess.TimeoutExpired:
        project.error = "git status timed out"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        project.error = str(exc)
    return project


def collect_all(
    projects: Iterable[Project],
    ignore: IgnoreRules,
    max_workers: int = 16,
    on_progress: Optional[Callable[[int, int, Project], None]] = None,
) -> list[Project]:
    """Synchronous, thread-parallel status collection across many projects.

    `on_progress(done, total, project)` is called from worker threads as each
    project finishes, so it must be thread-safe."""
    projects = list(projects)
    if not projects:
        return []
    lock = threading.Lock()
    done = 0

    def collect(project: Project) -> Project:
        nonlocal done
        collect_project_status(project, ignore)
        if on_progress is not None:
            with lock:
                done += 1
                count = done
            on_progress(count, len(projects), project)
        return project

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(collect, projects))


def head_commits(projects: Iterable[Project], max_workers: int = 16) -> dict[str, str]:
    """{project path: HEAD sha} ('' when it can't be read), in parallel."""

    def head(project: Project) -> tuple[str, str]:
        try:
            sha = _run_git(project.abs_path, ["rev-parse", "--verify", "-q", "HEAD"]).strip()
        except (OSError, subprocess.TimeoutExpired):
            sha = ""
        return project.path, sha

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(pool.map(head, projects))


def head_changes(
    projects: Iterable[Project],
    before: dict[str, str],
    after: dict[str, str],
    max_workers: int = 16,
) -> list[HeadChange]:
    """Projects whose HEAD moved between `before` and `after`, with how many
    commits came in and how many are no longer on HEAD. Sorted by path."""
    moved = [
        p for p in projects
        if before.get(p.path) and after.get(p.path) and before[p.path] != after[p.path]
    ]

    def count(project: Project, revs: str) -> int:
        out = _run_git(project.abs_path, ["rev-list", "--count", revs]).strip()
        return int(out) if out.isdigit() else 0

    def change(project: Project) -> HeadChange:
        old, new = before[project.path], after[project.path]
        return HeadChange(
            project.path, old, new,
            added=count(project, f"{old}..{new}"),
            dropped=count(project, f"{new}..{old}"),
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return sorted(pool.map(change, moved), key=lambda c: c.path)


_LOG_LIMIT = 200
_LOG_FORMAT = "--format=%h  %s  (%an, %ar)"


def describe_head_change(project: Project, change: HeadChange) -> str:
    """Text for the sync report's detail pane: the move, a diff summary, and
    the commits it brought in (and dropped, if any)."""
    cwd = project.abs_path
    lines = [f"{change.old[:12]} → {change.new[:12]}"]
    stat = _run_git(cwd, ["diff", "--shortstat", change.old, change.new]).strip()
    if stat:
        lines.append(stat)
    for revs, heading, total in (
        (f"{change.old}..{change.new}", "New commits", change.added),
        (f"{change.new}..{change.old}", "No longer on HEAD", change.dropped),
    ):
        if not total:
            continue
        lines += ["", f"{heading} ({total}):"]
        log = _run_git(cwd, ["log", "--no-color", _LOG_FORMAT, "-n", str(_LOG_LIMIT), revs])
        lines += [f"  {line}" for line in log.splitlines()]
        if total > _LOG_LIMIT:
            lines.append(f"  …and {total - _LOG_LIMIT} more")
    return "\n".join(lines)


def read_file_diff(project: Project, file: FileStatus) -> str:
    """`git diff` text for one file, for the diff view: everything since
    HEAD, staged or not (a staged-only change would show nothing in a
    plain `git diff`); an untracked file's whole content as added lines."""
    if file.is_untracked:
        return _run_git(project.abs_path, ["diff", "--no-index", "--", "/dev/null", file.path])
    paths = [file.orig_path, file.path] if file.orig_path else [file.path]
    text = _run_git(project.abs_path, ["diff", "-M", "HEAD", "--", *paths])
    if not text:  # no HEAD yet (fresh repo): what's staged
        text = _run_git(project.abs_path, ["diff", "--cached", "--", *paths])
    return text


def file_log(project: Project, rel_path: str, limit: int = 200) -> str:
    """The commits that touched one file (following renames), newest first."""
    return _run_git(
        project.abs_path,
        [
            "log", "--follow", f"-n{limit}", "--date=short",
            "--format=%h  %ad  %<(16,trunc)%an  %s", "--", rel_path,
        ],
    )


def file_blame(project: Project, rel_path: str) -> str:
    """`git blame` of the file as committed plus any uncommitted edits."""
    return _run_git(project.abs_path, ["blame", "--date=short", "--", rel_path])


def _git_lines(project: Project, args: list[str]) -> tuple[int, list[str]]:
    result = subprocess.run(
        ["git", *args],
        cwd=project.abs_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=_GIT_TIMEOUT,
    )
    return result.returncode, [
        line for line in (result.stdout + result.stderr).splitlines() if line
    ]


def branch_name_problem(name: str) -> str:
    """Why `name` can't be a branch name, or "": git's own rules (no
    leading '-', no spaces, '..', '~', ...). A name git would expand, such
    as @{-1}, is refused too, so repo gets exactly what was typed."""
    try:
        result = subprocess.run(
            ["git", "check-ref-format", "--branch", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""  # can't ask git; repo itself will complain
    if result.returncode == 0 and result.stdout.strip() == name:
        return ""
    return f"'{name}' isn't a valid branch name"


def unpushed_commits(
    project: Project, branch: str, limit: int = 5
) -> Optional[tuple[int, list[str]]]:
    """Commits on local `branch` that its upstream doesn't have: (count,
    the newest `limit` as "<sha> <subject>"), or None when the branch has
    no upstream to compare with."""
    span = f"{branch}@{{upstream}}..refs/heads/{branch}"
    returncode, lines = _git_lines(project, ["rev-list", "--count", span])
    if returncode or not lines or not lines[0].isdigit():
        return None
    count = int(lines[0])
    if not count:
        return 0, []
    _, shown = _git_lines(project, ["log", "--format=%h %s", f"-n{limit}", span])
    return count, shown


def _paths_of(file: FileStatus) -> list[str]:
    return [file.path, file.orig_path] if file.orig_path else [file.path]


def stage_file(project: Project, file: FileStatus) -> tuple[int, list[str]]:
    """`git add -A` one file: its edits, its deletion, or both sides of a rename."""
    return _git_lines(project, ["add", "-A", "--", *_paths_of(file)])


def unstage_file(project: Project, file: FileStatus) -> tuple[int, list[str]]:
    """Take one file out of the index, keeping the working-tree edits."""
    return _git_lines(project, ["restore", "--staged", "--", *_paths_of(file)])


_PATHS_PER_CALL = 200  # keep argv well under the OS limit on huge changesets


def discard_changes(project: Project) -> tuple[int, list[str]]:
    """Throw away exactly what the Stat column counts: every non-ignored
    changed file. Tracked changes (staged or not) are restored to HEAD and
    untracked files are deleted. Files matched by the ignore rules, commits,
    and the checked-out branch are left alone.

    Returns (exit code, output lines); the exit code is the first non-zero
    one from git, else 0."""
    return discard_files(project, [f for f in project.files if not f.ignored])


def discard_files(project: Project, files: list[FileStatus]) -> tuple[int, list[str]]:
    """Restore these tracked files to HEAD and delete these untracked ones;
    see discard_changes."""
    tracked: list[str] = []
    for f in files:
        if not f.is_untracked:
            tracked.append(f.path)
            if f.orig_path:
                tracked.append(f.orig_path)
    untracked = [f.path for f in files if f.is_untracked]

    returncode = 0
    output: list[str] = []

    def run(args: list[str]) -> int:
        nonlocal returncode
        result = subprocess.run(
            ["git", *args],
            cwd=project.abs_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT,
        )
        output.extend(line for line in (result.stdout + result.stderr).splitlines() if line)
        if result.returncode and not returncode:
            returncode = result.returncode
        return result.returncode

    for i in range(0, len(tracked), _PATHS_PER_CALL):
        chunk = tracked[i : i + _PATHS_PER_CALL]
        if run(["restore", "--source=HEAD", "--staged", "--worktree", "--", *chunk]) == 0:
            output.extend(f"Restored {path}" for path in chunk)
    for i in range(0, len(untracked), _PATHS_PER_CALL):
        run(["clean", "-f", "--", *untracked[i : i + _PATHS_PER_CALL]])
    return returncode, output


def fetch_remote(project: Project) -> tuple[str, str]:
    """(remote to fetch from, "") or ("", why there isn't one): the
    manifest's remote for the project if the checkout has it, else its only
    remote."""
    remotes = _run_git(project.abs_path, ["remote"]).split()
    if project.remote and project.remote in remotes:
        return project.remote, ""
    if len(remotes) == 1:
        return remotes[0], ""
    if not remotes:
        return "", f"{project.path} has no git remote to fetch from"
    return "", (
        f"{project.path} has several remotes ({', '.join(remotes)}) and none is "
        f"the manifest's ({project.remote or 'unknown'})"
    )
