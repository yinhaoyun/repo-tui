"""Per-project git status collection.

This is the core of the "too many files" fix: instead of relying on
`repo status` (which shells out to git per-project anyway, but gives us no
control over noise), we run git ourselves per project, in parallel across a
thread pool, and filter the result through the user's ignore rules before it
ever reaches the UI.
"""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from .ignore import IgnoreRules
from .models import FileStatus, Project

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
            path = fields[-1].split("\t")[0]
            files.append(FileStatus(path=path, index_status=xy[0], worktree_status=xy[1]))
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

        branch_count_out = _run_git(
            project.abs_path, ["for-each-ref", "--format=%(refname)", "refs/heads/"]
        )
        local_branch_count = len([l for l in branch_count_out.splitlines() if l.strip()])

        project.current_branch = branch
        project.is_detached = detached
        project.ahead = ahead
        project.behind = behind
        project.local_branch_count = local_branch_count
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
    projects: Iterable[Project], ignore: IgnoreRules, max_workers: int = 16
) -> list[Project]:
    """Synchronous, thread-parallel status collection across many projects."""
    projects = list(projects)
    if not projects:
        return []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(lambda p: collect_project_status(p, ignore), projects))


def read_file_diff(project: Project, rel_path: str, staged: bool = False) -> str:
    """Return `git diff` text for a single file, for the detail/diff view."""
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.extend(["--", rel_path])
    return _run_git(project.abs_path, args)
