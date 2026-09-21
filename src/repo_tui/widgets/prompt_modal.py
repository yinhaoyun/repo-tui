"""A small modal that asks the user for one line of text (branch name,
forall command, etc.) and returns it via dismiss()."""

from __future__ import annotations

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class PromptModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, placeholder: str = "") -> None:
        self._prompt = prompt
        self._placeholder = placeholder
        super().__init__()

    def compose(self):
        with Vertical(id="prompt-box"):
            yield Static(self._prompt, id="prompt-label")
            yield Input(placeholder=self._placeholder, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)
