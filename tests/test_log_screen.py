import asyncio
import subprocess

from textual.widgets import OptionList

from repo_tui import git_backend
from repo_tui.app import RepoTuiApp
from repo_tui.models import Project
from repo_tui.widgets.diff_screen import DEFAULT_PALETTE, DiffScreen
from repo_tui.widgets.log_screen import LogScreen, details_text

from test_app import _GIT_ENV, _focus_file, _git, dirty_tree  # noqa: F401 - fixture


def _commit_tree(cwd, parent, subject):
    return subprocess.run(
        ["git", "commit-tree", "HEAD^{tree}", "-p", parent, "-m", subject],
        cwd=cwd, check=True, capture_output=True, text=True, env=_GIT_ENV,
    ).stdout.strip()


def _local_and_upstream(base):
    """frameworks/base: one commit on HEAD that upstream lacks, and one on
    upstream (refs/remotes/aosp/main, the manifest's) that HEAD lacks."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=base, check=True,
                          capture_output=True, text=True).stdout.strip()
    _git(base, "update-ref", "refs/remotes/aosp/main", _commit_tree(base, head, "from upstream"))
    _git(base, "update-ref", "refs/heads/main", _commit_tree(base, head, "my local fix"))


def test_history_follows_renames_and_knows_each_name(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "old.txt").write_text("one\n")
    _git(tmp_path, "add", "old.txt")
    _git(tmp_path, "commit", "-q", "-m", "add it")
    _git(tmp_path, "mv", "old.txt", "new.txt")
    _git(tmp_path, "commit", "-q", "-m", "rename it")
    project = Project(path="p", name="p", abs_path=tmp_path)

    commits = git_backend.history(project, "new.txt")
    assert [(c.subject, c.path) for c in commits] == [("rename it", "new.txt"), ("add it", "old.txt")]
    assert "+one" in git_backend.commit_patch(project, commits[-1])  # under its old name
    assert [c.subject for c in git_backend.history(project)] == ["rename it", "add it"]
    assert git_backend.history_context(project) == (None, None)  # no upstream known


def test_history_context_counts_local_and_newer_upstream(dirty_tree):
    base = dirty_tree / "frameworks/base"
    _local_and_upstream(base)
    project = Project(
        path="frameworks/base", name="b", abs_path=base, manifest_revision="main", remote="aosp"
    )
    local, behind = git_backend.history_context(project)
    [mine] = [c for c in git_backend.history(project) if c.sha in local]
    assert mine.subject == "my local fix" and behind == 1


def test_details_text_styles_headers_and_stat():
    text = details_text(
        "commit abc\nAuthor: A <a@a>\nDate:   2026\nRefs:   \n\n\n\nmsg\n\n a.txt | 3 ++-\n",
        DEFAULT_PALETTE,
    )
    assert "Refs" not in str(text)  # empty refs line dropped
    assert "\n\n\n" not in str(text)  # blank runs collapsed
    styles = {str(text)[s.start:s.end]: str(s.style) for s in text.spans}
    assert styles["++"] == DEFAULT_PALETTE["added"] and styles["-"] == DEFAULT_PALETTE["removed"]


def test_l_opens_the_project_history_with_filter_diff_and_copy(dirty_tree, monkeypatch):
    base = dirty_tree / "frameworks/base"
    _local_and_upstream(base)

    async def scenario():
        app = RepoTuiApp(dirty_tree)
        copied = []
        monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press("l")  # frameworks/base is selected
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            screen = app.screen_stack[-1]
            assert isinstance(screen, LogScreen)
            summary = str(screen.query_one("#log-summary").render())
            assert "2 commits" in summary and "1 not upstream" in summary
            assert "upstream has 1 newer" in summary
            option_list = screen.query_one("#log-list", OptionList)
            first = str(option_list.get_option_at_index(0).prompt)
            assert first.startswith("↑ ") and "my local fix" in first
            assert "HEAD -> main" in first
            assert not str(option_list.get_option_at_index(1).prompt).startswith("↑")
            await app.workers.wait_for_complete()
            detail = "\n".join(str(line.text) for line in screen.query_one("#log-detail").lines)
            assert "my local fix" in detail and "Author: t <t@t>" in detail

            await pilot.press("y")
            assert len(copied[-1]) == 40

            await pilot.press("slash", *"init")
            await pilot.pause()
            assert option_list.option_count == 1
            await pilot.press("escape")  # clears the filter, stays open
            await pilot.pause()
            assert option_list.option_count == 2 and app.screen_stack[-1] is screen

            await pilot.press("enter")  # the highlighted commit's full diff
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen_stack[-1], DiffScreen)
            await pilot.press("escape", "escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1

    asyncio.run(scenario())


def test_l_in_the_file_list_opens_that_files_history(dirty_tree):
    async def scenario():
        app = RepoTuiApp(dirty_tree)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await _focus_file(pilot, app, "README.md")
            await pilot.press("l")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            screen = app.screen_stack[-1]
            assert isinstance(screen, LogScreen)
            assert "File history: frameworks/base/README.md" in str(
                screen.query_one("#log-title").render()
            )
            assert screen.query_one("#log-list", OptionList).option_count == 1

    asyncio.run(scenario())
