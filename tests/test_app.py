import asyncio
import subprocess
import threading

import pytest

from repo_tui import git_backend, repo_backend
from repo_tui.app import RepoTuiApp
from repo_tui.widgets.action_panel import ActionPanel
from repo_tui.widgets.busy_modal import BusyModal
from repo_tui.widgets.confirm_modal import ConfirmModal
from repo_tui.widgets.context_menu import ContextMenu
from repo_tui.widgets.detail_pane import FileTable
from repo_tui.widgets.diff_screen import DiffScreen
from repo_tui.widgets.help_modal import HelpModal
from repo_tui.widgets.project_list import ProjectList
from repo_tui.widgets.prompt_modal import PromptModal

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

    # build/make looks freshly synced: detached HEAD, no local branches.
    make = root / "build/make"
    _git(make, "checkout", "-q", "--detach")
    _git(make, "branch", "-q", "-D", "main")

    base = root / "frameworks/base"
    (base / "README.md").write_text("hello\nmore\n")
    (base / "generated.pyc").write_text("noise\n")
    (base / "real_change.c").write_text("int x;\n")
    _git(base, "add", "real_change.c")
    _git(base, "branch", "topic-b")  # gives frameworks/base a 2nd local branch

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
            await pilot.press("space")
            await pilot.pause()
            assert app.action_mode_active is True
            await pilot.press("escape")
            await pilot.pause()
            assert app.action_mode_active is False

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


def _select_project(project_list, path):
    for i in range(project_list.row_count):
        project_list.move_cursor(row=i)
        if project_list.project_at_cursor().path == path:
            return
    raise AssertionError(f"project {path!r} not found in list")


def test_right_click_opens_context_menu_for_clicked_project(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()

            project_list = app.query_one("#project-list", ProjectList)
            # row 1 (y offset 2, after the header) is the second row
            await pilot.click(ProjectList, offset=(5, 2), button=3)
            await pilot.pause()

            assert isinstance(app.screen_stack[-1], ContextMenu)
            assert project_list.project_at_cursor() is not None
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], ContextMenu)

    asyncio.run(scenario())


