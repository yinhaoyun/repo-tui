"""The '?' help screen: full key reference."""

from __future__ import annotations

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..hints import key, tip

# (section title, [(keys, description)]); keys are shown highlighted.
HELP_SECTIONS = [
    ("Navigation", [
        ("↑/↓, j/k", "move selection in the focused pane"),
        ("tab / shift+tab", "switch focus between left and right panes"),
        ("enter", "open diff for the selected file (right pane)"),
        ("mouse", "left-click a row to select it, scroll wheel to scroll"),
        ("right-click", "open the project context menu (left pane)"),
        ("drag │", "drag the divider between the panes to resize them"),
    ]),
    ("Look — never changes a checkout (lowercase: this project, Shift: whole tree)", [
        ("r", "refresh the selected project (e.g. after editing it elsewhere)"),
        ("R", "refresh all (parallel git status across every project)"),
        ("w", "why each project counts as changed"),
        ("u", "last sync report: which projects got which commits"),
        ("y", "copy (yank) the selected project's absolute path"),
        ("/", "filter the project list by path/name"),
        ("a", 'toggle "show all" vs "hide unchanged" (default: hidden)'),
        ("i", "toggle showing ignored files in the file list"),
        ("o", "settings: theme, sync jobs, sort, auto-refresh, …"),
        ("?", "toggle this help"),
        ("q, ctrl+c", "quit"),
    ]),
    ("Change — press space, then a key (stays open until you choose)", [
        ("space s", "repo sync — selected project only"),
        ("space S", "repo sync — entire tree"),
        ("space d", "repo sync -d -l — detach to manifest revision (no fetch)"),
        ("space b", "repo start — create a new branch (asks for a name)"),
        ("space c", "checkout — switch to an existing local branch"),
        ("space x", "discard changes — revert to HEAD + delete untracked (asks y/N)"),
        ("space f", "repo forall — run a shell command across projects"),
        ("esc / space", "close the panel without doing anything"),
    ]),
]

_KEY_WIDTH = max(len(keys) for _, rows in HELP_SECTIONS for keys, _ in rows) + 2


def help_text() -> str:
    lines = ["[b]repo-tui[/] — key reference"]
    for title, rows in HELP_SECTIONS:
        lines += ["", f"[b u]{title}[/]"]
        lines += [
            "  " + key(keys) + " " * (_KEY_WIDTH - len(keys)) + desc for keys, desc in rows
        ]
    lines += [
        "",
        tip("Tip: right-click a project row for its actions as a menu."),
        tip('Tip: click "N changed" or "last sync" in the header for details.'),
        tip("Press any key to close this help."),
    ]
    return "\n".join(lines)


class HelpModal(ModalScreen[None]):
    def compose(self):
        yield Vertical(Static(help_text(), id="help-text"), id="help-box")

    def on_key(self, event) -> None:  # noqa: ANN001 - textual event type
        event.stop()
        event.prevent_default()
        self.dismiss(None)

    def on_click(self) -> None:
        self.dismiss(None)
