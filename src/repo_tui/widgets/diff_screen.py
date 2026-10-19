"""A modal screen showing the full `git diff` output for one file (also used
for a commit's patch and a file's blame), coloured in the theme's colours:
added and removed lines, hunk headers, commit ids, and blame lines that
aren't committed yet. n/N jump between a diff's hunks."""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from rich.markup import escape
from rich.text import Text
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static

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
        ("escape", "dismiss_screen", "Close"),
        ("q", "dismiss_screen", "Close"),
        ("n", "next_hunk", "Next hunk"),
        ("N", "prev_hunk", "Previous hunk"),
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
        super().__init__()

    def compose(self):
        count = summary(self._kind, self._diff_text) if self._diff_text else ""
        title = f"[b]{escape(self._title)}[/]" + (f"  [dim]{count}[/]" if count else "")
        nav = ""
        if self._kind == "diff" and "\n@@" in "\n" + self._diff_text:
            nav = f"{key('n')}/{key('N')} hunk  "
        with Vertical(id="diff-box"):
            yield Static(f"{title}  {nav}{close_tip('esc', 'q')}", id="diff-title")
            yield RichLog(
                id="diff-log", wrap=False, highlight=False, markup=False, auto_scroll=False
            )

    def on_mount(self) -> None:
        log = self.query_one("#diff-log", RichLog)
        if not self._diff_text:
            log.write(self._empty)
            return
        text, self._hunks = colorize(self._kind, self._diff_text, theme_palette(self.app))
        log.write(text)

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
        self._jump(forward=True)

    def action_prev_hunk(self) -> None:
        self._jump(forward=False)

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
