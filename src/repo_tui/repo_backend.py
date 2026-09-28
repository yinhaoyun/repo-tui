"""Interaction with the `repo` tool and the on-disk `.repo/` metadata.

We avoid shelling out to `repo status`/`repo info` for routine refreshes
because they are slow across a large tree and give us less control than
talking to git directly per-project (see git_backend.py). We do use `.repo/`
metadata directly (project list, manifest.xml) to discover the tree's shape,
and we do shell out to `repo` itself for actions the user explicitly asks
for: sync, forall, start, abandon.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import re
import struct
import termios
import xml.etree.ElementTree as ET
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
    manifest_path = repo_root / ".repo" / "manifest.xml"
    return ET.parse(manifest_path).getroot()


def load_tree_info(repo_root: Path) -> TreeInfo:
    info = TreeInfo(root=repo_root)
    manifest_link = repo_root / ".repo" / "manifest.xml"
    if manifest_link.is_symlink():
        info.manifest_name = manifest_link.resolve().name
    info.manifest_branch = _manifest_branch(repo_root)
    return info


def _manifest_branch(repo_root: Path) -> str:
    manifests_git = repo_root / ".repo" / "manifests"
    if manifests_git.is_dir():
        try:
            out = _run_git_capture(manifests_git, ["symbolic-ref", "--short", "HEAD"])
            if out:
                return out.strip()
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


def load_projects(repo_root: Path) -> list[Project]:
    """Build the project list with manifest-derived fields (no git status yet)."""
    revisions_by_path: dict[str, str] = {}
    names_by_path: dict[str, str] = {}
    try:
        root = _read_manifest_xml(repo_root)
        default = root.find("default")
        default_revision = default.get("revision", "") if default is not None else ""
        for proj in root.findall("project"):
            path = proj.get("path") or proj.get("name", "")
            name = proj.get("name", path)
            revision = proj.get("revision", default_revision)
            revisions_by_path[path] = revision
            names_by_path[path] = name
    except (FileNotFoundError, ET.ParseError):
        pass

    project_list_file = repo_root / ".repo" / "project.list"
    paths: list[str]
    if project_list_file.is_file():
        paths = [
            line.strip()
            for line in project_list_file.read_text().splitlines()
            if line.strip()
        ]
    else:
        paths = list(revisions_by_path.keys())

    projects = []
    for path in paths:
        abs_path = repo_root / path
        if not (abs_path / ".git").exists():
            continue
        projects.append(
            Project(
                path=path,
                name=names_by_path.get(path, path),
                abs_path=abs_path,
                manifest_revision=revisions_by_path.get(path, ""),
            )
        )
    return projects


async def _stream_subprocess(
    cwd: Path, args: list[str], on_output: Optional[OutputCallback]
) -> int:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert process.stdout is not None
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        if on_output is not None:
            await on_output(line.decode(errors="replace").rstrip("\n"))
    return await process.wait()


async def _stream_subprocess_tty(
    cwd: Path,
    args: list[str],
    on_output: Optional[OutputCallback],
    on_progress: Optional[OutputCallback],
) -> int:
    """Like _stream_subprocess, but runs the command on a pseudo-terminal.

    repo only prints its live progress line ("Fetching: 45% [4 jobs] (12/27)
    ...", redrawn in place with \r) when stderr is a TTY. Text ending in \n
    goes to on_output; the in-place progress line goes to on_progress."""
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
        )
    finally:
        os.close(slave)

    loop = asyncio.get_running_loop()
    buf = ""
    try:
        while True:
            try:
                chunk = await loop.run_in_executor(None, os.read, master, 4096)
            except OSError:  # EIO: every writer to the pty has exited
                break
            if not chunk:
                break
            buf += chunk.decode(errors="replace")
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
        if buf.strip() and on_output is not None:
            await on_output(_ANSI_RE.sub("", buf).rstrip())
    finally:
        os.close(master)
    return await process.wait()


def sync_args(
    paths: Optional[list[str]] = None, jobs: Optional[int] = None, detach: bool = False
) -> list[str]:
    """argv for `repo sync`; shared with the UI so it can show the command."""
    args = ["repo", "sync", "-d" if detach else "--current-branch"]
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


async def forall(
    repo_root: Path,
    command: str,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
) -> int:
    """Run `repo forall [paths...] -c <command>`, streaming output."""
    args = ["repo", "forall", *(paths or []), "-c", command]
    return await _stream_subprocess(repo_root, args, on_output)


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


async def sync_detach(
    repo_root: Path,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
    on_progress: Optional[OutputCallback] = None,
    jobs: Optional[int] = None,
) -> int:
    """Run `repo sync -d [-jN] [paths...]`: detach back to the manifest-pinned
    revision, leaving whatever local branch was checked out untouched (it
    is not deleted, just no longer checked out)."""
    return await _stream_subprocess_tty(
        repo_root, sync_args(paths, jobs, detach=True), on_output, on_progress
    )
