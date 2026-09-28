"""A small popup menu: right-click on a project row (or a leader-key
shortcut) opens this to offer per-project repo actions, e.g. switching or
detaching a branch."""

from __future__ import annotations

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class ContextMenu(ModalScreen[str | None]):
    """Shows `options` (id, label pairs); dismisses with the chosen id, or
    None if cancelled. Navigable by keyboard (arrows/enter) or mouse click."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, options: list[tuple[str, str]]) -> None:
        self._title = title
        self._options = options
        super().__init__()

    def compose(self):
        with Vertical(id="menu-box"):
            yield Static(self._title, id="menu-title")
            yield OptionList(
                *(Option(label, id=option_id) for option_id, label in self._options)
            )

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
