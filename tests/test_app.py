import asyncio
import subprocess

import pytest

from repo_tui.app import RepoTuiApp
from repo_tui.widgets.detail_pane import FileTable
from repo_tui.widgets.diff_screen import DiffScreen
from repo_tui.widgets.help_modal import HelpModal
from repo_tui.widgets.project_list import ProjectList

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "PATH": "/usr/bin:/bin",
}


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=_GIT_ENV)


@pytest.fixture
def dirty_tree(tmp_path):
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
  <project path="frameworks/base" name="platform/frameworks/base" revision="main"/>
</manifest>
"""
    )
    _git(manifests, "add", "default.xml")
    _git(manifests, "commit", "-q", "-m", "manifest")
    (root / ".repo" / "manifest.xml").symlink_to(manifests / "default.xml")
    (root / ".repo" / "project.list").write_text("build/make\nframeworks/base\n")

    for path in ("build/make", "frameworks/base"):
        proj_dir = root / path
        proj_dir.mkdir(parents=True)
        _git(proj_dir, "init", "-q")
        (proj_dir / "README.md").write_text("hello\n")
        _git(proj_dir, "add", "README.md")
        _git(proj_dir, "commit", "-q", "-m", "init")
        _git(proj_dir, "branch", "-m", "main")  # match manifest revision="main"

    base = root / "frameworks/base"
    (base / "README.md").write_text("hello\nmore\n")
    (base / "generated.pyc").write_text("noise\n")
    (base / "real_change.c").write_text("int x;\n")
    _git(base, "add", "real_change.c")

    return root


def test_hides_clean_projects_by_default_and_filters_ignored_noise(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()

            project_list = app.query_one("#project-list", ProjectList)
            # build/make is clean -> hidden; frameworks/base is dirty -> shown
            assert project_list.row_count == 1
            project = project_list.project_at_cursor()
            assert project.path == "frameworks/base"
            assert project.stat_summary == "+2/-0"  # generated.pyc excluded

            await pilot.press("a")
            await pilot.pause()
            assert project_list.row_count == 2

    asyncio.run(scenario())


def test_enter_on_file_row_opens_diff_screen(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()

            await pilot.press("tab")
            await pilot.pause()
            file_table = app.query_one("#file-table", FileTable)
            for i in range(file_table.row_count):
                file_table.move_cursor(row=i)
                if file_table.file_at_cursor().path == "real_change.c":
                    break

            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert isinstance(app.screen_stack[-1], DiffScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], DiffScreen)

    asyncio.run(scenario())


def test_help_modal_closes_on_q_without_quitting_app(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()

            await pilot.press("?")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], HelpModal)

            await pilot.press("q")
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], HelpModal)

            # app must still be alive and responsive to further bindings
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert app.leader_active is True
            await pilot.press("escape")
            await pilot.pause()
            assert app.leader_active is False

    asyncio.run(scenario())


def test_filter_narrows_visible_projects(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()

            await pilot.press("a")  # show all projects first
            await pilot.pause()
            project_list = app.query_one("#project-list", ProjectList)
            assert project_list.row_count == 2

            await pilot.press("slash")
            await pilot.pause()
            for ch in "build":
                await pilot.press(ch)
            await pilot.pause()
            assert project_list.row_count == 1
            assert project_list.project_at_cursor().path == "build/make"

    asyncio.run(scenario())
