"""A blocking "please wait" dialog shown while a repo command runs (sync,
detach, branch, forall). It swallows every key and click so nothing else can
be triggered mid-operation; the app dismisses it when the command finishes.

Shows an animated progress bar (switched to a real percentage when the
command prints `NN% (done/total)` progress lines), elapsed time, and the
tail of the command's output."""

from __future__ import annotations

import re
import time

from textual import events
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ProgressBar, RichLog, Static

_PROGRESS_RE = re.compile(r"(\d+)% \((\d+)/(\d+)\)")


class BusyModal(ModalScreen[None]):
    def __init__(self, title: str) -> None:
        self._title = title
        self._started = time.monotonic()
        self._phase = "Running…"
        super().__init__()

    def compose(self):
        with Vertical(id="busy-box"):
            yield Static(f"[b]{self._title}[/]", id="busy-title")
            yield ProgressBar(total=None, show_eta=False, id="busy-bar")
            yield Static(id="busy-status")
            yield RichLog(id="busy-log", wrap=False, highlight=False, markup=False)
            yield Static("[dim]Please wait — keys are disabled until this finishes.[/]")

    def on_mount(self) -> None:
        self._update_status()
        self.set_interval(1.0, self._update_status)

    def _update_status(self) -> None:
        elapsed = int(time.monotonic() - self._started)
        self.query_one("#busy-status", Static).update(
            f"{self._phase}  [dim]{elapsed // 60}:{elapsed % 60:02d}[/]"
        )

    def add_output(self, line: str) -> None:
        self.query_one("#busy-log", RichLog).write(line)
        match = _PROGRESS_RE.search(line)
        if match:
            done, total = int(match[2]), int(match[3])
            self.query_one("#busy-bar", ProgressBar).update(total=total, progress=done)

    def set_phase(self, phase: str) -> None:
        self._phase = phase
        # back to indeterminate: the next phase has no known length
        self.query_one("#busy-bar", ProgressBar).update(total=None)
        self._update_status()

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()

    def on_click(self, event: events.Click) -> None:
        event.stop()
