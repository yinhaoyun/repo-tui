import asyncio
import inspect
import subprocess
import threading

import pytest
from textual.events import Click, MouseDown, MouseUp

from repo_tui import git_backend, repo_backend
from repo_tui.app import RepoTuiApp
from repo_tui.widgets.action_panel import ActionPanel
from repo_tui.widgets.busy_modal import BusyModal
from repo_tui.widgets.changed_screen import ChangedScreen
from repo_tui.widgets.sync_report import SyncReportScreen
from repo_tui.widgets.confirm_modal import ConfirmModal
from repo_tui.widgets.context_menu import ContextMenu
from repo_tui.widgets.detail_pane import FileTable
from repo_tui.widgets.diff_screen import DiffScreen
from repo_tui.widgets.log_screen import LogScreen
from repo_tui.widgets.result_screen import ResultScreen
from repo_tui.widgets.settings_screen import SettingsScreen
from repo_tui.widgets.help_modal import HelpModal
from repo_tui.widgets.picker_modal import PickerModal
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


async def _right_click(pilot, widget, offset):
    if "button" in inspect.signature(pilot.click).parameters:
        return await pilot.click(widget, offset=offset, button=3)
    # Textual < 7 (the newest on Python 3.8) has no click(button=); send the
    # same events it would.
    return await pilot._post_mouse_events(
        [MouseDown, MouseUp, Click], widget=widget, offset=offset, button=3
    )


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


def test_enter_enter_on_file_row_opens_diff_screen(dirty_tree):
    # enter opens the file's menu; Diff is its first entry
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
            assert isinstance(app.screen_stack[-1], ContextMenu)
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
            await _right_click(pilot, ProjectList, (5, 2))
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

            await pilot.press("space", "c")
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


def test_abandon_branch_picks_one_shows_unpushed_and_asks(dirty_tree, monkeypatch):
    base = dirty_tree / "frameworks/base"
    # topic-b gets a commit main doesn't have, with main as its upstream
    sha = subprocess.run(
        ["git", "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "work in progress"],
        cwd=base, check=True, capture_output=True, text=True, env=_GIT_ENV,
    ).stdout.strip()
    _git(base, "branch", "-f", "topic-b", sha)
    _git(base, "branch", "-q", "--set-upstream-to=main", "topic-b")

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_abandon(repo_root, branch, paths=None, on_output=None):
            calls.append((branch, paths))
            return 0

        monkeypatch.setattr(repo_backend, "abandon", fake_abandon)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            _select_project(app.query_one("#project-list", ProjectList), "frameworks/base")
            await pilot.press("space", "k")
            await pilot.pause()

            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            option_list = menu.query_one("OptionList")
            assert [o.id for o in option_list._options] == ["main", "topic-b", "--all"]
            option_list.highlighted = 1
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            confirm = app.screen_stack[-1]
            assert isinstance(confirm, ConfirmModal)
            body = str(confirm.query_one("#confirm-body").render())
            assert "1 unpushed commit(s)" in body
            assert "work in progress" in body
            assert "repo abandon topic-b frameworks/base" in body
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == [("topic-b", ["frameworks/base"])]

    asyncio.run(scenario())


def test_abandon_without_branches_says_so(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()
            _select_project(app.query_one("#project-list", ProjectList), "build/make")
            await pilot.press("space", "k")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            app._open_context_menu(app._selected_project())
            await pilot.pause()
            ids = [o.id for o in app.screen_stack[-1].query_one("OptionList")._options]
            assert "abandon" not in ids

    asyncio.run(scenario())


def test_abandon_a_branch_across_the_tree(dirty_tree, monkeypatch):
    _git(dirty_tree / "build/make", "branch", "topic-b")

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_abandon(repo_root, branch, paths=None, on_output=None):
            calls.append((branch, paths))
            return 0

        monkeypatch.setattr(repo_backend, "abandon", fake_abandon)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "K")
            await pilot.pause()

            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            option_list = menu.query_one("OptionList")
            # most widely held first
            assert [o.id for o in option_list._options] == ["topic-b", "main"]
            option_list.highlighted = 0
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            confirm = app.screen_stack[-1]
            assert isinstance(confirm, ConfirmModal)
            body = str(confirm.query_one("#confirm-body").render())
            assert "build/make" in body and "frameworks/base" in body
            assert "no upstream" in body
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == [("topic-b", None)]

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

            await pilot.press("space", "c")
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
            assert isinstance(app.screen_stack[-1], ResultScreen)

    asyncio.run(scenario())


def test_detach_uses_the_sync_jobs_setting(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        app.config.sync_jobs = 3
        jobs = []

        async def fake_sync_detach(repo_root, paths=None, on_output=None, **kwargs):
            jobs.append(kwargs.get("jobs"))
            return 0

        monkeypatch.setattr(repo_backend, "sync_detach", fake_sync_detach)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            app._detach_projects([next(p for p in app.projects if p.path == "build/make")])
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert jobs == [3]

    asyncio.run(scenario())


def test_bad_branch_name_asks_again_with_the_text_kept(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        started = []

        async def fake_start(repo_root, branch_name, paths=None, on_output=None):
            started.append((branch_name, paths))
            return 0

        monkeypatch.setattr(repo_backend, "start_branch", fake_start)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "b")
            await pilot.pause()
            await pilot.press(*"-bad", "enter")
            await pilot.pause()
            prompt = app.screen_stack[-1]
            assert isinstance(prompt, PromptModal)
            assert prompt.query_one("#prompt-input").value == "-bad"
            assert started == []

            prompt.query_one("#prompt-input").value = "topic/ok"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert started == [("topic/ok", [app._selected_project().path])]

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

            await _right_click(pilot, ProjectList, (5, 1))
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
            # no key that changes a checkout is bound on the top layer
            await pilot.press("s", "S", "d", "b", "c", "x")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            await pilot.press("f")  # only "show only…", a look-only filter menu
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ContextMenu)
            assert str(app.screen_stack[-1].query_one("#menu-title").render()) == "Show only"
            await pilot.press("escape")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == []

            await pilot.press("space")
            await pilot.press("z")  # not an action key: says so, stays open
            await pilot.pause()
            assert app.action_mode_active is True
            await pilot.press("escape")
            await pilot.pause()
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

            await pilot.press("space", "c")  # build/make has no other branch -> prompt
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], PromptModal)

            show_all = app.show_all
            await pilot.press("escape")
            await pilot.pause()
            app._open_context_menu(app._selected_project())
            await pilot.pause()
            await pilot.press("a")
            await pilot.press("R")
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
            assert isinstance(result, ResultScreen)
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

            await pilot.press("R")
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

            # the line follows the pointer
            splitter = app.query_one("#pane-splitter")
            assert splitter.size.width == 1
            await pilot.mouse_down("#pane-splitter", offset=(0, 5))
            await pilot.hover(offset=(80, 10))
            await pilot.mouse_up(offset=(80, 10))
            await pilot.pause()
            assert project_list.size.width == 80 != before
            assert splitter.region.x == 80
            await pilot.mouse_down("#pane-splitter", offset=(0, 5))
            await pilot.hover(offset=(before, 10))
            await pilot.mouse_up(offset=(before, 10))
            await pilot.pause()
            assert project_list.size.width == before

            # clamped so the right pane never collapses
            await pilot.mouse_down("#pane-splitter")
            await pilot.hover(offset=(119, 10))
            await pilot.mouse_up(offset=(119, 10))
            await pilot.pause()
            assert project_list.size.width == 120 - 1 - 20

            # a drag resizes; it never leaves a text selection behind
            assert not app.screen.selections

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

            assert isinstance(app.screen_stack[-1], ResultScreen)
            project = next(p for p in app.projects if p.path == "frameworks/base")
            assert project.stat_summary == "clean"
            assert (base / "README.md").read_text() == "hello\n"
            assert not (base / "real_change.c").exists()
            assert (base / "generated.pyc").exists()  # hidden by ignore rules: kept

    asyncio.run(scenario())


