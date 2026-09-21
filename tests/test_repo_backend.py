import subprocess

import pytest

from repo_tui.repo_backend import RepoTreeError, find_repo_root, load_projects, load_tree_info


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
