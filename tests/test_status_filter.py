import asyncio
from pathlib import Path

from repo_tui.app import RepoTuiApp
from repo_tui.models import FileStatus, Project
from repo_tui.widgets.context_menu import ContextMenu
from repo_tui.widgets.project_list import STATUS_FILTERS

from test_app import dirty_tree  # noqa: F401 - fixture


def _project(**kwargs):
    return Project(path="p", name="p", abs_path=Path("/p"), **kwargs)


def test_each_status_filter_picks_its_projects():
    edited = [FileStatus("a", " ", "M")]
    cases = {
        "modified": _project(files=edited),
        "ahead": _project(current_branch="t", ahead=2),
        "behind": _project(current_branch="t", behind=1),
        "branch": _project(current_branch="t"),
        "detached": _project(is_detached=True, files=edited),
        "error": _project(error="boom"),
    }
    for filter_id, project in cases.items():
        assert STATUS_FILTERS[filter_id][1](project), filter_id
    clean_detached = _project(is_detached=True)
    assert not any(test(clean_detached) for fid, (_, test) in STATUS_FILTERS.items() if fid)
    ignored_only = _project(is_detached=True, files=[FileStatus("o", "?", "?", ignored=True)])
    assert not STATUS_FILTERS["detached"][1](ignored_only)


def _rows(app):
    return app.query_one("#project-list")._row_paths


def test_f_picks_a_state_and_the_header_says_so(dirty_tree):
    (dirty_tree / "build/make" / "README.md").write_text("edited\n")  # detached + changes

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert sorted(_rows(app)) == ["build/make", "frameworks/base"]

            await pilot.press("f")
            await pilot.pause()
            menu = app.screen_stack[-1]
            assert isinstance(menu, ContextMenu)
            options = {o.id: str(o.prompt) for o in menu.query_one("OptionList")._options}
            assert "●" in options["all"] and "(2 changed)" in options["all"]
            assert "(2)" in options["modified"] and "(1)" in options["detached"]
            assert "ahead" not in options and "error" not in options  # none there
            ids = list(options)
            await pilot.press(*["down"] * ids.index("detached"), "enter")
            await pilot.pause()

            assert _rows(app) == ["build/make"]
            assert app.query_one("#hdr-filter").display
            header = str(app.query_one("#hdr-filter").render())
            assert "showing: detached + changes (1)" in header

            await pilot.press("a")  # show all doesn't undo the state filter
            await pilot.pause()
            assert _rows(app) == ["build/make"]

            await pilot.press("f")
            await pilot.pause()
            await pilot.press("enter")  # "Everything" is first
            await pilot.pause()
            assert app.status_filter == "" and not app.query_one("#hdr-filter").display
            assert len(_rows(app)) == len(app.projects)

    asyncio.run(scenario())