def test_single_project_action_rescans_only_that_project(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        detach_calls = []

        async def fake_sync_detach(repo_root, paths=None, on_output=None, **kwargs):
            detach_calls.append(paths)
            return 0

        monkeypatch.setattr(repo_backend, "sync_detach", fake_sync_detach)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            scanned = []
            real_collect_all = git_backend.collect_all

            def spy_collect_all(projects, *args, **kwargs):
                projects = list(projects)
                scanned.append([p.path for p in projects])
                return real_collect_all(projects, *args, **kwargs)

            monkeypatch.setattr(git_backend, "collect_all", spy_collect_all)

            _select_project(app.query_one("#project-list", ProjectList), "frameworks/base")
            await pilot.pause()
            await pilot.press("space", "d")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert detach_calls == [["frameworks/base"]]
            assert scanned == [["frameworks/base"]]

            await pilot.press("escape")  # close the result screen
            await pilot.press("R")  # a manual refresh-all still scans everything
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert sorted(scanned[-1]) == ["build/make", "frameworks/base"]

    asyncio.run(scenario())


def test_sync_errors_and_warnings_are_called_out(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []

        async def failing_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            await on_output("Fetching: 100% (2/2), done")
            await on_output("warning: project platform/build has local changes")
            await on_output("error: Cannot checkout platform/build: ManifestInvalidRevisionError")
            await on_output("Failing repos:")
            return 1

        monkeypatch.setattr(repo_backend, "sync_projects", failing_sync)
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "S")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            result = app.screen_stack[-1]
            assert isinstance(result, ResultScreen)
            assert result.severity == "error"
            assert result.status == "failed (exit 1), 2 error(s) and 1 warning(s)"
            problems_box = str(result.query_one("#result-problems").render())
            assert "Cannot checkout platform/build" in problems_box
            assert "has local changes" in problems_box
            assert "Fetching" not in problems_box

            assert len(notes) == 1
            message, kw = notes[0]
            assert kw["severity"] == "error"
            assert "warning: project platform/build" in message

    asyncio.run(scenario())


def test_clean_sync_shows_no_problems_box_or_toast(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []

        async def ok_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            await on_output("Syncing: 100% (2/2), done")
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", ok_sync)
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "S")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            result = app.screen_stack[-1]
            assert isinstance(result, ResultScreen)
            assert (result.status, result.severity) == ("done", "information")
            assert not result.query("#result-problems")
            assert notes == []

    asyncio.run(scenario())


def test_settings_screen_toggles_highlighted_hints_and_saves_it(dirty_tree):
    from repo_tui import config as config_mod
    from repo_tui import hints

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            keybar = app.query_one("#keybar")
            assert hints.highlight_enabled()
            assert "on $warning" in str(keybar.content)

            await pilot.press("o")
            await pilot.pause()
            screen = app.screen_stack[-1]
            assert isinstance(screen, SettingsScreen)
            await pilot.press("enter")  # first row of Display: highlight hints
            await pilot.pause()

            assert not hints.highlight_enabled()
            assert "on $warning" not in str(keybar.content)
            assert config_mod.load_settings() == {"highlight_hints": False}
            assert "Saved" in str(screen.query_one("#settings-status").content)

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], SettingsScreen)

    asyncio.run(scenario())
    # a fresh start picks the saved choice up
    assert RepoTuiApp(dirty_tree).config.highlight_hints is False


