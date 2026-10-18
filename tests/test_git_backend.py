import os
import subprocess

from repo_tui import git_backend
from repo_tui.git_backend import _parse_numstat, _parse_status_v2, collect_all
from repo_tui.ignore import IgnoreRules
from repo_tui.models import Project


def test_parse_numstat_basic():
    out = "3\t1\tfoo.py\n0\t0\tbar.txt\n"
    assert _parse_numstat(out) == {"foo.py": (3, 1), "bar.txt": (0, 0)}


def test_parse_numstat_binary_file():
    out = "-\t-\timage.png\n"
    assert _parse_numstat(out) == {"image.png": (0, 0)}


def test_parse_status_v2_branch_and_ahead_behind():
    out = "\n".join(
        [
            "# branch.oid abcdef",
            "# branch.head main",
            "# branch.upstream origin/main",
            "# branch.ab +2 -1",
        ]
    )
    branch, detached, ahead, behind, files = _parse_status_v2(out)
    assert branch == "main"
    assert not detached
    assert ahead == 2
    assert behind == 1
    assert files == []


def test_parse_status_v2_detached():
    out = "# branch.head (detached)"
    branch, detached, ahead, behind, files = _parse_status_v2(out)
    assert detached
    assert branch == ""


def test_parse_status_v2_files():
    out = "\n".join(
        [
            "# branch.head main",
            "1 M. N... 100644 100644 100644 aaaa bbbb README.md",
            "1 .M N... 100644 100644 100644 aaaa bbbb tracked.py",
            "? untracked.txt",
        ]
    )
    _, _, _, _, files = _parse_status_v2(out)
    codes = {f.path: f.code for f in files}
    assert codes["README.md"] == "M."
    assert codes["tracked.py"] == ".M"
    assert codes["untracked.txt"] == "??"
    untracked = next(f for f in files if f.path == "untracked.txt")
    assert untracked.is_untracked


def test_parse_status_v2_rename():
    out = "\n".join(
        [
            "# branch.head main",
            "2 R. N... 100644 100644 100644 aaaa bbbb R100 new_name.py\told_name.py",
        ]
    )
    _, _, _, _, files = _parse_status_v2(out)
    assert files[0].path == "new_name.py"


def test_collect_all_reports_progress_per_project(tmp_path, monkeypatch):
    monkeypatch.setattr(git_backend, "collect_project_status", lambda p, ignore: p)
    projects = [Project(path=f"p{i}", name=f"p{i}", abs_path=tmp_path) for i in range(5)]
    calls = []
    collect_all(projects, IgnoreRules(), max_workers=3,
                on_progress=lambda done, total, p: calls.append((done, total)))
    assert sorted(calls) == [(n, 5) for n in range(1, 6)]


def test_parse_status_v2_rename_keeps_orig_path():
    out = "2 R. N... 100644 100644 100644 abc abc R100 new.txt\told.txt"
    *_, files = _parse_status_v2(out)
    assert (files[0].path, files[0].orig_path) == ("new.txt", "old.txt")


def test_discard_changes_restores_renames_and_staged_files(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, env=env)

    git("init", "-q")
    (tmp_path / "old.txt").write_text("x\n")
    (tmp_path / "keep.txt").write_text("k\n")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    git("mv", "old.txt", "new.txt")
    (tmp_path / "keep.txt").write_text("changed\n")
    (tmp_path / "staged_new.txt").write_text("n\n")
    git("add", "staged_new.txt")
    (tmp_path / "junk.txt").write_text("j\n")

    project = Project(path="p", name="p", abs_path=tmp_path)
    git_backend.collect_project_status(project, IgnoreRules(patterns=[]))
    assert project.is_changed

    rc, _ = git_backend.discard_changes(project)
    assert rc == 0
    git_backend.collect_project_status(project, IgnoreRules(patterns=[]))
    assert project.files == []
    assert (tmp_path / "old.txt").exists() and not (tmp_path / "new.txt").exists()
    assert (tmp_path / "keep.txt").read_text() == "k\n"



