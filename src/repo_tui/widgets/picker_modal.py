"""Pick one value from a possibly long list (e.g. the manifest repo's
branches), with a filter box on top. What's typed can also be used as is,
for a value that isn't in the list (unless `allow_typed` is off); returns
it via dismiss(). Matches come first, so Enter takes the best match."""

from __future__ import annotations

from typing import Optional

from rich.markup import escape
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from ..hints import key, tip

_TYPED = "typed"


class PickerModal(ModalScreen[Optional[str]]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("down", "move(1)", "Down"),
        ("up", "move(-1)", "Up"),
    ]

    def __init__(
        self,
        title: str,
        choices: list[str],
        current: str = "",
        placeholder: str = "",
        allow_typed: bool = True,
    ) -> None:
        self._title = title
        # The current value goes first, so Enter on an empty filter keeps it.
        self._choices = ([current] if current in choices else []) + [
            c for c in choices if c != current
        ]
        self._current = current
        self._placeholder = placeholder
        self._allow_typed = allow_typed
        self._shown: list[str] = []
        super().__init__()

    def compose(self):
        with Vertical(id="picker-box"):
            yield Static(self._title, id="picker-title")
            yield Input(placeholder=self._placeholder, id="picker-input")
            yield OptionList(id="picker-list")
            yield Static(
                tip(
                    f"type to filter  {key('↑/↓')} move  {key('enter')}/click choose  "
                    f"{key('esc')} cancel"
                ),
                id="picker-keys",
            )

    def on_mount(self) -> None:
        self._fill("")
        self.query_one("#picker-input", Input).focus()

    def _fill(self, text: str) -> None:
        needle = text.strip().lower()
        self._shown = [c for c in self._choices if needle in c.lower()]
        options = []
        for i, choice in enumerate(self._shown):
            label = escape(choice)
            if choice == self._current:
                label += "  [dim](current)[/]"
            options.append(Option(label, id=str(i)))
        typed = text.strip()
        if self._allow_typed and typed and typed not in self._choices:
            options.append(Option(f"use “{escape(typed)}” as typed", id=_TYPED))
        option_list = self.query_one("#picker-list", OptionList)
        option_list.clear_options()
        option_list.add_options(options)
        if not options:
            option_list.add_option(Option("[dim](nothing matches)[/]", disabled=True))
        else:
            option_list.highlighted = 0

    def _value_of(self, option_id: str | None) -> str | None:
        if option_id == _TYPED:
            return self.query_one("#picker-input", Input).value.strip() or None
        if option_id is not None and option_id.isdigit():
            return self._shown[int(option_id)]
        return None

    def on_input_changed(self, event: Input.Changed) -> None:
        self._fill(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        option_list = self.query_one("#picker-list", OptionList)
        if option_list.highlighted is None:
            return
        value = self._value_of(option_list.get_option_at_index(option_list.highlighted).id)
        if value:
            self.dismiss(value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        value = self._value_of(event.option.id)
        if value:
            self.dismiss(value)

    def action_move(self, step: int) -> None:
        option_list = self.query_one("#picker-list", OptionList)
        if step > 0:
            option_list.action_cursor_down()
        else:
            option_list.action_cursor_up()

    def action_cancel(self) -> None:
        self.dismiss(None)
