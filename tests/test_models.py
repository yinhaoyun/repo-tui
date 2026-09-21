from pathlib import Path

from repo_tui.models import FileStatus, Project


def make_project(**overrides) -> Project:
    defaults = dict(path="foo/bar", name="platform/foo/bar", abs_path=Path("/tmp/foo/bar"))
    defaults.update(overrides)
    return Project(**defaults)


def test_clean_project_is_not_changed():
    p = make_project(current_branch="main", manifest_revision="main")
    assert not p.is_changed
    assert p.stat_summary == "clean"


def test_modified_file_marks_changed_and_is_filtered_when_ignored():
    p = make_project()
    p.files = [FileStatus(path="a.pyc", index_status="?", worktree_status="?", ignored=True)]
    assert not p.has_visible_changes
    assert not p.is_changed

    p.files.append(FileStatus(path="real.py", index_status=" ", worktree_status="M"))
    assert p.has_visible_changes
    assert p.is_changed


def test_off_manifest_branch_detection():
    p = make_project(current_branch="topic", manifest_revision="refs/heads/main")
    assert p.off_manifest_branch
    assert p.is_changed

    p2 = make_project(current_branch="main", manifest_revision="refs/heads/main")
    assert not p2.off_manifest_branch


def test_clean_detached_head_is_not_changed():
    # After a fresh `repo sync`, untouched projects normally sit in detached
    # HEAD at the manifest revision -- that's the expected steady state, not
    # something the user needs to look at.
    p = make_project(is_detached=True)
    assert not p.is_changed
    assert p.branch_summary == "(detached)"


def test_detached_head_with_real_changes_is_changed():
    p = make_project(is_detached=True)
    p.files = [FileStatus(path="real.py", index_status=" ", worktree_status="M")]
    assert p.is_changed


def test_ahead_behind_summary():
    p = make_project(current_branch="main", ahead=2, behind=1)
    assert p.is_changed
    assert "↑2" in p.branch_summary
    assert "↓1" in p.branch_summary
