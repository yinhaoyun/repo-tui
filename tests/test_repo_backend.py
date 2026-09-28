import asyncio
import subprocess
import sys

import pytest

from repo_tui.repo_backend import (
    RepoTreeError,
    _stream_subprocess_tty,
    find_repo_root,
    load_projects,
    load_tree_info,
    sync_args,
)


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"},
    )


@pytest.fixture
def fake_tree(tmp_path):
    root = tmp_path / "tree"
    manifests = root / ".repo" / "manifests"
    manifests.mkdir(parents=True)
    _git(manifests, "init", "-q")
    (manifests / "default.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <remote name="aosp" fetch="https://example.invalid/"/>
  <default remote="aosp" revision="main"/>
  <project path="build/make" name="platform/build" revision="main"/>
  <project path="frameworks/base" name="platform/frameworks/base"/>
</manifest>
"""
    )
    _git(manifests, "add", "default.xml")
    _git(manifests, "commit", "-q", "-m", "manifest")
    _git(manifests, "branch", "-m", "android-16.0.0_r1")
    (root / ".repo" / "manifest.xml").symlink_to(manifests / "default.xml")

    for path in ("build/make", "frameworks/base"):
        proj_dir = root / path
        proj_dir.mkdir(parents=True)
        _git(proj_dir, "init", "-q")

    (root / ".repo" / "project.list").write_text("build/make\nframeworks/base\n")
    return root


def test_find_repo_root_walks_up(fake_tree):
    nested = fake_tree / "frameworks" / "base"
    assert find_repo_root(nested) == fake_tree


def test_find_repo_root_raises_outside_tree(tmp_path):
    with pytest.raises(RepoTreeError):
        find_repo_root(tmp_path)


def test_load_tree_info_reads_manifest_branch(fake_tree):
    info = load_tree_info(fake_tree)
    assert info.manifest_branch == "android-16.0.0_r1"
    assert info.manifest_name == "default.xml"


def test_load_projects_uses_manifest_revisions(fake_tree):
    projects = load_projects(fake_tree)
    by_path = {p.path: p for p in projects}
    assert by_path["build/make"].manifest_revision == "main"
    # frameworks/base has no explicit revision -> falls back to <default revision="main">
    assert by_path["frameworks/base"].manifest_revision == "main"
    assert by_path["frameworks/base"].name == "platform/frameworks/base"


def test_sync_args_adds_jobs_and_paths():
    assert sync_args(["a/b"], 8) == ["repo", "sync", "--current-branch", "-j8", "a/b"]
    assert sync_args(jobs=None) == ["repo", "sync", "--current-branch"]
    assert sync_args(["a"], 4, detach=True) == ["repo", "sync", "-d", "-j4", "a"]


# Mimics repo's progress.py: a \r-redrawn progress line (only when stderr is
# a TTY), a message printed above it, then the final "done" line.
_FAKE_REPO = r"""
import sys
e = sys.stderr
print("tty:", e.isatty(), flush=True)
for i in (1, 2):
    e.write(f"\rFetching: {i * 50}% [4 jobs] ({i}/2) 0:0{i} | p{i}\x1b[K"); e.flush()
e.write("\r\x1b[2Kwarning: noisy\n"); e.flush()
e.write("\rFetching: 100% (2/2), done in 0.1s\x1b[K\n"); e.flush()
"""


def test_stream_subprocess_tty_splits_progress_from_output(tmp_path):
    output, progress = [], []

    async def on_output(line):
        output.append(line)

    async def on_progress(line):
        progress.append(line)

    rc = asyncio.run(
        _stream_subprocess_tty(
            tmp_path, [sys.executable, "-c", _FAKE_REPO], on_output, on_progress
        )
    )
    assert rc == 0
    assert output == ["tty: True", "warning: noisy", "Fetching: 100% (2/2), done in 0.1s"]
    # redraws that arrive in one read collapse to the newest one
    assert progress[-1] == "Fetching: 100% [4 jobs] (2/2) 0:02 | p2"
    assert all(line.startswith("Fetching: ") for line in progress)