def test_head_changes_counts_new_and_dropped_commits(tmp_path):
    import subprocess

    from repo_tui import git_backend
    from repo_tui.models import Project

    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, env=env)

    def commit(subject):
        (tmp_path / "f").write_text(subject)
        git("add", "f")
        git("commit", "-q", "-m", subject)

    git("init", "-q")
    commit("base")
    project = Project(path="p", name="p", abs_path=tmp_path)
    base = git_backend.head_commits([project])
    commit("topic 1")
    commit("topic 2")
    topic = git_backend.head_commits([project])

    # moving forward: 2 commits came in
    [forward] = git_backend.head_changes([project], base, topic)
    assert (forward.added, forward.dropped) == (2, 0)
    text = git_backend.describe_head_change(project, forward)
    assert "New commits (2)" in text and "topic 2" in text

    # detaching back to base: nothing new, 2 no longer on HEAD
    [back] = git_backend.head_changes([project], topic, base)
    assert (back.added, back.dropped) == (0, 2)
    assert "No longer on HEAD (2)" in git_backend.describe_head_change(project, back)

    assert git_backend.head_changes([project], base, base) == []


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def _file_repo(tmp_path):
    from repo_tui.models import Project

    root = tmp_path / "p"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("one\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "first")
    (root / "a.txt").write_text("one\ntwo\n")
    return Project(path="p", name="p", abs_path=root)


def test_file_diff_log_stage_unstage(tmp_path):
    from repo_tui.models import FileStatus

    project = _file_repo(tmp_path)
    tracked = FileStatus("a.txt", " ", "M")
    assert "+two" in git_backend.read_file_diff(project, tracked)

    assert git_backend.stage_file(project, tracked)[0] == 0
    staged = FileStatus("a.txt", "M", " ")
    # a staged-only change still shows in the diff view
    assert "+two" in git_backend.read_file_diff(project, staged)
    assert git_backend.unstage_file(project, staged)[0] == 0
    out = subprocess.run(["git", "status", "--porcelain"], cwd=project.abs_path,
                         capture_output=True, text=True).stdout
    assert out == " M a.txt\n"

    (project.abs_path / "new.txt").write_text("hello\n")
    untracked = FileStatus("new.txt", "?", "?")
    assert "+hello" in git_backend.read_file_diff(project, untracked)

    log = git_backend.file_log(project, "a.txt")
    assert "first" in log and len(log.splitlines()) == 1
    assert "one" in git_backend.file_blame(project, "a.txt")

    rc, _ = git_backend.discard_files(project, [tracked, untracked])
    assert rc == 0
    assert (project.abs_path / "a.txt").read_text() == "one\n"
    assert not (project.abs_path / "new.txt").exists()


def test_failed_file_op_returns_gits_message(tmp_path):
    from repo_tui.models import FileStatus

    project = _file_repo(tmp_path)
    rc, lines = git_backend.stage_file(project, FileStatus("missing.txt", "?", "?"))
    assert rc != 0 and any("missing.txt" in line for line in lines)


def _shallow_clone(tmp_path, dest):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    for i in range(3):
        (origin / "f.txt").write_text(f"{i}\n")
        _git(origin, "add", "f.txt")
        _git(origin, "commit", "-q", "-m", f"c{i}")
    _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{origin}", str(dest))
    return dest


def test_shallow_clone_is_detected_and_unshallowed(tmp_path):
    import asyncio

    from repo_tui import repo_backend
    from repo_tui.ignore import IgnoreRules

    dest = _shallow_clone(tmp_path, tmp_path / "proj")
    project = Project(path="proj", name="proj", abs_path=dest, remote="origin")
    git_backend.collect_project_status(project, IgnoreRules())
    assert project.is_shallow
    assert git_backend.fetch_remote(project) == ("origin", "")

    rc = asyncio.run(repo_backend.unshallow(project, "origin"))
    assert rc == 0
    git_backend.collect_project_status(project, IgnoreRules())
    assert not project.is_shallow
    assert len(git_backend.file_log(project, "f.txt").splitlines()) == 3


def test_fetch_remote_explains_when_there_is_none(tmp_path):
    project = _file_repo(tmp_path)  # no remotes
    remote, why = git_backend.fetch_remote(project)
    assert remote == "" and "no git remote" in why
    _git(project.abs_path, "remote", "add", "a", "x")
    _git(project.abs_path, "remote", "add", "b", "y")
    project.remote = "aosp"
    remote, why = git_backend.fetch_remote(project)
    assert remote == "" and "several remotes (a, b)" in why


def test_branch_name_problem_uses_gits_rules():
    assert git_backend.branch_name_problem("topic/ok") == ""
    for bad in ["-x", "a b", "a..b", "a~1", "x.lock", "@{-1}", "HEAD"]:
        assert git_backend.branch_name_problem(bad), bad


def test_unpushed_commits_against_the_upstream(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "base")
    git("branch", "topic")
    project = Project(path="p", name="p", abs_path=tmp_path)
    assert git_backend.unpushed_commits(project, "topic") is None  # no upstream

    git("branch", "-q", "--set-upstream-to=main", "topic")
    assert git_backend.unpushed_commits(project, "topic") == (0, [])

    for subject in ("one", "two", "three"):
        sha = git("commit-tree", "HEAD^{tree}", "-p", "topic", "-m", subject)
        git("branch", "-f", "topic", sha)
    count, shown = git_backend.unpushed_commits(project, "topic", limit=2)
    assert count == 3
    assert [line.split(" ", 1)[1] for line in shown] == ["three", "two"]
