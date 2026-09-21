"""The '?' help screen: full key reference."""

from __future__ import annotations

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

HELP_TEXT = """\
[b]repo-tui[/] — key reference

[b u]Navigation[/]
  ↑/↓, j/k        move selection in the focused pane
  tab / shift+tab  switch focus between left and right panes
  enter            open diff for the selected file (right pane)
  mouse            click a row to select it, scroll wheel to scroll

[b u]Global[/]
  ?                toggle this help
  /                filter the project list by path/name
  a                toggle "show all" vs "hide unchanged" (default: hidden)
  i                toggle showing ignored files in the file list
  r                refresh status (parallel git status across all projects)
  tab              switch focus between left/right pane
  q, ctrl+c        quit

[b u]Leader actions (tmux-style: press ctrl+b, then a key)[/]
  ctrl+b s         repo sync — selected project only
  ctrl+b S         repo sync — entire tree
  ctrl+b f         repo forall — run a shell command across projects
  ctrl+b b         repo start — create/checkout a branch (selected project)
  ctrl+b c         copy the selected project's absolute path
  esc              cancel leader mode

Press any key to close this help.
"""


class HelpModal(ModalScreen[None]):
    def compose(self):
        yield Vertical(Static(HELP_TEXT, id="help-text"), id="help-box")

    def on_key(self, event) -> None:  # noqa: ANN001 - textual event type
        event.stop()
        event.prevent_default()
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)
