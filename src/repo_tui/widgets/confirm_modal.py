"""A yes/no confirmation dialog for destructive actions. Only `y` confirms;
`n`, Esc, or a click outside the box cancel, so a stray Enter can't."""

from __future__ import annotations

from textual import events
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ConfirmModal(ModalScreen[bool]):
    def __init__(self, title: str, body: str) -> None:
        self._title = title
        self._body = body
        super().__init__()

    def compose(self):
        with Vertical(id="confirm-box"):
            yield Static(f"[b]{self._title}[/]", id="confirm-title")
            yield Static(self._body, id="confirm-body")
            yield Static("[b]y[/] yes   [b]n[/] / [b]Esc[/] no", id="confirm-keys")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if event.character in ("y", "Y"):
            self.dismiss(True)
        elif event.character in ("n", "N") or event.key == "escape":
            self.dismiss(False)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:  # clicked the dimmed background
            self.dismiss(False)
