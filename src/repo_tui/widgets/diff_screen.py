"""A modal screen showing the full `git diff` output for one file (also used
for a commit's patch and a file's blame), coloured in the theme's colours:
added and removed lines, hunk headers, commit ids, and blame lines that
aren't committed yet. n/N jump between a diff's hunks, ]/[ between its
files; / searches, and then n/N jump between the matches."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from ..markup import escape
from rich.text import Text
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, RichLog, Static

from ..hints import close_tip, key

# Role -> Rich style; DiffScreen fills the colours in from the theme.
Palette = Dict[str, str]
DEFAULT_PALETTE: Palette = {
    "added": "green",
    "removed": "red",
    "hunk": "cyan",
    "header": "bold",
    "id": "yellow",
    "muted": "dim",
    "pending": "bold yellow",
}

# git blame --date=short: "<sha> [<orig path>] (<author> <date> <line>) <code>"
_BLAME_RE = re.compile(r"^(\^?[0-9a-f]+)( [^(]*?)? ?(\(.*?\d{4}-\d\d-\d\d +\d+\))(.*)$")
_DIFF_HEADERS = ("diff --git ", "index ", "--- ", "+++ ", "new file mode", "deleted file mode",
                 "similarity index", "rename from", "rename to", "old mode", "new mode")


def diff_roles(lines: List[str]) -> List[str]:
    """Each diff line's role: "header", "hunk", "added", "removed", "muted"
    or "". Header lines only count as such between `diff --git` and the
    first @@, so a removed line reading "-- x" ("--- x") stays removed."""
    roles, in_header = [], True
    for line in lines:
        if line.startswith("diff --git "):
            in_header = True
        if line.startswith("@@"):
            in_header = False
            roles.append("hunk")
        elif in_header and line.startswith(_DIFF_HEADERS):
            roles.append("header")
        elif not in_header and line.startswith("+"):
            roles.append("added")
        elif not in_header and line.startswith("-"):
            roles.append("removed")
        elif line.startswith("\\"):  # "\ No newline at end of file"
            roles.append("muted")
        else:
            roles.append("")
    return roles


def _blame_line(line: str, palette: Palette) -> Text:
    m = _BLAME_RE.match(line)
    if not m:
        return Text(line)
    sha, orig, meta, code = m.groups()
    if set(sha.lstrip("^")) == {"0"}:  # not committed yet
        return Text.assemble((sha + (orig or "") + " " + meta, palette["pending"]), code)
    return Text.assemble(
        (sha, palette["id"]), (orig or "") + " ", (meta, palette["muted"]), code
    )


def theme_palette(app) -> Palette:  # noqa: ANN001 - a Textual App
    """DEFAULT_PALETTE's roles in the current theme's text colours, so a
    light theme gets colours readable on a light background."""
    variables = app.get_css_variables()
    palette = dict(DEFAULT_PALETTE)
    for role, variable in (
        ("added", "text-success"),
        ("removed", "text-error"),
        ("hunk", "text-primary"),
        ("id", "text-warning"),
    ):
        colour = variables.get(variable, "")
        if colour.startswith("#"):
            palette[role] = colour
    if palette["id"].startswith("#"):
        palette["pending"] = f"bold {palette['id']}"
    return palette


def colorize(kind: str, text: str, palette: Palette = DEFAULT_PALETTE) -> Tuple[Text, List[int]]:
    """`text` as one Rich Text styled for `kind` ("diff", "blame";
    anything else is left plain), and the line numbers of a diff's hunks."""
    lines = text.splitlines()
    if kind == "diff":
        roles = diff_roles(lines)
        styled = [
            Text(line, style=palette[role]) if role else Text(line)
            for line, role in zip(lines, roles)
        ]
        return Text("\n").join(styled), [i for i, r in enumerate(roles) if r == "hunk"]
    if kind != "blame":
        return Text(text), []
    return Text("\n").join(_blame_line(line, palette) for line in lines), []


def find_matches(lines: List[str], query: str) -> List[Tuple[int, int]]:
    """(line, column) of every occurrence of `query`, case-insensitive
    unless it has a capital letter (smart case, as in vim and less)."""
    if not query:
        return []
    flags = 0 if any(c.isupper() for c in query) else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)
    return [(i, m.start()) for i, line in enumerate(lines) for m in pattern.finditer(line)]


