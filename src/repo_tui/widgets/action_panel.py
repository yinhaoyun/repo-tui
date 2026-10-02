"""Action mode: pressing Space opens this overlay, a boxed panel listing every
action key grouped by scope. It is one-shot — an action key dismisses it with
that action's id; Esc or Space again dismisses it with None. It stays open
until one of those happens (no timeout)."""

from __future__ import annotations

from textual import events
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

# (key, action id, label) — split by what the action touches.
PROJECT_ACTIONS = [
    ("s", "sync_selected", "sync"),
    ("d", "detach", "detach (sync -d -l)"),
    ("b", "start_branch", "start new branch"),
    ("B", "switch_branch", "switch branch"),
    ("x", "discard", "discard changes…"),
    ("c", "copy_path", "copy path"),
]
TREE_ACTIONS = [
    ("S", "sync_all", "sync all"),
    ("f", "forall", "forall <cmd>"),
]
ACTION_KEYS = {key: action for key, action, _ in PROJECT_ACTIONS + TREE_ACTIONS}
EXIT_KEYS = {"escape", "space"}


def _keys_text() -> str:
    """Two columns ("This project" | "Whole tree") as padded markup lines."""

    def cells(title: str, actions: list[tuple[str, str, str]]) -> list[tuple[str, int]]:
        # (markup, visible width) pairs so columns can be padded by hand.
        out = [(f"[b u]{title}[/]", len(title))]
        out += [(f" [b yellow]{key}[/]  {label}", len(key) + len(label) + 3)
                for key, _, label in actions]
        return out

    left = cells("This project", PROJECT_ACTIONS)
    right = cells("Whole tree", TREE_ACTIONS)
    width = max(w for _, w in left) + 6
    lines = []
    for i in range(max(len(left), len(right))):
        lmark, lw = left[i] if i < len(left) else ("", 0)
        rmark = right[i][0] if i < len(right) else ""
        lines.append((lmark + " " * (width - lw) + rmark).rstrip())
    return "\n".join(lines)


class ActionPanel(ModalScreen[str | None]):
    def __init__(self, target: str | None) -> None:
        self._target = target
        super().__init__()

    def compose(self):
        box = Vertical(Static(_keys_text(), id="action-keys"), id="action-box")
        box.border_title = f"ACTION ─ target: {self._target or '(no project selected)'}"
        box.border_subtitle = "Esc / Space  exit"
        yield box
        yield Static(
            "[b reverse] ACTION MODE [/] press a key above, or [b]Esc[/]/[b]Space[/] to exit",
            id="action-bar",
        )

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if event.key in EXIT_KEYS:
            self.dismiss(None)
            return
        action = ACTION_KEYS.get(event.character or "")
        if action is None:
            self.notify(f"'{event.character or event.key}' is not an action key", timeout=2)
            self.dismiss(None)
            return
        self.dismiss(action)

    def on_click(self) -> None:
        self.dismiss(None)