def test_switch_branch_shows_picker_when_multiple_local_branches(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_checkout(repo_root, branch, paths=None, on_output=None):
            calls.append((branch, paths))
            return 0

        monkeypatch.setattr(repo_backend, "checkout_branch", fake_checkout)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()

            project_list = app.query_one("#project-list", ProjectList)
            _select_project(project_list, "frameworks/base")
            await pilot.pause()

            await pilot.press("B")
            await pilot.pause()

            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            option_list = menu.query_one("OptionList")
            ids = [o.id for o in option_list._options]
            assert ids == ["topic-b"]  # excludes the current branch ("main")

            option_list.highlighted = 0
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert calls == [("topic-b", ["frameworks/base"])]

    asyncio.run(scenario())


def test_switch_branch_falls_back_to_prompt_when_no_other_branches(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()

            project_list = app.query_one("#project-list", ProjectList)
            _select_project(project_list, "build/make")  # detached, no local branches
            await pilot.pause()

            await pilot.press("B")
            await pilot.pause()

            assert isinstance(app.screen_stack[-1], PromptModal)

    asyncio.run(scenario())


def test_action_panel_detach_calls_repo_sync_dash_d(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_sync_detach(repo_root, paths=None, on_output=None, **kwargs):
            calls.append(paths)
            return 0

        monkeypatch.setattr(repo_backend, "sync_detach", fake_sync_detach)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()

            project_list = app.query_one("#project-list", ProjectList)
            _select_project(project_list, "frameworks/base")
            await pilot.pause()

            await pilot.press("space")
            await pilot.press("d")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert calls == [["frameworks/base"]]
            assert isinstance(app.screen_stack[-1], DiffScreen)

    asyncio.run(scenario())


def test_context_menu_detach_option_calls_repo_sync_dash_d(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_sync_detach(repo_root, paths=None, on_output=None, **kwargs):
            calls.append(paths)
            return 0

        monkeypatch.setattr(repo_backend, "sync_detach", fake_sync_detach)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()

            project_list = app.query_one("#project-list", ProjectList)
            _select_project(project_list, "frameworks/base")
            await pilot.pause()

            await pilot.click(ProjectList, offset=(5, 1), button=3)
            await pilot.pause()
            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            option_list = menu.query_one("OptionList")
            ids = [o.id for o in option_list._options]
            detach_index = ids.index("detach")
            option_list.highlighted = detach_index
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert calls == [["frameworks/base"]]

    asyncio.run(scenario())


def test_action_panel_has_no_timeout_and_shows_target(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()
            _select_project(app.query_one("#project-list", ProjectList), "build/make")
            await pilot.pause()

            await pilot.press("space")
            await asyncio.sleep(3.5)  # the old leader timed out after 3s
            await pilot.pause()
            panel = app.screen_stack[-1]
            assert isinstance(panel, ActionPanel)
            assert "build/make" in panel.query_one("#action-box").border_title

            await pilot.press("space")  # space again exits
            await pilot.pause()
            assert app.action_mode_active is False

    asyncio.run(scenario())


def test_immediate_actions_need_action_panel(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_sync_detach(repo_root, paths=None, on_output=None, **kwargs):
            calls.append(paths)
            return 0

        monkeypatch.setattr(repo_backend, "sync_detach", fake_sync_detach)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("d")  # bare d: not bound on the top layer
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == []

            await pilot.press("space")
            await pilot.press("z")  # not an action key: exits, does nothing
            await pilot.pause()
            assert app.action_mode_active is False
            assert len(app.screen_stack) == 1

    asyncio.run(scenario())


def test_app_keys_are_ignored_while_a_modal_is_open(dirty_tree):
    # Top-level action keys (b/B/c/f) must not fire behind a popup; Textual
    # stops app bindings at modal screens, this guards that assumption.
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()
            _select_project(app.query_one("#project-list", ProjectList), "build/make")
            await pilot.pause()

            await pilot.press("B")  # build/make has no other branch -> prompt
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], PromptModal)

            show_all = app.show_all
            await pilot.press("escape")
            await pilot.pause()
            app._open_context_menu(app._selected_project())
            await pilot.pause()
            await pilot.press("a")
            await pilot.press("f")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ContextMenu)
            assert app.show_all == show_all

    asyncio.run(scenario())


def test_busy_modal_blocks_keys_while_sync_runs(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        release = asyncio.Event()

        seen = {}

        async def slow_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            seen["jobs"] = jobs
            await on_progress("Fetching: 50% [3 jobs] (1/2) 0:01 | platform/build")
            await release.wait()
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", slow_sync)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()

            await pilot.press("space", "S")
            await pilot.pause()
            busy = app.screen_stack[-1]
            assert isinstance(busy, BusyModal)
            bar = busy.query_one("#busy-bar")
            assert (bar.progress, bar.total) == (1, 2)
            jobs = app.config.sync_jobs
            assert seen["jobs"] == jobs
            assert f"-j{jobs}" in str(busy.query_one("#busy-command").render())
            status = str(busy.query_one("#busy-status").render())
            assert f"jobs: {jobs} max, 3 running" in status
            assert "platform/build" in str(busy.query_one("#busy-progress").render())

            show_all = app.show_all
            for key in ("space", "escape", "a", "b", "q"):
                await pilot.press(key)
            await pilot.pause()
            assert app.screen_stack[-1] is busy  # nothing got through
            assert app.show_all == show_all

            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            result = app.screen_stack[-1]
            assert isinstance(result, DiffScreen)
            assert not any(isinstance(s, BusyModal) for s in app.screen_stack)

    asyncio.run(scenario())


def test_refresh_shows_blocking_progress_dialog(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(app.screen_stack) == 1  # startup refresh dialog closed

            release = threading.Event()
            real_collect_all = git_backend.collect_all

            def slow_collect_all(projects, ignore, max_workers=16, on_progress=None):
                projects = list(projects)
                on_progress(1, len(projects), projects[0])
                release.wait(5)
                return real_collect_all(projects, ignore, max_workers)

            monkeypatch.setattr(git_backend, "collect_all", slow_collect_all)

            await pilot.press("r")
            await pilot.pause(0.2)
            busy = app.screen_stack[-1]
            assert isinstance(busy, BusyModal)
            bar = busy.query_one("#busy-bar")
            assert (bar.progress, bar.total) == (1, 2)
            assert "1/2 projects" in str(busy.query_one("#busy-progress").render())

            await pilot.press("a")  # blocked while refreshing
            assert app.screen_stack[-1] is busy

            release.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(app.screen_stack) == 1

    asyncio.run(scenario())


def test_dragging_splitter_resizes_project_list(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            project_list = app.query_one("#project-list", ProjectList)
            before = project_list.size.width

            await pilot.mouse_down("#pane-splitter")
            await pilot.hover(offset=(80, 10))
            await pilot.mouse_up(offset=(80, 10))
            await pilot.pause()
            assert project_list.size.width == 80 != before

            # clamped so the right pane never collapses
            await pilot.mouse_down("#pane-splitter")
            await pilot.hover(offset=(119, 10))
            await pilot.mouse_up(offset=(119, 10))
            await pilot.pause()
            assert project_list.size.width == 120 - 1 - 20

    asyncio.run(scenario())


def test_discard_asks_first_then_cleans_stat_but_keeps_ignored(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        base = dirty_tree / "frameworks/base"
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            _select_project(app.query_one("#project-list", ProjectList), "frameworks/base")
            await pilot.pause()

            await pilot.press("space", "x")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ConfirmModal)
            await pilot.press("enter")  # only `y` confirms
            await pilot.press("n")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert (base / "real_change.c").exists()

            await pilot.press("space", "x")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert isinstance(app.screen_stack[-1], DiffScreen)
            project = next(p for p in app.projects if p.path == "frameworks/base")
            assert project.stat_summary == "clean"
            assert (base / "README.md").read_text() == "hello\n"
            assert not (base / "real_change.c").exists()
            assert (base / "generated.pyc").exists()  # hidden by ignore rules: kept

    asyncio.run(scenario())
