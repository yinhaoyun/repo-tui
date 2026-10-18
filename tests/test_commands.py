import asyncio

from textual.command import CommandPalette

from repo_tui import config as config_mod
from repo_tui import repo_backend
from repo_tui.app import RepoTuiApp
from repo_tui.widgets.picker_modal import PickerModal
from repo_tui.widgets.prompt_modal import PromptModal

from test_app import _select_project, dirty_tree  # noqa: F401 - fixture


def _titles(app):
    return [c.title for c in app.get_system_commands(app.screen_stack[0])]


async def _ready(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()


def test_commands_fit_the_selection(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            await _ready(app, pilot)
            await pilot.press("a")
            await pilot.pause()
            project_list = app.query_one("#project-list")

            _select_project(project_list, "frameworks/base")  # branches, files, not shallow
            await pilot.pause()
            titles = _titles(app)
            assert "Abandon branch" in titles
            assert "Unshallow project" not in titles
            assert "File: history" in titles
            assert "Show all projects" not in titles and "Hide unchanged projects" in titles
            # Textual's own theme switch (which wouldn't save) is replaced by ours
            assert titles.count("Theme") == 1 and "Change theme" not in titles
            assert "Quit" in titles or "Quit the application" in titles
            help_texts = {c.title: c.help for c in app.get_system_commands(app.screen)}
            assert help_texts["Sync project"].endswith("frameworks/base  ·  space s")

            _select_project(project_list, "build/make")  # detached, clean
            await pilot.pause()
            titles = _titles(app)
            assert "Abandon branch" not in titles
            assert not any(t.startswith("File: ") for t in titles)

    asyncio.run(scenario())


def test_ctrl_p_finds_and_runs_a_command(dirty_tree, monkeypatch):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        calls = []

        async def fake_sync(repo_root, paths=None, on_output=None, **kwargs):
            calls.append(paths)
            return 0

        monkeypatch.setattr(repo_backend, "sync_projects", fake_sync)

        async with app.run_test(size=(140, 40)) as pilot:
            await _ready(app, pilot)
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert CommandPalette.is_open(app)
            await pilot.press(*"sync project")
            await pilot.pause(0.5)
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert calls == [["frameworks/base"]]

    asyncio.run(scenario())


def test_ctrl_p_does_nothing_over_a_dialog(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            await _ready(app, pilot)
            app.push_screen(PromptModal("anything?"))
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert not CommandPalette.is_open(app)
            assert isinstance(app.screen_stack[-1], PromptModal)

    asyncio.run(scenario())


def test_theme_command_applies_and_saves(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            await _ready(app, pilot)
            app.action_pick_theme()
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], PickerModal)
            await pilot.press(*"nord", "enter")
            await pilot.pause()
            assert app.theme == "nord"
            assert config_mod.load_settings() == {"theme": "nord"}

    asyncio.run(scenario())
