"""Bottom key-binding bar: the always-on keys. Actions that run immediately
(sync, detach, sync all) live behind Space, which opens the ActionPanel
listing every action key.
"""

from __future__ import annotations

from textual.widgets import Static

from ..hints import key

# Groups of (keys, label), separated by │ on the bar.
TOP_LEVEL_GROUPS = [
    [(["space"], "actions"), (["?"], "help")],
    [(["b", "B"], "branch"), (["c"], "copy"), (["f"], "forall")],
    [(["/"], "filter"), (["a"], "all"), (["i"], "ignored"), (["r", "R"], "refresh")],
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
