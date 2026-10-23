"""Action mode: pressing Space opens this overlay, a boxed panel listing every
action key grouped by scope. It is one-shot — an action key dismisses it with
that action's id; Esc or Space again dismisses it with None. It stays open
until one of those happens (no timeout)."""

from __future__ import annotations

from typing import Optional

from ..markup import escape
from textual import events
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..hints import key, tip

# (key, action id, label) — split by what the action touches. Every action
# here changes a checkout, or (fetch) is a long trip to the server that a
# stray key shouldn't start; lowercase keys act on the selected project,
# Shift on the whole tree (s/S, k/K, F, M), matching r/R on the top layer.
# The file actions are only offered while the file list has focus, on the
# file at its cursor.
FILE_ACTIONS = [
    ("a", "stage_file", "stage (git add)"),
    ("u", "unstage_file", "unstage"),
    ("r", "revert_file", "revert changes…"),
]
PROJECT_ACTIONS = [
    ("s", "sync_selected", "sync"),
    ("d", "detach", "detach (sync -d -l)"),
    ("b", "start_branch", "start new branch"),
    ("c", "switch_branch", "checkout branch"),
    ("k", "abandon_branch", "abandon (delete) branch…"),
    ("x", "discard", "discard changes…"),
    ("h", "unshallow", "unshallow (full history)…"),
]
TREE_ACTIONS = [
    ("S", "sync_all", "sync all"),
    ("F", "fetch_all", "fetch all (sync -n), see incoming"),
    ("K", "abandon_tree", "abandon branch everywhere…"),
    ("f", "forall", "forall <cmd>"),
    ("M", "switch_manifest", "switch manifest…"),
]
ACTION_KEYS = {key: action for key, action, _ in PROJECT_ACTIONS + TREE_ACTIONS}
FILE_ACTION_KEYS = {key: action for key, action, _ in FILE_ACTIONS}
EXIT_KEYS = {"escape", "space"}


def _keys_text(with_file: bool = False, project_heading: str = "This project") -> str:
    """Columns ("This file" | "This project" | "Whole tree") as padded
    markup lines; "This file" only `with_file`. `project_heading` names
    what the project actions act on (e.g. "3 marked projects")."""

    def cells(title: str, actions: list[tuple[str, str, str]]) -> list[tuple[str, int]]:
        # (markup, visible width) pairs so columns can be padded by hand.
        out = [(f"[b u]{title}[/]", len(title))]
        out += [(f" {key(k)}  {label}", len(k) + len(label) + 3)
                for k, _, label in actions]
        return out

    columns = [cells(project_heading, PROJECT_ACTIONS), cells("Whole tree", TREE_ACTIONS)]
    if with_file:
        columns.insert(0, cells("This file", FILE_ACTIONS))
    widths = [max(w for _, w in column) + 6 for column in columns]
    lines = []
    for i in range(max(len(column) for column in columns)):
        line = ""
        for column, width in zip(columns, widths):
            mark, w = column[i] if i < len(column) else ("", 0)
            line += mark + " " * (width - w)
        lines.append(line.rstrip())
    return "\n".join(lines)


class ActionPanel(ModalScreen[Optional[str]]):
    def __init__(
        self,
        target: str | None,
        file_target: str | None = None,
        project_heading: str = "This project",
    ) -> None:
        self._target = target
        self._file_target = file_target
        self._project_heading = project_heading
        self._keys = {**ACTION_KEYS, **(FILE_ACTION_KEYS if file_target else {})}
        super().__init__()

    def compose(self):
        box = Vertical(
            Static(
                _keys_text(self._file_target is not None, self._project_heading),
                id="action-keys",
            ),
            id="action-box",
        )
        target = self._target or "(no project selected)"
        if self._file_target:
            target += f" ─ file: {self._file_target}"
        box.border_title = f"ACTION ─ target: {escape(target)}"
        box.border_subtitle = f"{key('Esc')} / {key('Space')} exit"
        yield box
        yield Static(
            "[b reverse] ACTION MODE [/] "
            + tip(f"press a key above, or {key('Esc')}/{key('Space')} to exit"),
            id="action-bar",
        )

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if event.key in EXIT_KEYS:
            self.dismiss(None)
            return
        action = self._keys.get(event.character or "")
        if action is None:
            # Stay open: a slip shouldn't mean pressing Space again.
            self.notify(
                f"'{escape(event.character or event.key)}' isn't an action key here — "
                "pick one above, or Esc/Space to exit",
                timeout=3,
            )
            return
        self.dismiss(action)

    def on_click(self) -> None:
        self.dismiss(None)
