import asyncio

from rich.text import Text
from textual.app import App
from textual.widgets import RichLog

from repo_tui.widgets.diff_screen import (
    DEFAULT_PALETTE, DiffScreen, colorize, diff_roles, find_matches, highlight, summary,
)

DIFF = """diff --git a/q.sql b/q.sql
index 29b82df..ceb5927 100644
--- a/q.sql
+++ b/q.sql
@@ -1,3 +1,3 @@
 select 1;
--- a comment line, removed
 select 2;
+select 3;
\\ No newline at end of file
"""


def _styles(text):
    return {str(text)[s.start:s.end]: str(s.style) for s in text.spans}


def test_diff_roles_keep_removed_dash_lines_out_of_the_header():
    assert diff_roles(DIFF.splitlines()) == [
        "header", "header", "header", "header", "hunk",
        "", "removed", "", "added", "muted",
    ]
    assert summary("diff", DIFF) == "+1 −1 · 1 hunk"


def test_colorize_diff_styles_lines_and_finds_hunks():
    text, hunks = colorize("diff", DIFF)
    assert str(text) == DIFF.rstrip("\n")
    assert hunks == [4]
    styles = _styles(text)
    assert styles["+select 3;"] == DEFAULT_PALETTE["added"]
    assert styles["--- a comment line, removed"] == DEFAULT_PALETTE["removed"]
    assert styles["@@ -1,3 +1,3 @@"] == DEFAULT_PALETTE["hunk"]


def test_colorize_blame_marks_lines_not_committed_yet():
    blame = (
        "^a39739a old.txt (Someone 2026-10-18 1) one\n"
        "00000000 new.txt (Not Committed Yet 2026-10-18 2) four\n"
    )
    text, _ = colorize("blame", blame)
    assert str(text) == blame.rstrip("\n")
    styles = _styles(text)
    assert styles["^a39739a"] == DEFAULT_PALETTE["id"]
    assert styles["00000000 new.txt (Not Committed Yet 2026-10-18 2)"] == DEFAULT_PALETTE["pending"]
    assert summary("blame", blame) == "2 lines, 1 not committed"


def test_n_and_shift_n_jump_between_hunks():
    filler = "".join(f" line {i}\n" for i in range(60))
    diff = (
        "diff --git a/f b/f\n--- a/f\n+++ b/f\n"
        f"@@ -1 +1 @@\n+a\n{filler}@@ -80 +80 @@\n+b\n{filler}@@ -200 +200 @@\n+c\n{filler}"
    )
    hunk_lines = [3, 65, 127]

    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(DiffScreen("f", diff))

    async def scenario():
        app = Host()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = app.screen.query_one("#diff-log", RichLog)
            assert round(log.scroll_y) == 0
            await pilot.press("n")
            await pilot.pause()
            assert round(log.scroll_y) == hunk_lines[0]
            await pilot.press("n")
            await pilot.pause()
            assert round(log.scroll_y) == hunk_lines[1]
            await pilot.press("N")
            await pilot.pause()
            assert round(log.scroll_y) == hunk_lines[0]
            await pilot.press("G")
            await pilot.pause()
            assert log.scroll_y == log.max_scroll_y
            await pilot.press("g")
            await pilot.pause()
            assert log.scroll_y == 0

    asyncio.run(scenario())


def test_find_matches_uses_smart_case():
    lines = ["Foo foo", "bar FOO"]
    assert find_matches(lines, "foo") == [(0, 0), (0, 4), (1, 4)]
    assert find_matches(lines, "Foo") == [(0, 0)]
    assert find_matches(lines, "a.") == []  # literal, not a regex
    assert find_matches(lines, "") == []
    marked = highlight(Text("\n".join(lines)), lines, find_matches(lines, "Foo"), 3, "reverse")
    assert [(s.start, s.end, str(s.style)) for s in marked.spans] == [(0, 3, "reverse")]


def _two_files(filler_lines=40):
    filler = "".join(f" line {i}\n" for i in range(filler_lines))
    return (
        "diff --git a/one b/one\n--- a/one\n+++ b/one\n@@ -1 +1 @@\n+needle one\n"
        f"{filler}"
        "diff --git a/two b/two\n--- a/two\n+++ b/two\n@@ -1 +1 @@\n+needle two\n"
        f"{filler}"
    )


def _host(diff):
    class Host(App):
        def on_mount(self) -> None:
            self.push_screen(DiffScreen("f", diff))

    return Host()


def _title(app):
    return str(app.screen.query_one("#diff-title").render())


def test_brackets_jump_between_files():
    diff = _two_files()
    second = diff.splitlines().index("diff --git a/two b/two")

    async def scenario():
        app = _host(diff)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            log = app.screen.query_one("#diff-log", RichLog)
            assert "file" in _title(app)
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert round(log.scroll_y) == second and "file 2/2" in _title(app)
            await pilot.press("left_square_bracket")
            await pilot.pause()
            assert round(log.scroll_y) == 0 and "file 1/2" in _title(app)

    asyncio.run(scenario())


def test_search_highlights_and_n_walks_the_matches():
    diff = _two_files()
    lines = diff.splitlines()
    first, second = (i for i, line in enumerate(lines) if "needle" in line)

    async def scenario():
        app = _host(diff)
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            log = app.screen.query_one("#diff-log", RichLog)
            await pilot.press("slash", *"needle")
            await pilot.pause()
            assert "2 matches" in _title(app)
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert "match 1/2" in _title(app)
            assert round(log.scroll_y) == 0  # the first is already on screen
            await pilot.press("n")
            await pilot.pause()
            assert "match 2/2" in _title(app)
            assert round(log.scroll_y) <= second < round(log.scroll_y) + 18
            await pilot.press("n")  # wraps around
            await pilot.pause()
            assert "match 1/2" in _title(app) and round(log.scroll_y) <= first
            await pilot.press("N")
            await pilot.pause()
            assert "match 2/2" in _title(app)

            await pilot.press("escape")  # clears the search: n is hunks again
            await pilot.pause()
            await pilot.pause()
            assert "match" not in _title(app) and app.screen.is_modal
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, DiffScreen)

    asyncio.run(scenario())


def test_search_reports_nothing_found_and_esc_cancels_the_box():
    async def scenario():
        app = _host(_two_files())
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            await pilot.press("slash", *"zzz", "enter")
            await pilot.pause()
            assert "not found: zzz" in _title(app)
            await pilot.press("n")  # no search active: hunks
            await pilot.pause()
            assert "match" not in _title(app)
            await pilot.press("slash", "q")  # q types into the box, doesn't close
            await pilot.pause()
            assert isinstance(app.screen, DiffScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not app.screen.query_one("#diff-search").display
            assert isinstance(app.screen, DiffScreen)

    asyncio.run(scenario())
