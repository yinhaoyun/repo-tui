import asyncio

from textual.app import App
from textual.widgets import RichLog

from repo_tui.widgets.diff_screen import DEFAULT_PALETTE, DiffScreen, colorize, diff_roles, summary

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


def test_colorize_log_keeps_the_columns_of_a_wide_author_name():
    log = (
        "d9bb678  2026-10-18  尹浩云            中文 commit\n"
        "878f3b9  2026-10-18  A Very Long Au..  rename\n"
    )
    text, _ = colorize("log", log)
    assert str(text) == log.rstrip("\n")
    styles = _styles(text)
    assert styles["尹浩云"] == DEFAULT_PALETTE["hunk"]
    assert styles["878f3b9"] == DEFAULT_PALETTE["id"]
    assert summary("log", log) == "2 commits"


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
