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