def test_settings_tabs_change_theme_jobs_and_sort(dirty_tree):
    from repo_tui import config as config_mod

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("o")
            await pilot.pause()
            screen = app.screen_stack[-1]

            # Display tab, row 2: theme -> next theme, applied live
            before = app.theme
            await pilot.press("down", "right")
            await pilot.pause()
            assert app.theme != before
            assert app.config.theme == app.theme

            # Sync tab: jobs via typed number, clamped to the allowed range
            await pilot.press("tab", "enter")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], PromptModal)
            await pilot.press(*"500", "enter")
            await pilot.pause()
            assert app.config.sync_jobs == 128
            await pilot.press("left")
            await pilot.pause()
            assert app.config.sync_jobs == 127

            # Behaviour tab, row 2: sort; shown rows are only that tab's
            await pilot.press("tab")
            await pilot.pause()
            shown = [r.setting.name for r in screen.query("SettingRow") if r.display]
            assert shown == ["max_workers", "sort", "result_screen", "auto_refresh_minutes", "check_updates", "update_check_hours"]
            await pilot.press("down", "right")
            await pilot.pause()
            assert app.config.sort == "changes"

            saved = config_mod.load_settings()
            assert saved["sync_jobs"] == 127 and saved["sort"] == "changes"
            assert saved["theme"] == app.theme

    asyncio.run(scenario())


def test_problems_only_result_setting_skips_clean_output_screen(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        app.config.result_screen = "problems"
        notes = []

        async def ok_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            await on_output("Syncing: 100% (2/2), done")
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", ok_sync)
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(message))

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "S")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], ResultScreen)
            assert notes and "done" in notes[-1]

    asyncio.run(scenario())


def test_confirm_sync_all_setting_asks_first(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        app.config.confirm_sync_all = True
        calls = []

        async def fake_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            calls.append(paths)
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", fake_sync)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "S")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ConfirmModal)
            await pilot.press("n")
            await pilot.pause()
            assert calls == []

            await pilot.press("space", "S")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == [None]

    asyncio.run(scenario())


def test_auto_refresh_rescans_in_background_without_a_dialog(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app._auto_timer is None  # off by default

            app._apply_setting("auto_refresh_minutes", 5)
            assert app._auto_timer is not None

            (dirty_tree / "build/make/new_file.txt").write_text("x\n")
            app._auto_refresh_tick()
            await pilot.pause()
            assert not any(isinstance(s, BusyModal) for s in app.screen_stack)
            await app.workers.wait_for_complete()
            await pilot.pause()
            make = next(p for p in app.projects if p.path == "build/make")
            assert make.untracked_count == 1

            app._apply_setting("auto_refresh_minutes", 0)
            assert app._auto_timer is None

    asyncio.run(scenario())


def test_menu_refresh_after_a_background_scan_updates_the_list(dirty_tree):
    """A background scan that finishes while the context menu is open must
    not leave the menu holding a Project the list no longer shows."""

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()
            project_list = app.query_one("#project-list", ProjectList)
            _select_project(project_list, "build/make")
            app._open_context_menu(project_list.project_at_cursor())
            await pilot.pause()

            await app._background_refresh()
            (dirty_tree / "build/make/edited_elsewhere.c").write_text("int y;\n")

            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            menu.dismiss("refresh")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            make = next(p for p in app.projects if p.path == "build/make")
            assert make.untracked_count == 1 and make.is_changed
            _select_project(project_list, "build/make")
            assert project_list.project_at_cursor().stat_summary == "?1"

    asyncio.run(scenario())


@pytest.mark.parametrize("on", [True, False])
def test_every_key_hint_follows_the_highlight_setting(on):
    from repo_tui import hints
    from repo_tui.widgets.help_modal import help_text
    from repo_tui.widgets.keybar import top_level_hint

    hints.set_highlight(on)
    for text in (top_level_hint(), help_text()):
        assert ("[b #000000 on $warning]" in text) is on
    assert ("[b $text-warning]Scroll with" in help_text()) is on


def test_refresh_selected_project_rescans_only_that_one(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()
            _select_project(app.query_one("#project-list", ProjectList), "build/make")
            await pilot.pause()

            scanned = []
            real_collect_all = git_backend.collect_all

            def spy(projects, *args, **kwargs):
                projects = list(projects)
                scanned.append([p.path for p in projects])
                return real_collect_all(projects, *args, **kwargs)

            monkeypatch.setattr(git_backend, "collect_all", spy)
            (dirty_tree / "build/make/edited_elsewhere.c").write_text("int y;\n")

            await pilot.press("r")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert scanned == [["build/make"]]
            make = next(p for p in app.projects if p.path == "build/make")
            assert make.untracked_count == 1 and make.is_changed

    asyncio.run(scenario())


def _commit(cwd, name, subject):
    (cwd / name).write_text(subject + "\n")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", subject)


def test_sync_reports_which_projects_got_which_commits(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)

        async def fake_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            # what a real sync would do: new upstream commits land in build/make
            make = dirty_tree / "build/make"
            _commit(make, "a.mk", "Add rule A")
            _commit(make, "b.mk", "Add rule B")
            await on_output("Syncing: 100% (2/2), done")
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", fake_sync)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert not app.query_one("#hdr-sync").display  # no sync yet

            await pilot.press("space", "S")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            report = app.sync_report
            assert report.scanned == 2
            assert [(c.path, c.added, c.dropped) for c in report.changes] == [("build/make", 2, 0)]
            assert "1 updated" in str(app.query_one("#hdr-sync").content)

            # a clean sync that moved something opens the report on its own
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen_stack[-1]
            assert isinstance(screen, SyncReportScreen)
            detail = "\n".join(line.text for line in screen.query_one("#report-detail").lines)
            assert "Add rule A" in detail and "Add rule B" in detail
            assert "New commits (2)" in detail

            # o swaps it for the command's output, which links back with u
            await pilot.press("o")
            await pilot.pause()
            result = app.screen_stack[-1]
            assert isinstance(result, ResultScreen)
            assert "1 project(s) moved" in str(result.query_one("#result-updates").content)
            await pilot.press("u")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], SyncReportScreen)
            await pilot.press("escape")
            await pilot.pause()

            # the header item reopens it
            await pilot.click("#hdr-sync")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], SyncReportScreen)

    asyncio.run(scenario())


