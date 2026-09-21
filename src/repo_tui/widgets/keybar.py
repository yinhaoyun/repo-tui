"""Bottom key-binding bar, tmux-style: a Ctrl+B leader reveals a second set
of bindings for actions (sync, forall, branch...), while the always-on keys
(navigation, filter, help, quit) show by default.
"""

from __future__ import annotations

from textual.widgets import Static

TOP_LEVEL_HINT = (
    "[b]^B[/] leader  [b]?[/] help  [b]/[/] filter  [b]a[/] show-all  "
    "[b]r[/] refresh  [b]tab[/] switch pane  [b]q[/] quit"
)

LEADER_HINT = (
    "[b yellow]^B…[/] [b]s[/] sync project  [b]S[/] sync all  "
    "[b]f[/] forall cmd  [b]b[/] start branch  [b]c[/] copy path  "
    "[b]esc[/] cancel"
)


class KeyBar(Static):
    def show_top_level(self) -> None:
        self.update(TOP_LEVEL_HINT)

    def show_leader(self) -> None:
        self.update(LEADER_HINT)
