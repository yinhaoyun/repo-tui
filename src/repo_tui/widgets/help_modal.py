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
        ("l", "history: the project's commits — messages, files, enter for the diff"),
        ("y", "copy (yank) the selected project's absolute path"),
        ("m", "mark / unmark the project (and move down); space actions then act on the marks"),
        ("M, esc", "M: mark every shown project, or clear the marks; esc: clear them"),
        ("enter, shift+F10", "the project's actions as a menu (also right-click)"),
    ]),
    ("Global — anywhere; never changes a checkout (lowercase: this project, Shift: whole tree)", [
        ("ctrl+p", "command palette: find any action by name and run it"),
        ("r", "refresh the selected project (e.g. after editing it elsewhere)"),
        ("R", "refresh all (parallel git status across every project)"),
        ("w", "why each project counts as changed"),
        ("u", "last sync report: which projects got which commits"),
        ("/", "filter the project list by path/name"),
        ("f", "show only: uncommitted, unpushed, behind, on a branch, detached + changes, errors"),
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
        ("l", "history: commits that touched the file (follows renames), enter: diff"),
        ("b", "blame"),
        ("e", "open in $VISUAL / $EDITOR (vi if unset), then rescan"),
        ("y", "copy the file's absolute path"),
        ("space a/u/r", "stage / unstage / revert the file (revert asks y/N)"),
    ]),
    ("History view (l)", [
        ("↑↓, enter", "pick a commit (its message and files show on the right); its diff"),
        ("/", "filter by sha, subject, author, ref or date (esc clears)"),
        ("y", "copy the commit's sha"),
        ("↑ (in the list)", "a commit upstream doesn't have (not pushed / not merged)"),
    ]),
    ("Diff and blame views", [
        ("n / N", "next / previous hunk (diff); after a search, next / previous match"),
        ("] / [", "next / previous file (a diff of several files)"),
        ("/", "search (smart case), enter: find; esc clears the search"),
        ("j/k, g/G", "scroll a line, jump to the top / bottom"),
        ("ctrl+d / ctrl+u", "half a page down / up"),
        ("esc, q", "close (esc first clears a search)"),
    ]),
    ("Change — press space, then a key (stays open until you choose)", [
        ("space s", "repo sync — selected project only"),
        ("space S", "repo sync — entire tree"),
        ("space F", "repo sync -n — fetch all, check nothing out, see what's incoming"),
        ("space d", "repo sync -d -l — detach to manifest revision (no fetch)"),
        ("space b", "repo start — create a new branch (asks for a name)"),
        ("space c", "checkout — switch to an existing local branch"),
        ("space k", "repo abandon — delete a local branch of this project (asks y/N)"),
        ("space K", "repo abandon — delete a branch in every project that has it (asks y/N)"),
        ("space x", "discard changes — revert to HEAD + delete untracked (asks y/N)"),
        ("space h", "unshallow — fetch the selected project's full history (asks y/N)"),
        ("space f", "repo forall — a command (↑/↓: earlier ones), then: all / this / changed"),
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
        tip("Scroll with ↑↓ / j/k, PgUp/PgDn, g/G; press any other key to close."),
    ]
    return "\n".join(lines)


# key -> how the help scrolls; any other key closes it.
_SCROLL_KEYS = {
    "up": ("line", -1), "k": ("line", -1),
    "down": ("line", 1), "j": ("line", 1),
    "pageup": ("half", -1), "ctrl+u": ("half", -1),
    "pagedown": ("half", 1), "ctrl+d": ("half", 1),
    "home": ("end", -1), "g": ("end", -1),
    "end": ("end", 1), "G": ("end", 1),
}


class HelpModal(ModalScreen[None]):
    """Scrolls with the keys in _SCROLL_KEYS (and the mouse wheel), so it
    can be read on a short terminal; any other key, or a click outside the
    box, closes it."""

    def compose(self):
        box = Vertical(Static(help_text(), id="help-text"), id="help-box")
        box.border_subtitle = (
            f"{key('↑↓ j/k')} scroll  {key('g/G')} top/end  any other key closes"
        )
        yield box

    def on_key(self, event) -> None:  # noqa: ANN001 - textual event type
        event.stop()
        event.prevent_default()
        how = _SCROLL_KEYS.get(event.character if event.character in ("g", "G") else event.key)
        if how is None:
            self.dismiss(None)
            return
        box = self.query_one("#help-box")
        kind, direction = how
        if kind == "line":
            box.scroll_relative(y=direction, animate=False)
        elif kind == "half":
            half = max(1, box.scrollable_content_region.height // 2)
            box.scroll_relative(y=direction * half, animate=False)
        elif direction < 0:
            box.scroll_home(animate=False)
        else:
            box.scroll_end(animate=False)

    def on_click(self, event) -> None:  # noqa: ANN001 - textual event type
        if event.widget is self:  # the dimmed background, not the box or its scrollbar
            self.dismiss(None)