def test_sync_report_does_not_open_itself_when_off_or_after_detach(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        make = dirty_tree / "build/make"

        async def fake_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            _commit(make, "a.mk", "Add rule A")
            return 0

        async def fake_detach(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            _commit(make, "b.mk", "Add rule B")
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", fake_sync)
        monkeypatch.setattr(repo_backend, "sync_detach", fake_detach)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()

            # a detach moves HEAD on purpose: output screen, report via u only
            app._detach_projects([next(p for p in app.projects if p.path == "build/make")])
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert [c.path for c in app.sync_report.changes] == ["build/make"]
            assert isinstance(app.screen_stack[-1], ResultScreen)
            await pilot.press("escape")
            await pilot.pause()

            app.config.show_sync_report = False
            await pilot.press("space", "S")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert [c.added for c in app.sync_report.changes] == [1]
            assert isinstance(app.screen_stack[-1], ResultScreen)

    asyncio.run(scenario())


def test_clicking_changed_count_explains_and_jumps_to_project(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.click("#hdr-changed")
            await pilot.pause()
            screen = app.screen_stack[-1]
            assert isinstance(screen, ChangedScreen)
            table = screen.query_one("#changed-table")
            row = [str(cell) for cell in table.get_row("frameworks/base")]
            assert "file(s) changed" in row[2] and "on branch main" in row[2]

            await pilot.press("enter")
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], ChangedScreen)
            assert app._selected_project().path == "frameworks/base"

    asyncio.run(scenario())


def test_switch_manifest_warns_first_then_inits_syncs_and_reloads(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls, notes = [], []

        async def fake_switch(repo_root, branch, manifest, on_output=None, **kwargs):
            calls.append((branch, manifest))
            await on_output("warning: build/make has local changes, not removing")
            # the new manifest drops build/make, but repo left it on disk
            (repo_root / ".repo" / "project.list").write_text("frameworks/base\n")
            return 0

        monkeypatch.setattr(
            repo_backend, "fetch_manifests", lambda root: "fatal: could not read from remote"
        )
        monkeypatch.setattr(repo_backend, "switch_manifest", fake_switch)
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))

        async with app.run_test(size=(120, 50)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "M")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            # the failed fetch is reported, and the picker still opens
            assert any(kw.get("severity") == "warning" for _, kw in notes)
            assert isinstance(app.screen_stack[-1], PickerModal)
            await pilot.press(*"release-2", "enter")  # not listed: used as typed
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert isinstance(app.screen_stack[-1], PickerModal)
            await pilot.press(*"car.xml", "enter")
            await pilot.pause()

            confirm = app.screen_stack[-1]
            assert isinstance(confirm, ConfirmModal)
            body = str(confirm.query_one("#confirm-body").render())
            assert "repo init -b release-2 -m car.xml" in body
            assert "Couldn't fetch the manifest repo" in body
            assert "isn't among the known manifest branches" in body
            assert "uncommitted changes" in body and "frameworks/base" in body
            assert calls == []  # nothing runs before y

            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert calls == [("release-2", "car.xml")]
            assert [p.path for p in app.projects] == ["frameworks/base"]
            result = app.screen_stack[-1]
            assert isinstance(result, ResultScreen)
            assert result.severity == "warning"
            problems = [line for _, line in result.problem_lines]
            assert any("not removing" in line for line in problems)
            assert any("still on disk" in line and "build/make" in line for line in problems)

    asyncio.run(scenario())


