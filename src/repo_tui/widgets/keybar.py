"""Bottom key bar, two lines so the two kinds of keys never blur together:

  - the focus line: a coloured label naming what has focus (project list,
    file list, filter) and the keys that act on it. It changes as focus
    moves; the same key can mean something else elsewhere (y copies the
    project's path in the project list, the file's in the file list).
  - the global line: a muted GLOBAL label and the keys that work wherever
    focus is.

None of these keys can change a checkout. Everything that does (sync,
detach, branch, checkout, discard, forall, stage/unstage/revert a file)
lives behind Space, which opens the ActionPanel listing them.
"""

from __future__ import annotations

from textual.widgets import Static

from ..hints import key

Group = list[tuple[list[str], str]]

# Keys that work wherever focus is, as groups separated by │.
GLOBAL_GROUPS: list[Group] = [
    [(["space"], "actions"), (["?"], "help")],
    [(["r", "R"], "refresh"), (["w"], "why"), (["u"], "last sync")],
    [(["/"], "filter"), (["a"], "all"), (["i"], "ignored")],
    [(["o"], "settings"), (["v", "U"], "check/update"), (["q"], "quit")],
]

# Focus contexts: (label, keys) for whatever has focus.
FOCUS_CONTEXTS: dict[str, tuple[str, Group]] = {
    "projects": ("PROJECT LIST", [
        (["↑↓"], "select"), (["tab"], "to files"), (["y"], "copy project path"),
        (["right-click"], "menu"),
    ]),
    "files": ("FILE LIST", [
        (["↑↓"], "select"), (["enter"], "diff"), (["l"], "history"), (["b"], "blame"),
        (["e"], "edit"), (["y"], "copy file path"), (["space"], "+ stage/unstage/revert"),
        (["tab"], "to projects"),
    ]),
    "filter": ("FILTER", [
        (["type"], "to filter by path/name"), (["enter"], "keep it"), (["esc"], "clear"),
    ]),
}


def _group_text(group: Group) -> str:
    return "  ".join("/".join(key(k) for k in keys) + f" {label}" for keys, label in group)


def focus_hint(context: str) -> str:
    label, group = FOCUS_CONTEXTS[context]
    return f"[b $background on $accent] {label} [/] " + _group_text(group)


def global_hint() -> str:
    return "[b $text-muted on $surface] GLOBAL [/] " + " │ ".join(
        _group_text(g) for g in GLOBAL_GROUPS
    )


def top_level_hint(context: str = "projects") -> str:
    return focus_hint(context) + "\n" + global_hint()


class KeyBar(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.context = "projects"

    def show_top_level(self, context: str | None = None) -> None:
        """Re-render; with `context`, for that focus context."""
        if context is not None:
            self.context = context
        self.update(top_level_hint(self.context))
