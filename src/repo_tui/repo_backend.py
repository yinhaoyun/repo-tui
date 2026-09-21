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
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Awaitable, Callable, Optional

from .models import Project, TreeInfo

OutputCallback = Callable[[str], Awaitable[None]]


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


async def sync_projects(
    repo_root: Path,
    paths: Optional[list[str]] = None,
    on_output: Optional[OutputCallback] = None,
) -> int:
    """Run `repo sync [paths...]`, streaming output line-by-line."""
    args = ["repo", "sync", "--current-branch"]
    if paths:
        args.extend(paths)
    return await _stream_subprocess(repo_root, args, on_output)


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
