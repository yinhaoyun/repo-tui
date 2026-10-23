"""Interaction with the `repo` tool and the on-disk `.repo/` metadata.

We avoid shelling out to `repo status`/`repo info` for routine refreshes
because they are slow across a large tree and give us less control than
talking to git directly per-project (see git_backend.py). We do use `.repo/`
metadata directly (project list, manifest.xml) to discover the tree's shape,
and we do shell out to `repo` itself for actions the user explicitly asks
for: sync, forall, start, checkout, abandon.
"""

from __future__ import annotations

import asyncio
import codecs
import fcntl
import os
import pty
import re
import shutil
import signal
import struct
import termios
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .models import Project, TreeInfo

OutputCallback = Callable[[str], Awaitable[None]]

# repo's colour/erase-line escapes, stripped from output run under a pty.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class RepoTreeError(RuntimeError):
    """Raised when the current directory is not inside a repo tree."""


def find_repo_root(start: Optional[Path] = None) -> Path:
    """Walk upward from `start` (default: cwd) looking for a `.repo` dir."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".repo").is_dir():
            return candidate
    raise RepoTreeError(
        f"No .repo directory found in {here} or any parent. "
        "Run repo-tui from inside a `repo init`-ed tree."
    )


def _read_manifest_xml(repo_root: Path) -> ET.Element:
    """.repo/manifest.xml with its <include>s inlined. Older repo makes it a
    symlink to the chosen manifest; newer repo writes a stub that just
    <include>s it, which on its own lists no projects."""
    manifest_path = repo_root / ".repo" / "manifest.xml"
    root = ET.parse(manifest_path).getroot()
    _inline_includes(root, repo_root / ".repo" / "manifests", depth=0)
    return root


def _local_manifests(repo_root: Path) -> list[ET.Element]:
    """The tree's local manifests, in the order repo applies them: the
    legacy .repo/local_manifest.xml, then .repo/local_manifests/*.xml sorted
    by name. Each has its <include>s (relative to its own folder) inlined."""
    repo_dir = repo_root / ".repo"
    local_dir = repo_dir / "local_manifests"
    paths = [repo_dir / "local_manifest.xml"]
    try:
        paths += sorted(p for p in local_dir.iterdir() if p.suffix == ".xml")
    except OSError:
        pass
    roots = []
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        _inline_includes(root, path.parent, depth=0)
        roots.append(root)
    return roots


@dataclass
class _ManifestProject:
    name: str
    revision: str
    remote: str
    clone_depth: str


def _manifest_projects(repo_root: Path) -> dict[str, _ManifestProject]:
    """{path: manifest fields} after the main manifest and then every local
    manifest have been applied in order, the way repo does: <project> adds
    one, <extend-project> changes the revision/remote of the ones with its
    name (and path, if given), <remove-project> drops them. A project's
    revision is its own, else its remote's, else <default>'s."""
    try:
        roots = [_read_manifest_xml(repo_root)]
    except (OSError, ET.ParseError):
        roots = []
    roots += _local_manifests(repo_root)
    defaults: dict[str, str] = {}
    remote_revisions: dict[str, str] = {}
    for root in roots:  # a local manifest may add a remote but not a default
        default = root.find("default")
        if default is not None and not defaults:
            defaults = dict(default.attrib)
        for remote in root.findall("remote"):
            if remote.get("name") and remote.get("revision"):
                remote_revisions[remote.get("name")] = remote.get("revision")

    def revision_for(remote: str, own: str) -> str:
        return own or remote_revisions.get(remote, "") or defaults.get("revision", "")

    projects: dict[str, _ManifestProject] = {}
    for root in roots:
        for el in root:
            name = el.get("name", "")
            if el.tag == "project":
                path = el.get("path") or name
                remote = el.get("remote", defaults.get("remote", ""))
                projects[path] = _ManifestProject(
                    name or path,
                    revision_for(remote, el.get("revision", "")),
                    remote,
                    el.get("clone-depth", ""),
                )
                continue
            matching = [
                path for path, p in projects.items()
                if p.name == name and el.get("path", path) == path
            ]
            if el.tag == "remove-project":
                for path in matching:
                    del projects[path]
            elif el.tag == "extend-project":
                for path in matching:
                    p = projects[path]
                    if el.get("remote"):
                        p.remote = el.get("remote")
                        p.revision = revision_for(p.remote, el.get("revision", ""))
                    elif el.get("revision"):
                        p.revision = el.get("revision")
    return projects


def _inline_includes(root: ET.Element, manifests_dir: Path, depth: int) -> None:
    """Replace each <include> with the included manifest's elements, in
    place: order matters, e.g. a later <remove-project> drops a project
    that an earlier include added."""
    for include in root.findall("include"):
        index = list(root).index(include)
        root.remove(include)
        name = include.get("name", "")
        if not name or depth > 8:
            continue
        try:
            included = ET.parse(manifests_dir / name).getroot()
        except (OSError, ET.ParseError):
            continue
        _inline_includes(included, manifests_dir, depth + 1)
        for offset, child in enumerate(list(included)):
            root.insert(index + offset, child)


def load_tree_info(repo_root: Path) -> TreeInfo:
    info = TreeInfo(root=repo_root)
    info.manifest_name = current_manifest_file(repo_root)
    info.manifest_branch = _manifest_branch(repo_root)
    return info


def current_manifest_file(repo_root: Path) -> str:
    """The manifest file `repo init -m` picked, relative to .repo/manifests
    (e.g. "default.xml"), or "" if it can't be told."""
    manifest_path = repo_root / ".repo" / "manifest.xml"
    manifests_dir = repo_root / ".repo" / "manifests"
    if manifest_path.is_symlink():
        target = manifest_path.resolve()
        try:
            return str(target.relative_to(manifests_dir.resolve()))
        except ValueError:
            return target.name
    try:
        root = ET.parse(manifest_path).getroot()
    except (OSError, ET.ParseError):
        return ""
    includes = root.findall("include")
    if len(includes) == 1 and root.find("project") is None:
        return includes[0].get("name", "")
    return ""


def _manifest_branch(repo_root: Path) -> str:
    manifests_git = repo_root / ".repo" / "manifests"
    if manifests_git.is_dir():
        try:
            out = _run_git_capture(manifests_git, ["symbolic-ref", "--short", "HEAD"])
            if out:
                local = out.strip()
                # repo checks the manifest out on a local branch named
                # "default" that tracks the branch given to `repo init -b`.
                merge = _run_git_capture(
                    manifests_git, ["config", "--get", f"branch.{local}.merge"]
                ).strip()
                return _strip_heads(merge) or local
        except Exception:
            pass
    try:
        root = _read_manifest_xml(repo_root)
        default = root.find("default")
        if default is not None:
            return default.get("revision", "")
    except Exception:
        pass
    return ""


def _run_git_capture(cwd: Path, args: list[str]) -> str:
    import subprocess

    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result.stdout


def _strip_heads(ref: str) -> str:
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref


# -- switching the tree to another manifest branch / file -------------------


def fetch_manifests(repo_root: Path) -> str:
    """`git fetch` the manifest repo so its branch list is current. Returns
    "" on success, or what went wrong (the cached list still works)."""
    import subprocess

    manifests = repo_root / ".repo" / "manifests"
    if not manifests.is_dir():
        return f"{manifests} doesn't exist"
    try:
        result = subprocess.run(
            ["git", "fetch", "--quiet", "--prune"],
            cwd=manifests,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        return "git fetch timed out after 60s"
    except OSError as exc:
        return str(exc)
    if result.returncode != 0:
        lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
        reasons = [line for line in lines if line.startswith(("fatal:", "error:"))]
        return " ".join(reasons or lines) or f"git fetch exited with {result.returncode}"
    return ""


def manifest_branches(repo_root: Path) -> list[str]:
    """Branches of the manifest repo (remote-tracking ones, plus any local
    ones other than repo's own "default"), sorted."""
    manifests = repo_root / ".repo" / "manifests"
    try:
        out = _run_git_capture(
            manifests,
            ["for-each-ref", "--format=%(refname)", "refs/remotes/", "refs/heads/"],
        )
    except Exception:
        return []
    names = set()
    for ref in out.split():
        if ref.startswith("refs/remotes/"):
            # refs/remotes/<remote>/<branch...>
            parts = ref.split("/", 3)
            if len(parts) == 4 and parts[3] != "HEAD":
                names.add(parts[3])
        elif ref.startswith("refs/heads/") and ref != "refs/heads/default":
            names.add(_strip_heads(ref))
    return sorted(names)


def _manifest_ref(repo_root: Path, branch: str) -> str:
    """The manifest repo ref that `branch` names, or "" if it isn't known
    locally (it may still exist on the server: repo init fetches)."""
    manifests = repo_root / ".repo" / "manifests"
    for ref in (f"refs/remotes/origin/{branch}", f"refs/heads/{branch}", branch):
        try:
            out = _run_git_capture(
                manifests, ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"]
            )
        except Exception:
            return ""
        if out.strip():
            return ref
    return ""


def manifest_files(repo_root: Path, branch: str) -> Optional[list[str]]:
    """The manifest (*.xml) files on `branch` of the manifest repo, or None
    when the branch isn't known locally."""
    ref = _manifest_ref(repo_root, branch)
    if not ref:
        return None
    out = _run_git_capture(
        repo_root / ".repo" / "manifests", ["ls-tree", "-r", "--name-only", ref]
    )
    return sorted(name for name in out.splitlines() if name.endswith(".xml"))


def init_args(branch: str, manifest: str) -> list[str]:
    """argv for `repo init` re-run inside an existing tree: it keeps the
    manifest URL and other init options and only changes branch/file."""
    return ["repo", "init", "-b", branch, "-m", manifest]


async def switch_manifest(
    repo_root: Path,
    branch: str,
    manifest: str,
    on_output: Optional[OutputCallback] = None,
    on_progress: Optional[OutputCallback] = None,
    jobs: Optional[int] = None,
) -> int:
    """`repo init -b <branch> -m <manifest>`, then `repo sync` the whole
    tree onto it. The sync is skipped if init fails."""

    async def say(line: str) -> None:
        if on_output is not None:
            await on_output(line)

    await say("$ " + " ".join(init_args(branch, manifest)))
    returncode = await _stream_subprocess_tty(
        repo_root, init_args(branch, manifest), on_output, on_progress
    )
    if returncode != 0:
        await say(f"error: repo init failed (exit {returncode}); repo sync was not run")
        return returncode
    await say("$ " + " ".join(sync_args(jobs=jobs)))
    return await _stream_subprocess_tty(repo_root, sync_args(jobs=jobs), on_output, on_progress)


def load_projects(repo_root: Path) -> list[Project]:
    """Build the project list with manifest-derived fields (no git status
    yet), local manifests included."""
    manifest = _manifest_projects(repo_root)

    project_list_file = repo_root / ".repo" / "project.list"
    paths: list[str]
    if project_list_file.is_file():
        paths = [
            line.strip()
            for line in project_list_file.read_text().splitlines()
            if line.strip()
        ]
    else:
        paths = list(manifest)

    projects = []
    for path in paths:
        abs_path = repo_root / path
        if not (abs_path / ".git").exists():
            continue
        m = manifest.get(path)
        projects.append(
            Project(
                path=path,
                name=m.name if m else path,
                abs_path=abs_path,
                manifest_revision=m.revision if m else "",
                remote=m.remote if m else "",
                clone_depth=m.clone_depth if m else "",
            )
        )
    return projects


# How long a cancelled command gets to exit after SIGTERM before SIGKILL.
_TERMINATE_GRACE = 5.0


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """Stop a command started with start_new_session (so its pid is also
    its process group): SIGTERM the whole group -- repo's git children
    too -- then SIGKILL whatever is still running after a grace period."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            await asyncio.wait_for(process.wait(), _TERMINATE_GRACE)
            return
        except asyncio.TimeoutError:
            continue


async def _stream_subprocess(
    cwd: Path, args: list[str], on_output: Optional[OutputCallback]
) -> int:
    """Run `args`, streaming its combined output line by line. The command
    never sees the TUI's terminal: stdin is /dev/null and it runs in its own
    session (no controlling tty), and git is told not to prompt, so a
    command that reads input or asks for credentials fails with a message
    instead of stealing keystrokes or drawing over the screen."""
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    assert process.stdout is not None
    try:
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            if on_output is not None:
                await on_output(line.decode(errors="replace").rstrip("\n"))
        return await process.wait()
    except asyncio.CancelledError:  # cancelled from the busy dialog, or quitting
        await _terminate(process)
        raise


async def _stream_subprocess_tty(
    cwd: Path,
    args: list[str],
    on_output: Optional[OutputCallback],
    on_progress: Optional[OutputCallback],
    env: Optional[dict[str, str]] = None,
) -> int:
    """Like _stream_subprocess, but runs the command on a pseudo-terminal.

    repo only prints its live progress line ("Fetching: 45% [4 jobs] (12/27)
    ...", redrawn in place with \r) when stderr is a TTY. Text ending in \n
    goes to on_output; the in-place progress line goes to on_progress.

    Cancelling the awaiting task stops the command's whole process group."""
    master, slave = pty.openpty()
    # Wide enough that repo doesn't elide the progress line; no output
    # post-processing, so newlines arrive as plain \n rather than \r\n.
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 250, 0, 0))
    attrs = termios.tcgetattr(slave)
    attrs[1] &= ~termios.OPOST
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=slave,
            stderr=slave,
            start_new_session=True,
            env=env,
        )
    finally:
        os.close(slave)

    loop = asyncio.get_running_loop()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buf = ""
    # The blocking read runs in a thread that can't be interrupted, so it's
    # shielded from cancellation and master is only closed once it returns.
    read = None
    close_master = True
    try:
        while True:
            read = loop.run_in_executor(None, os.read, master, 4096)
            try:
                chunk = await asyncio.shield(read)
            except OSError:  # EIO: every writer to the pty has exited
                break
            read = None
            if not chunk:
                break
            buf += decoder.decode(chunk)
            latest_progress = ""
            while (match := re.search(r"[\r\n]", buf)) is not None:
                segment = _ANSI_RE.sub("", buf[: match.start()]).rstrip()
                sep, buf = match.group(), buf[match.end() :]
                if not segment:
                    continue
                if sep == "\n":
                    if on_output is not None:
                        await on_output(segment)
                else:  # a progress line, overwritten by the next \r redraw
                    latest_progress = segment
            # Whatever follows the last \r is the progress line being drawn.
            latest_progress = _ANSI_RE.sub("", buf).strip() or latest_progress
            if latest_progress and on_progress is not None:
                await on_progress(latest_progress)
        buf += decoder.decode(b"", final=True)
        if buf.strip() and on_output is not None:
            await on_output(_ANSI_RE.sub("", buf).rstrip())
    except asyncio.CancelledError:  # cancelled from the busy dialog, or quitting
        await _terminate(process)
        if read is not None:
            # Returns (EIO) once nothing holds the pty open. Something that
            # escaped the process group might; then leak the fd rather than
            # close it under a thread still reading it.
            try:
                await asyncio.wait_for(asyncio.shield(read), 2.0)
            except asyncio.TimeoutError:
                close_master = False
            except OSError:
                pass
        raise
    finally:
        if close_master:
            os.close(master)
    return await process.wait()


def sync_args(
    paths: Optional[list[str]] = None,
    jobs: Optional[int] = None,
    detach: bool = False,
    network_only: bool = False,
) -> list[str]:
    """argv for `repo sync`; shared with the UI so it can show the command.

    `detach` is local-only (`-d -l`): going back to the manifest revision
    needs no network fetch, so it doesn't contact the server at all.
    `network_only` (`-n`) fetches but leaves every checkout alone."""
    args = ["repo", "sync", *(["-d", "-l"] if detach else ["--current-branch"])]
    if network_only:
        args.insert(2, "-n")
    if jobs:
        args.append(f"-j{jobs}")
    return args + list(paths or [])


async def sync_projects(
    repo_root: Path,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
    on_progress: Optional[OutputCallback] = None,
    jobs: Optional[int] = None,
) -> int:
    """Run `repo sync [-jN] [paths...]`, streaming output and live progress."""
    return await _stream_subprocess_tty(
        repo_root, sync_args(paths, jobs), on_output, on_progress
    )


async def fetch_projects(
    repo_root: Path,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
    on_progress: Optional[OutputCallback] = None,
    jobs: Optional[int] = None,
) -> int:
    """Run `repo sync -n`: fetch what's new without touching any checkout."""
    return await _stream_subprocess_tty(
        repo_root, sync_args(paths, jobs, network_only=True), on_output, on_progress
    )


async def forall(
    repo_root: Path,
    command: str,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
) -> int:
    """Run `repo forall [paths...] -p -c <command>`, streaming output."""
    return await _stream_subprocess(repo_root, forall_args(command, paths), on_output)


def forall_args(command: str, paths: Optional[list[str]] = None) -> list[str]:
    """argv for `repo forall`: in `paths` (default: every project), with
    -p so each project's output is headed by its name."""
    return ["repo", "forall", *(paths or []), "-p", "-c", command]


async def start_branch(
    repo_root: Path,
    branch_name: str,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
) -> int:
    """Run `repo start <branch> [paths...]`."""
    args = ["repo", "start", branch_name, *(paths or ["."])]
    return await _stream_subprocess(repo_root, args, on_output)


async def checkout_branch(
    repo_root: Path,
    branch_name: str,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
) -> int:
    """Run `repo checkout <branch> [paths...]`: switch to an existing
    local topic branch (one previously created with `repo start`)."""
    args = ["repo", "checkout", branch_name, *(paths or [])]
    return await _stream_subprocess(repo_root, args, on_output)


def abandon_args(branch: Optional[str], paths: Optional[list[str]] = None) -> list[str]:
    """argv for `repo abandon`: one branch, or (branch None) every local
    branch with --all. No paths means every project that has it."""
    return ["repo", "abandon", *(["--all"] if branch is None else [branch]), *(paths or [])]


async def abandon(
    repo_root: Path,
    branch: Optional[str],
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
) -> int:
    """Run `repo abandon`: delete a local branch (`git branch -D`), checking
    out the manifest revision first where it's the current branch."""
    return await _stream_subprocess(repo_root, abandon_args(branch, paths), on_output)


async def sync_detach(
    repo_root: Path,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
    on_progress: Optional[OutputCallback] = None,
    jobs: Optional[int] = None,
) -> int:
    """Run `repo sync -d -l [-jN] [paths...]`: detach back to the manifest-
    pinned revision without fetching, leaving whatever local branch was
    checked out untouched (it is not deleted, just no longer checked out)."""
    return await _stream_subprocess_tty(
        repo_root, sync_args(paths, jobs, detach=True), on_output, on_progress
    )


def unshallow_args(remote: str) -> list[str]:
    return ["git", "fetch", "--unshallow", "--progress", remote]


async def unshallow(
    project: Project,
    remote: str,
    on_output: Optional[OutputCallback] = None,
    on_progress: Optional[OutputCallback] = None,
) -> int:
    """`git fetch --unshallow <remote>` in one project: fetch its full
    history. git's "Receiving objects: 45% (…)" line is the live progress.
    Never prompts (no terminal, GIT_TERMINAL_PROMPT=0): a server that needs
    credentials fails with git's message instead."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return await _stream_subprocess_tty(
        project.abs_path, unshallow_args(remote), on_output, on_progress, env=env
    )


def init_depth(repo_root: Path) -> str:
    """The --depth given to `repo init`, if any ("" otherwise): repo sync
    keeps fetching shallow to that depth."""
    try:
        return _run_git_capture(
            repo_root / ".repo" / "manifests",
            ["config", "--get", "repo.depth"],
        ).strip()
    except Exception:
        return ""


# repo checks its launcher's version on every run (main.py's
# _CheckWrapperVersion) and, when the copy under .repo/repo/ is newer, logs:
#
#   ... A new version of repo (2.54) is available.
#   ... You should upgrade soon:
#       cp /tree/.repo/repo/repo /home/me/bin/repo
#
# (or "!!! ... You must upgrade before you can continue:" with the same cp
# line when the launcher is too old to run at all). When the launcher isn't
# writable it prints "The launcher is not writable" and no cp line, so
# there's nothing to offer.
_NEW_VERSION_RE = re.compile(r"A new version of repo \(([0-9.]+)\) is available")
# repo doesn't quote the paths; the source always ends in .repo/repo/repo,
# which tells us where it stops even if a path has a space in it.
_CP_RE = re.compile(r"^\s*cp (.*?/\.repo/repo/repo) (.+?)\s*$")
_LAUNCHER_VERSION_RE = re.compile(r"^VERSION = \(\s*(\d+)\s*,\s*(\d+)", re.MULTILINE)


@dataclass(frozen=True)
class LauncherUpgrade:
    """repo's suggested `cp <new launcher> <launcher in use>`."""

    version: str  # the new launcher's version, as repo printed it
    source: Path
    dest: Path

    @property
    def command(self) -> str:
        return f"cp {self.source} {self.dest}"


def find_launcher_upgrade(lines: list[str]) -> Optional[LauncherUpgrade]:
    """The launcher upgrade repo asked for in a command's output, if any."""
    version = None
    for line in lines:
        match = _NEW_VERSION_RE.search(line)
        if match:
            version = match.group(1)
            continue
        match = _CP_RE.match(line) if version else None
        if match:
            return LauncherUpgrade(version, Path(match.group(1)), Path(match.group(2)))
    return None


def launcher_version(path: Path) -> Optional[tuple]:
    """The VERSION a repo launcher script declares, e.g. (2, 54)."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    match = _LAUNCHER_VERSION_RE.search(text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def upgrade_launcher(upgrade: LauncherUpgrade) -> None:
    """Do repo's `cp`: overwrite the launcher in place (through a symlink,
    keeping its permissions), as cp onto an existing file does."""
    shutil.copyfile(upgrade.source, upgrade.dest)
