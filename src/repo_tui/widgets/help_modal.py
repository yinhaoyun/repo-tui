"""The '?' help screen: full key reference."""

from __future__ import annotations

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

HELP_TEXT = """\
[b]repo-tui[/] — key reference

[b u]Navigation[/]
  ↑/↓, j/k         move selection in the focused pane
  tab / shift+tab  switch focus between left and right panes
  enter            open diff for the selected file (right pane)
  mouse            left-click a row to select it, scroll wheel to scroll
  right-click      open the project context menu (left pane)
  drag │           drag the divider between the panes to resize them

[b u]View & app[/]
  ?                toggle this help
  /                filter the project list by path/name
  a                toggle "show all" vs "hide unchanged" (default: hidden)
  i                toggle showing ignored files in the file list
  r                refresh status (parallel git status across all projects)
  q, ctrl+c        quit

[b u]Actions — direct (each asks before doing anything)[/]
  b                repo start — create a new branch (selected project)
  B                repo checkout — switch to an existing local branch
  c                copy the selected project's absolute path
  f                repo forall — run a shell command across projects

[b u]Action panel — press space, then a key (stays open until you choose)[/]
  space s          repo sync — selected project only
  space S          repo sync — entire tree
  space d          repo sync -d — detach selected project to manifest revision
  space b/B/c/f    same as the direct keys above
  esc / space      close the panel without doing anything
  right-click a project row for the per-project actions as a menu

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
