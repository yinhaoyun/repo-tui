"""A small modal that asks the user for one line of text (branch name,
forall command, etc.) and returns it via dismiss(). With a history, ↑/↓
step through earlier entries, and back down to what was being typed."""

from __future__ import annotations

from typing import List, Optional

from typing import Optional

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ..hints import key, tip


class PromptModal(ModalScreen[Optional[str]]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("up", "history(-1)", "Earlier"),
        ("down", "history(1)", "Later"),
    ]

    def __init__(
        self,
        prompt: str,
        placeholder: str = "",
        value: str = "",
        history: Optional[List[str]] = None,
    ) -> None:
        self._prompt = prompt
        self._placeholder = placeholder
        self._value = value
        self._history = list(history or [])
        self._pos = len(self._history)  # past the newest: what's being typed
        self._draft = value
        super().__init__()

    def compose(self):
        with Vertical(id="prompt-box"):
            yield Static(self._prompt, id="prompt-label")
            yield Input(self._value, placeholder=self._placeholder, id="prompt-input")
            keys = f"{key('enter')} confirm  {key('esc')} cancel"
            if self._history:
                keys += f"  {key('↑/↓')} earlier entries"
            yield Static(tip(keys), id="prompt-keys")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def action_history(self, step: int) -> None:
        if not self._history:
            return
        field = self.query_one("#prompt-input", Input)
        if self._pos == len(self._history):
            self._draft = field.value
        self._pos = max(0, min(len(self._history), self._pos + step))
        field.value = (
            self._history[self._pos] if self._pos < len(self._history) else self._draft
        )
        field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
