"""Bottom key-binding bar: the always-on keys. Actions that run immediately
(sync, detach, sync all) live behind Space, which opens the ActionPanel
listing every action key.
"""

from __future__ import annotations

from textual.widgets import Static

TOP_LEVEL_HINT = (
    "[b]space[/] actions  [b]?[/] help │ "
    "[b]b[/]/[b]B[/] new/switch branch  [b]c[/] copy  [b]f[/] forall │ "
    "[b]/[/] filter  [b]a[/] all  [b]i[/] ignored  [b]r[/] refresh │ [b]q[/] quit"
)


class KeyBar(Static):
    def show_top_level(self) -> None:
        self.update(TOP_LEVEL_HINT)
