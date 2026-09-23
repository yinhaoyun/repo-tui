import subprocess
from pathlib import Path

import pytest

from repo_tui import self_update

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": "/usr/bin:/bin",
}


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=_GIT_ENV)


def _write_package_files(root: Path) -> None:
    (root / "pyproject.toml").write_text('[project]\nname = "repo-tui"\nversion = "0.1.0"\n')
    pkg = root / "src" / "repo_tui"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")


@pytest.fixture
def clone_pair(tmp_path, monkeypatch):
    """An `origin` repo and a `clone` of it, wired so self_update thinks the
    currently-running repo_tui package lives inside `clone`."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    _write_package_files(origin)
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "initial")
    _git(origin, "branch", "-m", "main")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))

    monkeypatch.setattr(self_update.repo_tui, "__file__", str(clone / "src" / "repo_tui" / "__init__.py"))
    return origin, clone


def test_finds_git_checkout_root(clone_pair):
    origin, clone = clone_pair
    assert self_update._find_git_checkout_root() == clone


def test_no_op_when_already_up_to_date(clone_pair, capsys):
    result = self_update.run_update()
    assert result == 0
    assert "already up to date" in capsys.readouterr().out


def test_refuses_dirty_checkout(clone_pair, capsys):
    origin, clone = clone_pair
    (clone / "src" / "repo_tui" / "__init__.py").write_text("# local edit\n")
    result = self_update.run_update()
    assert result == 1
    assert "refusing to touch it" in capsys.readouterr().err


def test_pulls_new_commits_and_reinstalls(clone_pair, capsys, monkeypatch):
    origin, clone = clone_pair
    (origin / "NEW_FILE.txt").write_text("hello\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "add a file")

    calls = []
    monkeypatch.setattr(
        self_update,
        "_pip_install_editable",
        lambda root: calls.append(root) or subprocess.CompletedProcess([], 0, "", ""),
    )

    result = self_update.run_update()
    assert result == 0
    assert (clone / "NEW_FILE.txt").is_file()
    assert calls == [clone]
    assert "add a file" in capsys.readouterr().out


def test_reports_pip_failure(clone_pair, monkeypatch, capsys):
    origin, clone = clone_pair
    (origin / "NEW_FILE.txt").write_text("hello\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "add a file")

    monkeypatch.setattr(
        self_update,
        "_pip_install_editable",
        lambda root: subprocess.CompletedProcess([], 1, "", "boom"),
    )

    result = self_update.run_update()
    assert result == 1
    assert "pip install failed" in capsys.readouterr().err


def test_no_git_checkout_found(tmp_path, monkeypatch):
    lonely = tmp_path / "no_git_here" / "src" / "repo_tui" / "__init__.py"
    monkeypatch.setattr(self_update.repo_tui, "__file__", str(lonely))
    result = self_update.run_update()
    assert result == 1
