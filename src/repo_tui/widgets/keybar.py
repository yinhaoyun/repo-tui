"""Bottom key bar, two lines so the two kinds of keys never blur together:

  - the focus line: a coloured label naming what has focus (project list,
    file list, filter) and the keys that act on it. It changes as focus
    moves; the same key can mean something else elsewhere (y copies the
    project's path in the project list, the file's in the file list).
  - the global line: a muted GLOBAL label and the keys that work wherever
    focus is.

Each line fits the bar's width: when the terminal is too narrow, the least
needed keys go first (rank 3, then 2), rightmost first, then alternative
keys (enter/shift+f10 -> enter), and a "…" says some are hidden. Rank 1
keys always stay -- including ? (help) and ctrl+p (commands), which lead
to everything that was dropped.

None of these keys can change a checkout. Everything that does (sync,
detach, branch, checkout, abandon, discard, forall, stage/unstage/revert
a file) lives behind Space, which opens the ActionPanel listing them.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from rich.cells import cell_len
from textual.widgets import Static

from ..hints import key

# (keys, label, rank): rank 1 always shows, 3 is the first to go.
Item = Tuple[List[str], str, int]
Group = List[Item]

# Keys that work wherever focus is, as groups separated by │.
GLOBAL_GROUPS: list[Group] = [
    [(["space"], "actions", 1), (["ctrl+p"], "commands", 1), (["?"], "help", 1)],
    [(["r", "R"], "refresh", 2), (["w"], "why", 3), (["u"], "last sync", 3)],
    [(["/"], "filter", 2), (["f"], "show only", 2), (["a"], "all", 2), (["i"], "ignored", 3)],
    [(["o"], "settings", 3), (["v", "U"], "check/update", 3), (["q"], "quit", 1)],
]

# Focus contexts: (label, keys) for whatever has focus.
FOCUS_CONTEXTS: dict[str, tuple[str, Group]] = {
    "projects": ("PROJECT LIST", [
        (["↑↓"], "select", 3), (["enter", "shift+f10"], "menu", 1), (["l"], "log", 2),
        (["m"], "mark", 2),
        (["M"], "all/none", 3), (["tab"], "to files", 2), (["y"], "copy project path", 3),
    ]),
    "files": ("FILE LIST", [
        (["↑↓"], "select", 3), (["enter", "shift+f10"], "menu", 1), (["click"], "diff", 3),
        (["l"], "history", 2), (["b"], "blame", 2),
        (["e"], "edit", 2), (["y"], "copy file path", 3),
        (["space"], "+ stage/unstage/revert", 1), (["tab"], "to projects", 2),
    ]),
    "filter": ("FILTER", [
        (["type"], "to filter by path/name", 2), (["enter"], "keep it", 1),
        (["esc"], "clear", 1),
    ]),
}

_ITEM_GAP, _GROUP_GAP, _MORE = "  ", " │ ", " …"


def _item_width(item: Item) -> int:
    keys, label, _ = item
    return cell_len("/".join(keys)) + 1 + cell_len(label)


def _line_width(label: str, groups: list[Group], dropped: bool) -> int:
    shown = [g for g in groups if g]
    width = cell_len(label) + 3  # " LABEL " and a space
    width += sum(_item_width(item) for g in shown for item in g)
    width += sum(len(_ITEM_GAP) * (len(g) - 1) for g in shown)
    width += len(_GROUP_GAP) * max(0, len(shown) - 1)
    return width + (len(_MORE) if dropped else 0)


def _fit(label: str, groups: list[Group], width: Optional[int]) -> tuple[list[Group], bool]:
    """`groups` minus the least needed items until the line fits `width`
    (None: no limit). Returns the groups left and whether any were dropped."""
    groups = [list(g) for g in groups]
    dropped = False
    while width and _line_width(label, groups, dropped) > width:
        candidates = [
            (rank, gi, ii)
            for gi, g in enumerate(groups)
            for ii, (_, _, rank) in enumerate(g)
            if rank > 1
        ]
        if candidates:
            _, gi, ii = max(candidates)
            del groups[gi][ii]
            dropped = True
            continue
        # Only must-haves left: show just the first of alternative keys
        # (enter/shift+f10 -> enter); past that, the bar clips the rest.
        trimmed = [[(keys[:1], text, rank) for keys, text, rank in g] for g in groups]
        if trimmed == groups:
            break
        groups, dropped = trimmed, True
    return groups, dropped


def _line(label_markup: str, label: str, groups: list[Group], width: Optional[int]) -> str:
    groups, dropped = _fit(label, groups, width)
    body = _GROUP_GAP.join(
        _ITEM_GAP.join("/".join(key(k) for k in keys) + f" {text}" for keys, text, _ in g)
        for g in groups
        if g
    )
    return f"{label_markup} {body}" + (f"[dim]{_MORE}[/]" if dropped else "")


def focus_hint(context: str, width: Optional[int] = None, marked: int = 0) -> str:
    """The focus line; with `marked` projects, its label says so (Space's
    project actions then act on them), and esc clears them."""
    label, group = FOCUS_CONTEXTS[context]
    if marked:
        label += f" · {marked} marked"
        if context == "projects":
            group = [(["esc"], "unmark", 1)] + group
    return _line(f"[b $background on $accent] {label} [/]", label, [group], width)


def global_hint(width: Optional[int] = None) -> str:
    return _line("[b $text-muted on $surface] GLOBAL [/]", "GLOBAL", GLOBAL_GROUPS, width)


def top_level_hint(
    context: str = "projects", width: Optional[int] = None, marked: int = 0
) -> str:
    return focus_hint(context, width, marked) + "\n" + global_hint(width)


class KeyBar(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.context = "projects"
        self.marked = 0

    def show_top_level(self, context: str | None = None, marked: int | None = None) -> None:
        """Re-render for the bar's width; with `context`, for that focus
        context; with `marked`, for that many marked projects."""
        if context is not None:
            self.context = context
        if marked is not None:
            self.marked = marked
        self.update(top_level_hint(self.context, self.size.width or None, self.marked))

    def on_resize(self) -> None:
        self.show_top_level()