def test_switch_manifest_refuses_option_like_names(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(repo_backend, "fetch_manifests", lambda root: "")
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "M")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press(*"--foo", "enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert len(app.screen_stack) == 1
            assert notes and notes[-1][1]["severity"] == "error"
            assert "can't start with '-'" in notes[-1][0]

    asyncio.run(scenario())


def test_update_available_shows_in_header_and_U_updates_then_offers_restart(
    dirty_tree, monkeypatch
):
    from repo_tui import self_update
    from repo_tui.widgets.header_bar import HeaderBar

    # A stray "[" in a commit subject used to swallow the dialog's own markup
    # and crash the app with a MarkupError.
    check = self_update.UpdateCheck(
        behind=2, commits=["abc1234 new thing", "def5678 jump between files (]/[)"]
    )
    monkeypatch.setattr(self_update, "check_for_update", lambda **kw: check)
    updates = []

    def fake_update(say, batch=False):
        updates.append(batch)
        say("pulled new commits:\nabc1234 new thing\ndef5678 jump between files (]/[)", False)
        say("done (aaaa -> bbbb).", False)
        return 0

    monkeypatch.setattr(self_update, "update", fake_update)

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            header = app.query_one("#header", HeaderBar)
            assert header.update_behind == 2
            assert header.query_one("#hdr-update").display
            assert any("U" in m and kw.get("title") == "repo-tui update available"
                       for m, kw in notes)

            await pilot.press("U")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            confirm = app.screen_stack[-1]
            assert isinstance(confirm, ConfirmModal)
            body = str(confirm.query_one("#confirm-body").render())
            assert "new thing" in body and "(]/[)" in body
            assert updates == []  # nothing before y

            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert updates == [True]  # non-interactive git inside the TUI
            assert isinstance(app.screen_stack[-1], ResultScreen)
            assert header.update_behind == 0

            await pilot.press("escape")  # close output -> restart offer
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ConfirmModal)
            await pilot.press("y")
            await pilot.pause()
        assert app.restart_requested

    asyncio.run(scenario())


def test_U_explains_why_it_cant_update(dirty_tree, monkeypatch):
    from repo_tui import self_update

    check = self_update.UpdateCheck(behind=1, dirty=True, root=dirty_tree)
    monkeypatch.setattr(self_update, "check_for_update", lambda **kw: check)

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            notes.clear()
            await pilot.press("U")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(app.screen_stack) == 1
            message, kw = notes[-1]
            assert kw["severity"] == "error" and "local changes" in message

    asyncio.run(scenario())


def test_failed_startup_update_check_is_reported(dirty_tree, monkeypatch):
    from repo_tui import self_update

    monkeypatch.setattr(
        self_update, "check_for_update",
        lambda **kw: self_update.UpdateCheck(error="git fetch failed: offline"),
    )

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert any(kw.get("severity") == "warning" and "offline" in m for m, kw in notes)

    asyncio.run(scenario())


def test_extra_and_custom_themes_and_their_problems(dirty_tree, monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[display]\ntheme = "mine"\n'
        '[themes.mine]\nprimary = "#ff8800"\nbackground = "#101418"\n'
        '[themes.broken]\nprimary = "nope"\n'
    )
    monkeypatch.setenv("REPO_TUI_CONFIG", str(config))

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.theme == "mine"
            assert {"github-dark", "kanagawa", "synthwave-84", "broken"} & set(
                app.available_themes
            ) == {"github-dark", "kanagawa", "synthwave-84"}
            assert any(
                kw.get("severity") == "warning" and "[themes.broken] primary" in m
                for m, kw in notes
            )

            # enter on the Theme row opens a filterable picker
            await pilot.press("o")
            await pilot.pause()
            settings = app.screen_stack[-1]
            assert isinstance(settings, SettingsScreen)
            settings.show_tab(0)
            while settings._selected_row().setting.name != "theme":
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], PickerModal)
            await pilot.press(*"kanag", "enter")
            await pilot.pause()
            assert app.theme == "kanagawa"
            assert app.config.theme == "kanagawa"

    asyncio.run(scenario())


def test_unknown_configured_theme_is_reported(dirty_tree, monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[display]\ntheme = "no-such-theme"\n')
    monkeypatch.setenv("REPO_TUI_CONFIG", str(config))

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.theme == "textual-dark"
            assert any(
                kw.get("severity") == "warning" and "no-such-theme" in m for m, kw in notes
            )

    asyncio.run(scenario())


def test_update_checks_repeat_on_an_interval_and_only_pop_up_on_news(dirty_tree, monkeypatch):
    from repo_tui import self_update
    from repo_tui.widgets.header_bar import HeaderBar

    results = [
        self_update.UpdateCheck(behind=1),
        self_update.UpdateCheck(behind=1),  # nothing new: no popup
        self_update.UpdateCheck(error="git fetch failed: offline"),
        self_update.UpdateCheck(error="git fetch failed: offline"),  # same error: once
        self_update.UpdateCheck(behind=3),  # more commits: popup
    ]
    monkeypatch.setattr(self_update, "check_for_update", lambda **kw: results.pop(0))

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append(kw.get("title")))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app._update_timer is not None
            assert app._update_timer._interval == 6 * 3600  # default: every 6 h
            for _ in range(4):  # what the timer does every interval
                app._update_check_tick()
                await app.workers.wait_for_complete()
            await pilot.pause()
            assert notes == [
                "repo-tui update available",
                "Couldn't check for repo-tui updates",
                "repo-tui update available",
            ]
            assert app.query_one("#header", HeaderBar).update_behind == 3

            app._apply_setting("update_check_hours", 1)
            assert app._update_timer._interval == 3600
            app._apply_setting("check_updates", False)
            assert app._update_timer is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "check, title, text",
    [
        ({"head": "abc1234 2026-10-15"}, "repo-tui 0.1.0 (abc1234 2026-10-15)", "up to date"),
        ({"behind": 2, "commits": ["d1 new", "d2 fix"]}, "2 new commit(s)", "Press [b]U[/]"),
        ({"behind": 1, "commits": ["d1"], "dirty": True}, "1 new commit(s)", "Can't update yet"),
        ({"error": "git fetch failed: offline"}, "couldn't check", "offline"),
    ],
)
def test_v_checks_now_and_always_says_what_it_found(dirty_tree, monkeypatch, check, title, text):
    from repo_tui import self_update

    result = self_update.UpdateCheck(**check)
    calls = []
    monkeypatch.setattr(
        self_update, "check_for_update", lambda **kw: calls.append(1) or result
    )

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            notes.clear()
            before = len(calls)
            await pilot.press("v")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(calls) == before + 1  # a fresh check, not a cached result
            assert len(app.screen_stack) == 1
            message, kw = notes[-1]
            assert title in kw["title"] and text in message

    asyncio.run(scenario())


