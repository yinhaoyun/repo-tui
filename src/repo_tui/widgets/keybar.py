"""Bottom key-binding bar: the always-on keys, none of which can change a
checkout. Everything that does (sync, detach, branch, checkout, discard,
forall) lives behind Space, which opens the ActionPanel listing them.
"""

from __future__ import annotations

from textual.widgets import Static

from ..hints import key

# Groups of (keys, label), separated by │ on the bar.
TOP_LEVEL_GROUPS = [
    [(["space"], "actions"), (["?"], "help")],
    [(["r", "R"], "refresh"), (["w"], "why"), (["u"], "last sync"), (["y"], "copy")],
    [(["/"], "filter"), (["a"], "all"), (["i"], "ignored")],
    [(["o"], "settings"), (["q"], "quit")],
]


def top_level_hint() -> str:
    return " │ ".join(
        "  ".join("/".join(key(k) for k in keys) + f" {label}" for keys, label in group)
        for group in TOP_LEVEL_GROUPS
    )


class KeyBar(Static):
    def show_top_level(self) -> None:
        self.update(top_level_hint())
