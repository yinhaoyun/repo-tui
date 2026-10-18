import asyncio
import subprocess
import sys

import pytest

from repo_tui.repo_backend import (
    RepoTreeError,
    _stream_subprocess_tty,
    abandon_args,
    current_manifest_file,
    fetch_manifests,
    find_repo_root,
    manifest_branches,
    manifest_files,
    switch_manifest,
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
    assert sync_args(["a"], detach=True) == ["repo", "sync", "-d", "-l", "a"]
    assert sync_args(jobs=4, network_only=True) == [
        "repo", "sync", "-n", "--current-branch", "-j4"
    ]


def test_abandon_args_one_branch_or_all():
    assert abandon_args("topic", ["a/b"]) == ["repo", "abandon", "topic", "a/b"]
    assert abandon_args("topic") == ["repo", "abandon", "topic"]
    assert abandon_args(None, ["a/b"]) == ["repo", "abandon", "--all", "a/b"]


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


def test_manifest_branch_follows_repos_default_branch_tracking(fake_tree):
    # Real repo checks the manifest out on a local "default" branch that
    # tracks the branch given to `repo init -b`.
    manifests = fake_tree / ".repo" / "manifests"
    _git(manifests, "branch", "-m", "default")
    _git(manifests, "config", "branch.default.merge", "refs/heads/android-15")
    assert load_tree_info(fake_tree).manifest_branch == "android-15"


def test_include_stub_manifest_is_followed(fake_tree):
    # Newer repo writes .repo/manifest.xml as a stub that <include>s the file.
    link = fake_tree / ".repo" / "manifest.xml"
    link.unlink()
    link.write_text('<manifest><include name="default.xml" /></manifest>')
    assert current_manifest_file(fake_tree) == "default.xml"
    by_path = {p.path: p for p in load_projects(fake_tree)}
    assert by_path["frameworks/base"].manifest_revision == "main"


def test_manifest_branches_and_files(fake_tree, tmp_path):
    manifests = fake_tree / ".repo" / "manifests"
    _git(manifests, "checkout", "-q", "-b", "other")
    (manifests / "sub").mkdir()
    (manifests / "sub" / "car.xml").write_text("<manifest/>")
    (manifests / "README").write_text("x")
    _git(manifests, "add", ".")
    _git(manifests, "commit", "-q", "-m", "more")
    _git(manifests, "checkout", "-q", "-b", "default")  # repo's own branch: hidden
    # a remote-tracking branch, as after `git fetch`
    _git(manifests, "update-ref", "refs/remotes/origin/android-15", "other")
    _git(manifests, "update-ref", "refs/remotes/origin/HEAD", "other")

    assert manifest_branches(fake_tree) == ["android-15", "android-16.0.0_r1", "other"]
    assert manifest_files(fake_tree, "android-15") == ["default.xml", "sub/car.xml"]
    assert manifest_files(fake_tree, "android-16.0.0_r1") == ["default.xml"]
    assert manifest_files(fake_tree, "no-such-branch") is None
    # an unreachable server is reported, not raised
    _git(manifests, "remote", "add", "origin", str(tmp_path / "missing.git"))
    assert "missing.git" in fetch_manifests(fake_tree)


_FAKE_REPO_CMD = """#!/bin/sh
echo "ran: $*" >> "$LOG"
[ "$1" = init ] && [ -n "$FAIL_INIT" ] && { echo "fatal: bad branch"; exit 1; }
exit 0
"""


@pytest.mark.parametrize("fail_init", [False, True])
def test_switch_manifest_inits_then_syncs_unless_init_fails(tmp_path, monkeypatch, fail_init):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "repo").write_text(_FAKE_REPO_CMD)
    (bin_dir / "repo").chmod(0o755)
    log = tmp_path / "log"
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("LOG", str(log))
    if fail_init:
        monkeypatch.setenv("FAIL_INIT", "1")
    output = []

    async def on_output(line):
        output.append(line)

    rc = asyncio.run(switch_manifest(tmp_path, "android-15", "car.xml", on_output, jobs=4))
    ran = log.read_text().splitlines()
    if fail_init:
        assert rc == 1
        assert ran == ["ran: init -b android-15 -m car.xml"]
        assert output[-1].startswith("error: repo init failed")
    else:
        assert rc == 0
        assert ran == [
            "ran: init -b android-15 -m car.xml",
            "ran: sync --current-branch -j4",
        ]