async def _focus_file(pilot, app, path):
    await pilot.press("tab")
    await pilot.pause()
    file_table = app.query_one("#file-table", FileTable)
    assert app.focused is file_table
    for i in range(file_table.row_count):
        file_table.move_cursor(row=i)
        if file_table.file_at_cursor().path == path:
            break
    await pilot.pause()
    return file_table


def _porcelain(path):
    return subprocess.run(
        ["git", "status", "--porcelain", "--", "README.md"],
        cwd=path, capture_output=True, text=True,
    ).stdout


def test_file_list_keys_stage_unstage_revert_history(dirty_tree, monkeypatch):
    base = dirty_tree / "frameworks/base"

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        notes = []
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            file_table = await _focus_file(pilot, app, "README.md")
            assert app.query_one("#keybar").context == "files"

            # look: history, in a viewer
            await pilot.press("l")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], LogScreen)
            await pilot.press("escape")
            await pilot.pause()

            # change: behind Space, in the "This file" column
            await pilot.press("space")
            await pilot.pause()
            panel = app.screen_stack[-1]
            assert "file: README.md" in panel.query_one("#action-box").border_title
            assert "This file" in str(panel.query_one("#action-keys").render())
            await pilot.press("a")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _porcelain(base) == "M  README.md\n"
            assert file_table.file_at_cursor().path == "README.md"  # cursor stayed
            assert notes[-1][0] == "Staged README.md"

            await pilot.press("space", "u")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _porcelain(base) == " M README.md\n"

            await pilot.press("space", "r")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ConfirmModal)
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _porcelain(base) == ""
            assert (base / "README.md").read_text() == "hello\n"

    asyncio.run(scenario())


def test_file_actions_only_from_the_file_list(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space")  # project list has focus
            await pilot.pause()
            panel = app.screen_stack[-1]
            assert "This file" not in str(panel.query_one("#action-keys").render())
            await pilot.press("a")  # not an action key here
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.screen_stack[-1] is panel
            assert "M  README.md" not in _porcelain(dirty_tree / "frameworks/base")

    asyncio.run(scenario())


def test_file_list_edit_and_copy_path(dirty_tree, monkeypatch, tmp_path):
    import contextlib

    editor = tmp_path / "fake-editor"
    editor.write_text('#!/bin/sh\necho edited >> "$1"\n')
    editor.chmod(0o755)
    monkeypatch.setenv("VISUAL", str(editor))
    base = dirty_tree / "frameworks/base"

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        copied, notes = [], []
        monkeypatch.setattr(app, "suspend", contextlib.nullcontext)
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        monkeypatch.setattr(app, "notify", lambda message, **kw: notes.append((message, kw)))
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await _focus_file(pilot, app, "README.md")
            await pilot.press("y")
            assert copied == [str(base / "README.md")]

            await pilot.press("e")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert (base / "README.md").read_text().endswith("edited\n")

            monkeypatch.setenv("VISUAL", "no-such-editor-xyz")
            await pilot.press("e")
            await pilot.pause()
            assert notes[-1][1]["severity"] == "error"
            assert "not found" in notes[-1][0]

    asyncio.run(scenario())


def test_key_bar_focus_line_follows_focus_and_global_line_stays(dirty_tree, monkeypatch):
    from repo_tui.widgets.keybar import global_hint

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        copied = []
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(200, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            keybar = app.query_one("#keybar")

            def lines():
                return str(keybar.render()).split("\n")

            assert keybar.context == "projects"
            focus, global_ = lines()
            assert focus.startswith(" PROJECT LIST ") and "copy project path" in focus
            assert global_.startswith(" GLOBAL ")
            await pilot.press("y")  # focus key: the project's path here
            assert copied[-1] == str(dirty_tree / "frameworks/base")

            await pilot.press("tab")
            await pilot.pause()
            focus, global_after = lines()
            assert focus.startswith(" FILE LIST ") and "blame" in focus
            assert global_after == global_  # the global line never changes
            await pilot.press("y")  # ...and the file's path here
            assert copied[-1].startswith(str(dirty_tree / "frameworks/base") + "/")

            await pilot.press("/")
            await pilot.pause()
            assert lines()[0].startswith(" FILTER ")
            await pilot.press("escape")
            await pilot.pause()
            assert keybar.context == "projects"

            # a dialog's own focus doesn't change it
            await pilot.press("space")
            await pilot.pause()
            assert keybar.context == "projects"

    asyncio.run(scenario())
    assert "PROJECT" not in global_hint() and "FILE" not in global_hint()


def test_unshallow_only_the_selected_project(dirty_tree, tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    for i in range(3):
        (origin / "f.txt").write_text(f"{i}\n")
        _git(origin, "add", "f.txt")
        _git(origin, "commit", "-q", "-m", f"c{i}")
    for path in ("external/shallow", "external/other"):
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(dirty_tree / path)],
            check=True, capture_output=True,
        )
        # repo leaves projects detached on a remote named after the manifest's remote
        _git(dirty_tree / path, "remote", "rename", "origin", "aosp")
        _git(dirty_tree / path, "checkout", "-q", "--detach")
    manifest = dirty_tree / ".repo" / "manifests" / "default.xml"
    manifest.write_text(manifest.read_text().replace(
        "</manifest>",
        '  <project path="external/shallow" name="ext/shallow" clone-depth="1"/>\n'
        '  <project path="external/other" name="ext/other" clone-depth="1"/>\n</manifest>',
    ))
    project_list = dirty_tree / ".repo" / "project.list"
    project_list.write_text(project_list.read_text() + "external/shallow\nexternal/other\n")

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("a")
            await pilot.pause()
            plist = app.query_one("#project-list", ProjectList)
            _select_project(plist, "external/shallow")
            await pilot.pause()
            project = plist.project_at_cursor()
            assert project.is_shallow and project.remote == "aosp"
            summary = str(app.query_one("#project-summary").render())
            assert "shallow clone" in summary

            await pilot.press("space", "h")
            await pilot.pause()
            confirm = app.screen_stack[-1]
            assert isinstance(confirm, ConfirmModal)
            body = str(confirm.query_one("#confirm-body").render())
            assert "git fetch --unshallow --progress aosp" in body
            assert 'clone-depth="1"' in body and "shallow again" in body

            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ResultScreen)
            assert app.screen_stack[-1].severity == "information"
            by_path = {p.path: p for p in app.projects}
            assert not by_path["external/shallow"].is_shallow
            assert by_path["external/other"].is_shallow  # only the selected one
            await pilot.press("escape")

            # already full: says so, runs nothing
            notes = []
            app.notify = lambda message, **kw: notes.append(message)
            await pilot.press("space", "h")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert "already has its full history" in notes[-1]

    asyncio.run(scenario())


