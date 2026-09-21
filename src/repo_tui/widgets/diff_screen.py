"""A modal screen showing the full `git diff` output for one file."""

from __future__ import annotations

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static


class DiffScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss_screen", "Close"), ("q", "dismiss_screen", "Close")]

    def __init__(self, title: str, diff_text: str) -> None:
        self._title = title
        self._diff_text = diff_text
        super().__init__()

    def compose(self):
        with Vertical(id="diff-box"):
            yield Static(f"[b]{self._title}[/]  [dim](esc/q to close)[/]", id="diff-title")
            log = RichLog(id="diff-log", wrap=False, highlight=False, markup=False)
            yield log

    def on_mount(self) -> None:
        log = self.query_one("#diff-log", RichLog)
        text = self._diff_text or "(no textual diff — binary file or no changes)"
        log.write(text)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)
