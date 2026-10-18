import asyncio

from repo_tui import repo_backend
from repo_tui.app import RepoTuiApp
from repo_tui.widgets.action_panel import ActionPanel
from repo_tui.widgets.confirm_modal import ConfirmModal
from repo_tui.widgets.context_menu import ContextMenu
from repo_tui.widgets.prompt_modal import PromptModal

from test_app import _porcelain, dirty_tree  # noqa: F401 - fixture

BOTH = ["frameworks/base", "build/make"]  # list order with "show all": changed first
IN_TREE_ORDER = ["build/make", "frameworks/base"]  # commands get the manifest's order


async def _mark_both(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.press("a")  # show build/make too
    await pilot.pause()
    project_list = app.query_one("#project-list")
    project_list.move_cursor(row=0)
    await pilot.press("m", "m")  # m moves down, so this marks both rows
    await pilot.pause()
    assert project_list.marked == set(BOTH)
    return project_list


def _keybar(app):
    return str(app.query_one("#keybar").render())


def test_marking_shows_in_the_list_and_key_bar(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            project_list = await _mark_both(app, pilot)
            assert "2 marked" in _keybar(app)
            assert str(project_list.get_row_at(0)[0]).startswith("[b $accent]✓")

            await pilot.press("R")  # a refresh keeps them
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert project_list.marked == set(BOTH)

            await pilot.press("M")  # marks present: clears them
            await pilot.pause()
            assert project_list.marked == set()
            assert "marked" not in _keybar(app)
            await pilot.press("M")  # none: marks every shown row
            await pilot.pause()
            assert project_list.marked == set(BOTH)
            await pilot.press("escape")
            await pilot.pause()
            assert project_list.marked == set()

    asyncio.run(scenario())


def test_space_sync_acts_on_the_marked_projects_then_unmarks(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_sync(repo_root, paths=None, on_output=None, **kwargs):
            calls.append(paths)
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", fake_sync)

        async with app.run_test(size=(140, 40)) as pilot:
            project_list = await _mark_both(app, pilot)
            await pilot.press("space")
            await pilot.pause()
            panel = app.screen_stack[-1]
            assert isinstance(panel, ActionPanel)
            assert "2 marked projects" in panel.query_one("#action-box").border_title
            keys = str(panel.query_one("#action-keys").render())
            assert "2 marked projects" in keys and "This project" not in keys
            await pilot.press("s")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == [IN_TREE_ORDER]  # one repo sync for both
            assert project_list.marked == set()

    asyncio.run(scenario())


def test_cancelling_keeps_the_marks(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            project_list = await _mark_both(app, pilot)
            await pilot.press("space", "b")
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], PromptModal)
            await pilot.press("escape")
            await pilot.pause()
            assert project_list.marked == set(BOTH)

    asyncio.run(scenario())


def test_discard_lists_each_marked_project_and_cleans_them(dirty_tree):
    (dirty_tree / "build/make" / "README.md").write_text("edited\n")

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            await _mark_both(app, pilot)
            await pilot.press("space", "x")
            await pilot.pause()
            confirm = app.screen_stack[-1]
            assert isinstance(confirm, ConfirmModal)
            title = str(confirm.query_one("#confirm-title").render())
            assert "in 2 projects?" in title
            body = str(confirm.query_one("#confirm-body").render())
            assert "build/make  1 file(s)" in body
            await pilot.press("y")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert _porcelain(dirty_tree / "build/make").strip() == ""
            # frameworks/base keeps only what the ignore rules hide
            assert "real_change.c" not in _porcelain(dirty_tree / "frameworks/base")

    asyncio.run(scenario())


def test_checkout_offers_branches_with_how_many_have_them(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_checkout(repo_root, branch, paths=None, on_output=None):
            calls.append((branch, paths))
            return 0

        monkeypatch.setattr(repo_backend, "checkout_branch", fake_checkout)

        async with app.run_test(size=(140, 40)) as pilot:
            await _mark_both(app, pilot)
            await pilot.press("space", "c")
            await pilot.pause()
            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            options = menu.query_one("OptionList")._options
            assert [o.id for o in options] == ["topic-b"]
            assert "1 of 2" in str(options[0].prompt)
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == [("topic-b", ["frameworks/base"])]  # only where it exists

    asyncio.run(scenario())


def test_unshallow_refuses_marks_and_forall_offers_them_first(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_forall(repo_root, command, paths=None, on_output=None):
            calls.append(paths)
            return 0

        monkeypatch.setattr(repo_backend, "forall", fake_forall)

        async with app.run_test(size=(140, 40)) as pilot:
            await _mark_both(app, pilot)
            await pilot.press("space", "h")
            await pilot.pause()
            assert len(app.screen_stack) == 1  # just a warning toast

            await pilot.press("space", "f")
            await pilot.pause()
            await pilot.press(*"pwd", "enter")
            await pilot.pause()
            menu = app.screen_stack[-1]
            ids = [o.id for o in menu.query_one("OptionList")._options]
            assert ids[0] == "marked"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert calls == [IN_TREE_ORDER]

    asyncio.run(scenario())