@pytest.mark.parametrize("menu_key", ["enter", "shift+f10"])
def test_menu_key_opens_the_project_menu(dirty_tree, menu_key):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press(menu_key)
            await pilot.pause()
            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            assert menu._title == "frameworks/base"
            assert ("unshallow" in [o for o, _ in menu._options]) is False
            await pilot.press("escape")

    asyncio.run(scenario())


@pytest.mark.parametrize("menu_key", ["enter", "shift+f10", "right-click"])
def test_file_menu_offers_what_fits_and_runs_it(dirty_tree, menu_key):
    base = dirty_tree / "frameworks/base"
    (base / "brand_new.txt").write_text("x\n")  # untracked

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            file_table = await _focus_file(pilot, app, "brand_new.txt")
            row = file_table.cursor_row
            if menu_key == "right-click":
                await _right_click(pilot, FileTable, (5, row + 1))
            else:
                await pilot.press(menu_key)
            await pilot.pause()
            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)  # not the diff
            ids = [o for o, _ in menu._options]
            # untracked, nothing staged: no blame, no unstage
            assert ids == ["diff", "log", "edit", "copy", "stage", "revert"]

            menu.query_one("OptionList").highlighted = ids.index("stage")
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            out = subprocess.run(["git", "status", "--porcelain", "--", "brand_new.txt"],
                                 cwd=base, capture_output=True, text=True).stdout
            assert out == "A  brand_new.txt\n"

    asyncio.run(scenario())


def test_left_click_on_a_file_still_opens_its_diff(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.click(FileTable, offset=(5, 1))
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], DiffScreen)

    asyncio.run(scenario())


@pytest.mark.parametrize("width", [80, 100, 120])
def test_key_bar_fits_a_narrow_terminal_and_keeps_the_essentials(dirty_tree, width):
    from rich.cells import cell_len

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            keybar = app.query_one("#keybar")
            room = keybar.size.width

            def lines():
                return [line for line in str(keybar.render()).split("\n")]

            for _ in ("projects", "files"):
                focus, global_ = lines()  # two lines: the global one never wraps away
                assert cell_len(focus) <= room and cell_len(global_) <= room
                for must in ("space actions", "ctrl+p commands", "? help", "q quit"):
                    assert must in global_
                assert "menu" in focus
                await pilot.press("tab")
                await pilot.pause()

    asyncio.run(scenario())


def test_key_bar_shows_more_keys_when_the_terminal_grows(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            keybar = app.query_one("#keybar")
            assert "settings" not in str(keybar.render())
            await pilot.resize_terminal(200, 30)
            await pilot.pause()
            assert "o settings" in str(keybar.render())
            assert "…" not in str(keybar.render())

    asyncio.run(scenario())


def test_help_scrolls_on_a_short_terminal_and_other_keys_close_it(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("?")
            await pilot.pause()
            help_screen = app.screen_stack[-1]
            assert isinstance(help_screen, HelpModal)
            box = help_screen.query_one("#help-box")
            assert box.max_scroll_y > 0  # too long for 20 rows

            await pilot.press("j", "j")
            await pilot.pause()
            assert box.scroll_y == 2
            await pilot.press("G")
            await pilot.pause()
            assert box.scroll_y == box.max_scroll_y
            await pilot.press("g")
            await pilot.pause()
            assert box.scroll_y == 0
            await pilot.click("#help-text")  # inside the box: stays open
            await pilot.pause()
            assert app.screen_stack[-1] is help_screen

            await pilot.press("x")  # anything else closes, as before
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], HelpModal)

            await pilot.press("?")
            await pilot.pause()
            await pilot.click(offset=(0, 0))  # the dimmed background, outside the box
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], HelpModal)

    asyncio.run(scenario())


