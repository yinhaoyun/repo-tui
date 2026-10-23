import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from repo_tui import repo_backend

from repo_tui.repo_backend import (
    RepoTreeError,
    _stream_subprocess,
    _stream_subprocess_tty,
    abandon_args,
    forall_args,
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


def test_forall_args_head_each_projects_output():
    assert forall_args("git log -1") == ["repo", "forall", "-p", "-c", "git log -1"]
    assert forall_args("pwd", ["a", "b"]) == ["repo", "forall", "a", "b", "-p", "-c", "pwd"]


def _launcher(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env python3\nVERSION = {version}\nKEYRING_VERSION = (2, 3)\n")
    path.chmod(0o755)
    return path


def test_find_launcher_upgrade_reads_repos_cp_suggestion():
    # As main.py's _CheckWrapperVersion logs it (a path with a space, too).
    lines = [
        "",
        "... A new version of repo (2.54) is available.",
        "... You should upgrade soon:",
        "    cp /src/my tree/.repo/repo/repo /home/me/.bin/repo",
        "Fetching: 100% (2/2), done",
    ]
    upgrade = repo_backend.find_launcher_upgrade(lines)
    assert upgrade == repo_backend.LauncherUpgrade(
        "2.54", Path("/src/my tree/.repo/repo/repo"), Path("/home/me/.bin/repo")
    )
    assert upgrade.command == "cp /src/my tree/.repo/repo/repo /home/me/.bin/repo"


def test_find_launcher_upgrade_handles_the_too_old_error():
    lines = [
        "repo: error:",
        "!!! Your version of repo 1.12 is too old.",
        "!!! We need at least version 1.15.",
        "!!! A new version of repo (2.54) is available.",
        "!!! You must upgrade before you can continue:",
        "",
        "    cp /t/.repo/repo/repo /usr/local/bin/repo",
    ]
    upgrade = repo_backend.find_launcher_upgrade(lines)
    assert upgrade is not None and upgrade.dest == Path("/usr/local/bin/repo")


def test_find_launcher_upgrade_ignores_unwritable_launcher_and_stray_cp():
    unwritable = [
        "... A new version of repo (2.54) is available.",
        "... New version is available at: /t/.repo/repo/repo",
        "... The launcher is run from: /usr/bin/repo",
        "!!! The launcher is not writable.  Please talk to your sysadmin or distro",
    ]
    assert repo_backend.find_launcher_upgrade(unwritable) is None
    # A cp line with no "new version" notice before it is just output.
    assert repo_backend.find_launcher_upgrade(["cp /t/.repo/repo/repo /x"]) is None


def test_upgrade_launcher_copies_over_the_launcher_in_place(tmp_path):
    source = _launcher(tmp_path / "tree" / ".repo" / "repo" / "repo", "(2, 54)")
    dest = _launcher(tmp_path / "bin" / "repo", "(2, 36)")
    assert repo_backend.launcher_version(dest) == (2, 36)

    repo_backend.upgrade_launcher(repo_backend.LauncherUpgrade("2.54", source, dest))

    assert repo_backend.launcher_version(dest) == (2, 54)
    assert dest.stat().st_mode & 0o777 == 0o755
    assert repo_backend.launcher_version(tmp_path / "missing") is None


_PROBE = r"""
import os, sys
print("stdin:", repr(sys.stdin.read()))
print("prompt:", os.environ.get("GIT_TERMINAL_PROMPT"))
try:
    open("/dev/tty").close()
    print("tty: open")
except OSError:
    print("tty: none")
"""


def test_stream_subprocess_keeps_the_command_off_the_terminal(tmp_path):
    output = []

    async def on_output(line):
        output.append(line)

    rc = asyncio.run(_stream_subprocess(tmp_path, [sys.executable, "-c", _PROBE], on_output))
    assert rc == 0
    assert output == ["stdin: ''", "prompt: 0", "tty: none"]


_SPLIT_UTF8 = r"""
import os, sys, time
os.write(1, "中".encode()[:2]); time.sleep(0.3)
os.write(1, "中".encode()[2:] + "文\n".encode())
"""


def test_stream_subprocess_tty_keeps_multibyte_chars_split_across_reads(tmp_path):
    output = []

    async def on_output(line):
        output.append(line)

    rc = asyncio.run(
        _stream_subprocess_tty(tmp_path, [sys.executable, "-c", _SPLIT_UTF8], on_output, None)
    )
    assert rc == 0
    assert output == ["中文"]


# Starts a grandchild in the same process group, says both pids, then
# waits; cancelling must stop both.
_TREE_OF_PROCESSES = r"""
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print("pids", child.pid, flush=True)
time.sleep(60)
"""


def _alive(pid):
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie still answers kill(0); it has exited all the same
    with open(f"/proc/{pid}/stat") as f:
        return f.read().split(") ", 1)[1][0] != "Z"


@pytest.mark.parametrize("tty", [False, True])
def test_cancelling_a_command_stops_its_whole_process_group(tmp_path, tty):
    async def scenario():
        started = asyncio.Event()
        pids = []

        async def on_output(line):
            if line.startswith("pids "):
                pids.append(int(line.split()[1]))
                started.set()

        argv = [sys.executable, "-c", _TREE_OF_PROCESSES]
        if tty:
            task = asyncio.ensure_future(_stream_subprocess_tty(tmp_path, argv, on_output, None))
        else:
            task = asyncio.ensure_future(_stream_subprocess(tmp_path, argv, on_output))
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        return pids[0]

    grandchild = asyncio.run(scenario())
    for _ in range(50):
        if not _alive(grandchild):
            break
        import time
        time.sleep(0.1)
    assert not _alive(grandchild)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_local_manifests_add_extend_and_remove_projects(fake_tree):
    repo_dir = fake_tree / ".repo"
    _write(repo_dir / "manifests" / "default.xml", """<?xml version="1.0"?>
<manifest>
  <remote name="aosp" fetch="https://example.invalid/" revision="refs/heads/aosp-main"/>
  <remote name="vendor" fetch="https://vendor.invalid/" revision="refs/heads/vendor-main"/>
  <default remote="aosp"/>
  <include name="extra.xml"/>
  <project path="frameworks/base" name="platform/frameworks/base"/>
  <remove-project name="platform/extra"/>
</manifest>
""")
    _write(repo_dir / "manifests" / "extra.xml", """<?xml version="1.0"?>
<manifest>
  <project path="extra" name="platform/extra"/>
  <project path="build/make" name="platform/build" revision="main"/>
</manifest>
""")
    _write(repo_dir / "local_manifests" / "10-mine.xml", """<?xml version="1.0"?>
<manifest>
  <remove-project name="platform/frameworks/base"/>
  <project path="frameworks/base" name="vendor/base" remote="vendor"/>
  <extend-project name="platform/build" revision="refs/heads/topic"/>
  <project path="vendor/tools" name="vendor/tools" remote="vendor" revision="v1"/>
</manifest>
""")
    for path in ("extra", "vendor/tools"):
        (fake_tree / path).mkdir(parents=True)
        _git(fake_tree / path, "init", "-q")
    (repo_dir / "project.list").unlink()  # paths come from the manifests

    projects = {p.path: p for p in repo_backend.load_projects(fake_tree)}
    # the main manifest's remove-project drops what the earlier include added
    assert set(projects) == {"build/make", "frameworks/base", "vendor/tools"}
    assert projects["build/make"].manifest_revision == "refs/heads/topic"
    base = projects["frameworks/base"]
    # a project without a revision takes its remote's
    assert (base.name, base.remote, base.manifest_revision) == (
        "vendor/base", "vendor", "refs/heads/vendor-main"
    )
    assert projects["vendor/tools"].manifest_revision == "v1"


def test_project_without_revision_uses_its_remotes_then_the_default(fake_tree):
    _write(fake_tree / ".repo" / "manifests" / "default.xml", """<?xml version="1.0"?>
<manifest>
  <remote name="aosp" fetch="https://example.invalid/"/>
  <remote name="other" fetch="https://other.invalid/" revision="other-main"/>
  <default remote="aosp" revision="main"/>
  <project path="build/make" name="platform/build"/>
  <project path="frameworks/base" name="platform/frameworks/base" remote="other"/>
</manifest>
""")
    projects = {p.path: p for p in repo_backend.load_projects(fake_tree)}
    assert projects["build/make"].manifest_revision == "main"
    assert projects["frameworks/base"].manifest_revision == "other-main"
