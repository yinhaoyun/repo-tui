"""Bottom key-binding bar: the always-on keys, none of which can change a
checkout. Everything that does (sync, detach, branch, checkout, discard,
forall, stage/unstage/revert a file) lives behind Space, which opens the
ActionPanel listing them. While the file list has focus, its keys come first.
"""

from __future__ import annotations

from textual.widgets import Static

from ..hints import key

# Groups of (keys, label), separated by │ on the bar.
TOP_LEVEL_GROUPS = [
    [(["space"], "actions"), (["?"], "help")],
    [(["r", "R"], "refresh"), (["w"], "why"), (["u"], "last sync"), (["y"], "copy")],
    [(["/"], "filter"), (["a"], "all"), (["i"], "ignored")],
    [(["o"], "settings"), (["v", "U"], "check/update"), (["q"], "quit")],
]


# Shown first while the file list has focus: the keys for the file at the
# cursor (stage/unstage/revert are in the Space panel's "This file" column).
FILE_GROUP = [
    (["enter"], "diff"), (["l"], "history"), (["b"], "blame"), (["e"], "edit"),
    (["y"], "copy path"), (["space"], "stage/unstage/revert"),
]


def _group_text(group) -> str:
    return "  ".join("/".join(key(k) for k in keys) + f" {label}" for keys, label in group)


def top_level_hint(file_focus: bool = False) -> str:
    if not file_focus:
        return " │ ".join(_group_text(g) for g in TOP_LEVEL_GROUPS)
    # The file keys first; leave out the general keys they take over here
    # (y copies the file's path, Space shows the file's actions).
    taken = {k for keys, _ in FILE_GROUP for k in keys}
    rest = [[(keys, label) for keys, label in g if not set(keys) & taken] for g in TOP_LEVEL_GROUPS]
    return " │ ".join(
        ["[b]file:[/] " + _group_text(FILE_GROUP)] + [_group_text(g) for g in rest if g]
    )


class KeyBar(Static):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._file_focus = False

    def show_top_level(self, file_focus: bool = False) -> None:
        self._file_focus = file_focus
        self.update(top_level_hint(file_focus))

    def refresh_hints(self) -> None:
        """Re-render in the current focus mode, e.g. after the hint style changed."""
        self.show_top_level(self._file_focus)