def highlight(text: Text, lines: List[str], found: List[Tuple[int, int]], length: int,
              style: str) -> Text:
    """A copy of `text` (lines joined by newlines) with each match styled."""
    starts, offset = [], 0
    for line in lines:
        starts.append(offset)
        offset += len(line) + 1
    marked = text.copy()
    for line, column in found:
        start = starts[line] + column
        marked.stylize(style, start, start + length)
    return marked


def summary(kind: str, text: str) -> str:
    """A short count for the title: "+12 −3 · 2 hunks", "120 lines", …"""
    lines = text.splitlines()
    if kind == "diff":
        roles = diff_roles(lines)
        added, removed, hunks = (roles.count(r) for r in ("added", "removed", "hunk"))
        return f"+{added} −{removed} · {hunks} hunk{'s' if hunks != 1 else ''}"
    if kind == "blame":
        pending = sum(
            1 for line in lines
            if (m := _BLAME_RE.match(line)) and set(m.group(1).lstrip("^")) == {"0"}
        )
        return f"{len(lines)} lines" + (f", {pending} not committed" if pending else "")
    return ""


class DiffScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "escape", "Close"),
        ("q", "dismiss_screen", "Close"),
        ("n", "next_hunk", "Next hunk"),
        ("N", "prev_hunk", "Previous hunk"),
        ("right_square_bracket", "next_file", "Next file"),
        ("left_square_bracket", "prev_file", "Previous file"),
        ("slash", "search", "Search"),
        ("j", "scroll('down')", "Down"),
        ("k", "scroll('up')", "Up"),
        ("g", "scroll('home')", "Top"),
        ("G", "scroll('end')", "Bottom"),
        ("ctrl+d", "scroll('half_down')", "Half page down"),
        ("ctrl+u", "scroll('half_up')", "Half page up"),
    ]

    def __init__(
        self,
        title: str,
        diff_text: str,
        empty: str = "(no textual diff — binary file or no changes)",
        kind: str = "diff",
    ) -> None:
        self._title = title
        self._diff_text = diff_text
        self._empty = empty
        self._kind = kind
        self._hunks: list[int] = []
        self._lines = diff_text.splitlines()
        self._files = [i for i, line in enumerate(self._lines) if line.startswith("diff --git ")]
        self._text = Text()
        self._query = ""
        self._found: List[Tuple[int, int]] = []
        self._at = -1  # index in _found of the last match jumped to
        super().__init__()

    def _heading(self, status: str = "") -> str:
        count = summary(self._kind, self._diff_text) if self._diff_text else ""
        title = f"[b]{escape(self._title)}[/]" + (f"  [dim]{count}[/]" if count else "")
        nav = ""
        if self._diff_text:
            if self._hunks:
                nav += f"{key('n')}/{key('N')} hunk  "
            if len(self._files) > 1:
                nav += f"{key(']')}/{key('[')} file  "
            nav += f"{key('/')} search  "
        if status:
            title += f"  [b $text-warning]{escape(status)}[/]"
        return f"{title}  {nav}{close_tip('esc', 'q')}"

    def compose(self):
        if self._kind == "diff":
            self._hunks = [i for i, r in enumerate(diff_roles(self._lines)) if r == "hunk"]
        with Vertical(id="diff-box"):
            yield Static(self._heading(), id="diff-title")
            yield RichLog(
                id="diff-log", wrap=False, highlight=False, markup=False, auto_scroll=False
            )
            yield Input(placeholder="search (smart case) — enter: find, esc: cancel",
                        id="diff-search")

    def on_mount(self) -> None:
        self.query_one("#diff-search", Input).display = False
        log = self.query_one("#diff-log", RichLog)
        if not self._diff_text:
            log.write(self._empty)
            return
        self._text, self._hunks = colorize(self._kind, self._diff_text, theme_palette(self.app))
        log.write(self._text)

    def _jump(self, forward: bool) -> None:
        log = self.query_one("#diff-log", RichLog)
        here = round(log.scroll_y)
        targets = [i for i in self._hunks if (i > here if forward else i < here)]
        if forward and log.scroll_y >= log.max_scroll_y:
            targets = []  # the rest is already on screen
        if not targets:
            self.notify("No more hunks" if self._hunks else "No hunks here", timeout=2)
            return
        log.scroll_to(y=targets[0] if forward else targets[-1], animate=False)

    def action_next_hunk(self) -> None:
        if self._query:
            self._jump_match(forward=True)
        else:
            self._jump(forward=True)

    def action_prev_hunk(self) -> None:
        if self._query:
            self._jump_match(forward=False)
        else:
            self._jump(forward=False)

    # -- files ---------------------------------------------------------------

    def _jump_file(self, forward: bool) -> None:
        if len(self._files) < 2:
            self.notify("Only one file here" if self._files else "No files here", timeout=2)
            return
        log = self.query_one("#diff-log", RichLog)
        here = round(log.scroll_y)
        targets = [i for i in self._files if (i > here if forward else i < here)]
        if forward and log.scroll_y >= log.max_scroll_y:
            targets = []
        if not targets:
            self.notify("No more files", timeout=2)
            return
        target = targets[0] if forward else targets[-1]
        log.scroll_to(x=0, y=target, animate=False)
        number = self._files.index(target) + 1
        self._set_status(f"file {number}/{len(self._files)}")

    def action_next_file(self) -> None:
        self._jump_file(forward=True)

    def action_prev_file(self) -> None:
        self._jump_file(forward=False)

    # -- search --------------------------------------------------------------

    def _set_status(self, status: str = "") -> None:
        self.query_one("#diff-title", Static).update(self._heading(status))

    def action_search(self) -> None:
        if not self._diff_text:
            return
        field = self.query_one("#diff-search", Input)
        field.value = self._query
        field.display = True
        field.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.value:
            count = len(find_matches(self._lines, event.value))
            self._set_status(f"{count} match{'es' if count != 1 else ''}")
        else:
            self._set_status()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._close_search()
        self._search(event.value)

    def _close_search(self) -> None:
        field = self.query_one("#diff-search", Input)
        field.display = False
        self.query_one("#diff-log", RichLog).focus()

    def _search(self, query: str) -> None:
        """Highlight every match of `query` (empty: clear) and jump to the
        first one from the top of the view on."""
        self._query = query
        self._found = find_matches(self._lines, query)
        self._at = -1
        log = self.query_one("#diff-log", RichLog)
        x, y = log.scroll_x, log.scroll_y
        log.clear()
        if self._found:
            log.write(highlight(self._text, self._lines, self._found, len(query),
                                "reverse bold"))
        else:
            log.write(self._text)

        def restore() -> None:  # rewriting the log loses its place
            log.scroll_to(x=x, y=y, animate=False)
            if self._found:
                self._jump_match(forward=True)

        self.call_after_refresh(restore)
        if not query:
            self._set_status()
        elif not self._found:
            self._query = ""
            self._set_status(f"not found: {query}")

    def _jump_match(self, forward: bool) -> None:
        """The next/previous match: from the last one jumped to while it's
        still on screen, else from the top of the view; wraps around."""
        log = self.query_one("#diff-log", RichLog)
        top = round(log.scroll_y)
        height = max(1, log.scrollable_content_region.height)
        lines = [line for line, _ in self._found]
        if 0 <= self._at < len(lines) and top <= lines[self._at] < top + height:
            index = self._at + (1 if forward else -1)
        elif forward:
            index = next((i for i, line in enumerate(lines) if line >= top), len(lines))
        else:
            index = max((i for i, line in enumerate(lines) if line < top), default=-1)
        if not 0 <= index < len(lines):
            index = 0 if forward else len(lines) - 1
            self.notify("Search wrapped", timeout=2)
        self._at = index
        line, column = self._found[index]
        width = max(1, log.scrollable_content_region.width)
        x = 0 if column + len(self._query) < width else column - width // 3
        y = top if top <= line < top + height else max(0, line - 3)  # still if visible
        log.scroll_to(x=x, y=y, animate=False)
        self._set_status(f"match {index + 1}/{len(lines)}: {self._query}")

    def action_escape(self) -> None:
        """esc: cancel the search box; else clear a search; else close."""
        field = self.query_one("#diff-search", Input)
        if field.display:
            self._close_search()
            self._set_status(f"match {self._at + 1}/{len(self._found)}: {self._query}"
                             if self._query else "")
        elif self._query:
            self._search("")
        else:
            self.dismiss(None)

    def action_scroll(self, where: str) -> None:
        log = self.query_one("#diff-log", RichLog)
        half = max(1, log.scrollable_content_region.height // 2)
        if where == "down":
            log.scroll_relative(y=1, animate=False)
        elif where == "up":
            log.scroll_relative(y=-1, animate=False)
        elif where == "home":
            log.scroll_home(animate=False)
        elif where == "end":
            log.scroll_end(animate=False)
        elif where == "half_down":
            log.scroll_relative(y=half, animate=False)
        elif where == "half_up":
            log.scroll_relative(y=-half, animate=False)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