def _fake_upstream(path, subject="upstream change"):
    """Point refs/remotes/aosp/main (the fixture manifest's remote/revision)
    at a new commit on top of HEAD, as a fetch would."""
    sha = subprocess.run(
        ["git", "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", subject],
        cwd=path, check=True, capture_output=True, text=True, env=_GIT_ENV,
    ).stdout.strip()
    _git(path, "update-ref", "refs/remotes/aosp/main", sha)


@pytest.mark.parametrize("upstream_moved", [True, False])
def test_fetch_all_shows_what_a_sync_would_bring_in(dirty_tree, monkeypatch, upstream_moved):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_fetch(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            calls.append(paths)
            if upstream_moved:
                _fake_upstream(dirty_tree / "build/make")
            return 0

        monkeypatch.setattr(repo_backend, "fetch_projects", fake_fetch)

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "F")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert calls == [None]
            report_screen = app.screen_stack[-1]
            assert isinstance(report_screen, SyncReportScreen)
            title = str(report_screen.query_one("#report-title").render())
            assert "Incoming" in title
            if upstream_moved:
                summary = str(report_screen.query_one("#report-summary").render())
                assert "1 of 2 project(s) have new commits upstream" in summary
                await pilot.pause(0.3)
                await app.workers.wait_for_complete()
                detail = "\n".join(
                    str(line.text) for line in report_screen.query_one("#report-detail").lines
                )
                assert "Upstream commits a sync would bring in (1):" in detail
                assert "upstream change" in detail
            else:
                assert "Up to date" in str(report_screen.query_one("#report-empty").render())
            # a fetch moves no checkout, so it isn't the "last sync"
            assert app.sync_report is None

    asyncio.run(scenario())


def test_forall_picks_where_and_remembers_the_command(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_forall(repo_root, command, paths=None, on_output=None):
            calls.append((command, paths))
            return 0

        monkeypatch.setattr(repo_backend, "forall", fake_forall)

        async def run(command, scope_keys):
            await pilot.press("space", "f")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], PromptModal)
            await pilot.press(*command, "enter")
            await pilot.pause()
            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            assert [o.id for o in menu.query_one("OptionList")._options] == [
                "tree", "project", "changed"
            ]
            await pilot.press(*scope_keys)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press("escape")  # the output screen
            await pilot.pause()

        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            # frameworks/base is the only changed project (and selected)
            await run("git log -1", ["down", "down", "enter"])
            assert calls[-1] == ("git log -1", ["frameworks/base"])
            await run("pwd", ["enter"])  # Enter twice: the whole tree, as before
            assert calls[-1] == ("pwd", None)

            await pilot.press("space", "f")
            await pilot.pause()
            prompt = app.screen_stack[-1]
            field = prompt.query_one("#prompt-input")
            await pilot.press(*"half")
            await pilot.press("up")
            assert field.value == "pwd"
            await pilot.press("up")
            assert field.value == "git log -1"
            await pilot.press("down", "down")
            assert field.value == "half"  # back to what was being typed

    asyncio.run(scenario())


def _write_launcher(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env python3\nVERSION = {version}\n")
    return path


def test_sync_offers_repos_launcher_upgrade_and_runs_the_cp(dirty_tree, tmp_path, monkeypatch):
    source = _write_launcher(dirty_tree / ".repo" / "repo" / "repo", "(2, 54)")
    dest = _write_launcher(tmp_path / "bin" / "repo", "(2, 36)")

    async def scenario():
        app = RepoTuiApp(dirty_tree)

        async def ok_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            await on_output("... A new version of repo (2.54) is available.")
            await on_output("... You should upgrade soon:")
            await on_output(f"    cp {source} {dest}")
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", ok_sync)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "S")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            confirm = app.screen_stack[-1]
            assert isinstance(confirm, ConfirmModal)
            assert "2.36 → 2.54" in confirm._body
            assert isinstance(app.screen_stack[-2], ResultScreen)
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()

            assert "VERSION = (2, 54)" in dest.read_text()
            assert isinstance(app.screen_stack[-1], ResultScreen)

    asyncio.run(scenario())


def test_declined_launcher_upgrade_is_not_offered_again(dirty_tree, tmp_path, monkeypatch):
    source = _write_launcher(dirty_tree / ".repo" / "repo" / "repo", "(2, 54)")
    dest = _write_launcher(tmp_path / "bin" / "repo", "(2, 36)")

    async def scenario():
        app = RepoTuiApp(dirty_tree)

        async def ok_sync(repo_root, paths=None, on_output=None, on_progress=None, jobs=None):
            await on_output("... A new version of repo (2.54) is available.")
            await on_output(f"    cp {source} {dest}")
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", ok_sync)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("space", "S")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], ConfirmModal)
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("escape")  # close the output
            await pilot.pause()

            await pilot.press("space", "S")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not isinstance(app.screen_stack[-1], ConfirmModal)
            assert "VERSION = (2, 36)" in dest.read_text()

    asyncio.run(scenario())
