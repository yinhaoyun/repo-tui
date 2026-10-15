"""The `o` settings screen: the settings in config.SETTINGS, grouped into
tabs (Display / Sync / Behaviour), one row each with its current value.

Keyboard: tab/shift+tab switch tabs, ↑/↓ (j/k) pick a row, ←/→ (h/l) change
the value, enter/space toggle or step it (enter on a number types it in),
esc/q/o close. Enter on the theme opens a filterable list. Mouse: click a tab to switch, click a row to change it.

Every change is applied and saved at once through `on_change`, which returns
a status line to show (where it was saved, or why it wasn't)."""

from __future__ import annotations

from typing import Any, Callable

from rich.markup import escape
from textual import events
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..config import SETTINGS, Config, Setting
from ..hints import key, tip
from .picker_modal import PickerModal
from .prompt_modal import PromptModal

TABS = list(dict.fromkeys(setting.tab for setting in SETTINGS))
_LABEL_WIDTH = 32

OnChange = Callable[[str, Any], str]


class TabLabel(Static):
    def __init__(self, index: int) -> None:
        super().__init__(f" {TABS[index]} ", classes="settings-tab")
        self.index = index

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.screen.show_tab(self.index)


class SettingRow(Static):
    def __init__(self, setting: Setting) -> None:
        super().__init__(classes="setting-row")
        self.setting = setting

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.screen.click_row(self)


class SettingsScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("tab", "tab(1)", show=False, priority=True),
        Binding("shift+tab", "tab(-1)", show=False, priority=True),
        Binding("up,k", "move(-1)", show=False),
        Binding("down,j", "move(1)", show=False),
        Binding("left,h", "step(-1)", show=False),
        Binding("right,l", "step(1)", show=False),
        Binding("enter,space", "activate", show=False),
        Binding("escape,q,o", "close", show=False),
    ]

    def __init__(self, config: Config, themes: list[str], on_change: OnChange) -> None:
        self._config = config
        self._themes = sorted(set(themes) | {config.theme})
        self._on_change = on_change
        self._tab = 0
        self._selected = {tab: 0 for tab in TABS}  # selected row per tab
        self._status = ""
        super().__init__()

    # -- layout -------------------------------------------------------------

    def compose(self):
        box = Vertical(id="settings-box")
        box.border_title = "Settings"
        with box:
            with Horizontal(id="settings-tabs"):
                for index in range(len(TABS)):
                    yield TabLabel(index)
            with Vertical(id="settings-rows"):
                for setting in SETTINGS:
                    yield SettingRow(setting)
            yield Static(id="settings-help")
            yield Static(id="settings-status")
            yield Static(id="settings-keys")

    def on_mount(self) -> None:
        self._render_all()

    def _rows(self) -> list[SettingRow]:
        return [row for row in self.query(SettingRow) if row.setting.tab == TABS[self._tab]]

    def _selected_row(self) -> SettingRow:
        rows = self._rows()
        return rows[min(self._selected[TABS[self._tab]], len(rows) - 1)]

    def _render_all(self) -> None:
        for tab in self.query(TabLabel):
            tab.set_class(tab.index == self._tab, "-active")
        selected = self._selected_row()
        for row in self.query(SettingRow):
            row.display = row.setting.tab == TABS[self._tab]
            row.set_class(row is selected, "-selected")
            row.update(self._row_markup(row.setting, row is selected))
        self.query_one("#settings-help", Static).update(escape(selected.setting.help))
        self.query_one("#settings-status", Static).update(self._status)
        self.query_one("#settings-keys", Static).update(
            tip(
                f"{key('tab')} next tab  {key('↑↓')} move  {key('←→')} change  "
                f"{key('enter')} edit/pick  {key('esc')} close"
            )
        )

    def _row_markup(self, setting: Setting, selected: bool) -> str:
        pointer = "[b $accent]▸[/]" if selected else " "
        label = escape(setting.label.ljust(_LABEL_WIDTH))
        value = getattr(self._config, setting.name)
        if setting.kind == "bool":
            control = (
                "[b #000000 on $success] ● ON  [/]"
                if value
                else "[b $text-muted on $surface] ○ OFF [/]"
            )
        else:
            arrow = "[b $accent]" if selected else "[$text-muted]"
            control = f"{arrow}‹[/] [b]{escape(self._value_label(setting, value))}[/] {arrow}›[/]"
        return f"{pointer} {label}{control}"

    def _value_label(self, setting: Setting, value: Any) -> str:
        for choice, label in setting.choices:
            if choice == value:
                return label
        return str(value)

    # -- changing values ----------------------------------------------------

    def _options(self, setting: Setting) -> list[Any]:
        if setting.kind == "theme":
            return self._themes
        return [value for value, _ in setting.choices]

    def _stepped(self, setting: Setting, direction: int) -> Any:
        value = getattr(self._config, setting.name)
        if setting.kind == "bool":
            return not value
        if setting.kind == "int":
            return setting.coerce(value + direction)
        options = self._options(setting)
        index = options.index(value) if value in options else 0
        return options[(index + direction) % len(options)]

    def _set(self, setting: Setting, value: Any) -> None:
        if value is None or value == getattr(self._config, setting.name):
            self._render_all()
            return
        self._status = self._on_change(setting.name, value)
        self._render_all()

    def _prompt_number(self, setting: Setting) -> None:
        def handle(text: str | None) -> None:
            if text is None:
                return
            try:
                number = int(text.strip())
            except ValueError:
                self._status = f"[b $text-error]Not a number: {escape(text)}[/]"
                self._render_all()
                return
            self._set(setting, setting.coerce(number))

        self.app.push_screen(
            PromptModal(
                f"{setting.label} ({setting.minimum}–{setting.maximum}):",
                str(getattr(self._config, setting.name)),
            ),
            handle,
        )

    def _pick_theme(self, setting: Setting) -> None:
        def handle(theme: str | None) -> None:
            if theme:
                self._set(setting, theme)

        self.app.push_screen(
            PickerModal(
                f"[b]Theme[/] [dim]({len(self._themes)} available; ←/→ in settings "
                "previews them one by one)[/]",
                self._themes,
                getattr(self._config, setting.name),
                "filter themes",
                allow_typed=False,
            ),
            handle,
        )

    # -- actions ------------------------------------------------------------

    def show_tab(self, index: int) -> None:
        self._tab = index % len(TABS)
        self._render_all()

    def click_row(self, row: SettingRow) -> None:
        rows = self._rows()
        if row in rows:
            self._selected[TABS[self._tab]] = rows.index(row)
            self.action_activate()

    def action_tab(self, direction: int) -> None:
        self.show_tab(self._tab + direction)

    def action_move(self, direction: int) -> None:
        tab = TABS[self._tab]
        self._selected[tab] = (self._selected[tab] + direction) % len(self._rows())
        self._render_all()

    def action_step(self, direction: int) -> None:
        setting = self._selected_row().setting
        self._set(setting, self._stepped(setting, direction))

    def action_activate(self) -> None:
        setting = self._selected_row().setting
        if setting.kind == "int":
            self._prompt_number(setting)
        elif setting.kind == "theme":
            self._pick_theme(setting)
        else:
            self._set(setting, self._stepped(setting, 1))

    def action_close(self) -> None:
        self.dismiss(None)

    def on_click(self, event: events.Click) -> None:
        if event.widget is self:  # clicked the dimmed background
            self.dismiss(None)
