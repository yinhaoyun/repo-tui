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
        ("click", "on a file row: open its diff"),
        ("mouse", "left-click a row to select it, scroll wheel to scroll"),
        ("drag │", "drag the divider between the panes to resize them"),
    ]),
    ("Project list — while it has focus", [
        ("y", "copy (yank) the selected project's absolute path"),
        ("enter, shift+F10", "the project's actions as a menu (also right-click)"),
    ]),
    ("Global — anywhere; never changes a checkout (lowercase: this project, Shift: whole tree)", [
        ("r", "refresh the selected project (e.g. after editing it elsewhere)"),
        ("R", "refresh all (parallel git status across every project)"),
        ("w", "why each project counts as changed"),
        ("u", "last sync report: which projects got which commits"),
        ("/", "filter the project list by path/name"),
        ("a", 'toggle "show all" vs "hide unchanged" (default: hidden)'),
        ("i", "toggle showing ignored files in the file list"),
        ("o", "settings: theme, sync jobs, sort, auto-refresh, …"),
        ("v", "version: check GitHub for a newer repo-tui now (and say what's new)"),
        ("U", "update repo-tui itself from GitHub (asks first; never touches the tree)"),
        ("?", "toggle this help"),
        ("q, ctrl+c", "quit"),
    ]),
    ("File list — while it has focus (tab to it), on the file at the cursor", [
        ("enter, shift+F10", "the file's actions as a menu (also right-click)"),
        ("click", "diff since HEAD (staged + unstaged; untracked: whole file)"),
        ("l", "history: commits that touched the file (follows renames)"),
        ("b", "blame"),
        ("e", "open in $VISUAL / $EDITOR (vi if unset), then rescan"),
        ("y", "copy the file's absolute path"),
        ("space a/u/r", "stage / unstage / revert the file (revert asks y/N)"),
    ]),
    ("Change — press space, then a key (stays open until you choose)", [
        ("space s", "repo sync — selected project only"),
        ("space S", "repo sync — entire tree"),
        ("space d", "repo sync -d -l — detach to manifest revision (no fetch)"),
        ("space b", "repo start — create a new branch (asks for a name)"),
        ("space c", "checkout — switch to an existing local branch"),
        ("space k", "repo abandon — delete a local branch of this project (asks y/N)"),
        ("space K", "repo abandon — delete a branch in every project that has it (asks y/N)"),
        ("space x", "discard changes — revert to HEAD + delete untracked (asks y/N)"),
        ("space h", "unshallow — fetch the selected project's full history (asks y/N)"),
        ("space f", "repo forall — run a shell command across projects"),
        ("space M", "switch the tree's manifest branch/file (repo init -b -m + sync all)"),
        ("esc / space", "close the panel without doing anything"),
    ]),
]

_KEY_WIDTH = max(len(keys) for _, rows in HELP_SECTIONS for keys, _ in rows) + 2


def help_text() -> str:
    lines = [
        "[b]repo-tui[/] — key reference",
        "[dim]The bottom bar shows the keys for what has focus on its top line "
        "(it changes as you move), and the global keys below it.[/]",
    ]
    for title, rows in HELP_SECTIONS:
        lines += ["", f"[b u]{title}[/]"]
        lines += [
            "  " + key(keys) + " " * (_KEY_WIDTH - len(keys)) + desc for keys, desc in rows
        ]
    lines += [
        "",
        tip("Tip: enter, shift+F10 or right-click on a project or file opens its menu."),
        tip('Tip: click "N changed", "last sync" or "⬆ repo-tui update" in the header.'),
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
