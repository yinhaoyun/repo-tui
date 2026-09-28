"""A blocking "please wait" dialog shown while a repo command (sync, detach,
branch, forall) or a status refresh runs. It swallows every key and click so nothing else can
be triggered mid-operation; the app dismisses it when the command finishes.

Shows the command line, an animated progress bar (switched to a real
percentage from repo's live progress line, e.g. "Fetching: 45% [4 jobs]
(12/27) 0:03 | platform/build"), that progress line itself with the number of
jobs currently running, elapsed time, and the tail of the command's output."""

from __future__ import annotations

import re
import time

from rich.markup import escape
from textual import events
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ProgressBar, RichLog, Static

# "45% [4 jobs] (12/27)" / "45% (12/27)"; the counts may carry a unit suffix.
_PROGRESS_RE = re.compile(
    r"(\d+)%\s+(?:\[(\d+) jobs?\]\s+)?\((\d+)[A-Za-z]*/(\d+)[A-Za-z]*\)"
)


class BusyModal(ModalScreen[None]):
    def __init__(
        self, title: str, command: str = "", jobs: int | None = None, show_log: bool = True
    ) -> None:
        self._title = title
        self._show_log = show_log
        self._command = command
        self._jobs = jobs
        self._running_jobs: int | None = None
        self._started = time.monotonic()
        self._phase = "Running…"
        super().__init__()

    def compose(self):
        with Vertical(id="busy-box"):
            yield Static(f"[b]{self._title}[/]", id="busy-title")
            if self._command:
                yield Static(f"[dim]$ {escape(self._command)}[/]", id="busy-command")
            yield ProgressBar(total=None, show_eta=False, id="busy-bar")
            yield Static(id="busy-status")
            yield Static(id="busy-progress")
            if self._show_log:
                yield RichLog(id="busy-log", wrap=False, highlight=False, markup=False)
            yield Static("[dim]Please wait — keys are disabled until this finishes.[/]")

    def on_mount(self) -> None:
        self._update_status()
        self.set_interval(1.0, self._update_status)

    def _update_status(self) -> None:
        elapsed = int(time.monotonic() - self._started)
        parts = [self._phase, f"[dim]{elapsed // 60}:{elapsed % 60:02d}[/]"]
        if self._jobs:
            running = f", {self._running_jobs} running" if self._running_jobs else ""
            parts.append(f"jobs: {self._jobs} max{running}")
        self.query_one("#busy-status", Static).update("  ".join(parts))

    def _track_progress(self, line: str) -> None:
        match = _PROGRESS_RE.search(line)
        if match:
            running, done, total = match[2], int(match[3]), int(match[4])
            self.query_one("#busy-bar", ProgressBar).update(total=total, progress=done)
            self._running_jobs = int(running) if running else None
            self._update_status()

    def add_output(self, line: str) -> None:
        self.query_one("#busy-log", RichLog).write(line)
        self._track_progress(line)

    def set_progress(self, line: str) -> None:
        """repo's live, redrawn-in-place progress line."""
        self.query_one("#busy-progress", Static).update(escape(line))
        self._track_progress(line)

    def set_count(self, done: int, total: int, detail: str = "") -> None:
        """Progress counted in items (e.g. projects scanned by a refresh)."""
        if not self.is_mounted:  # a late update after the dialog closed
            return
        self.query_one("#busy-bar", ProgressBar).update(total=total, progress=done)
        text = f"{done}/{total} projects" + (f"  {detail}" if detail else "")
        self.query_one("#busy-progress", Static).update(escape(text))

    def set_phase(self, phase: str) -> None:
        self._phase = phase
        self._running_jobs = None
        self.query_one("#busy-progress", Static).update("")
        # back to indeterminate: the next phase has no known length
        self.query_one("#busy-bar", ProgressBar).update(total=None)
        self._update_status()

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()

    def on_click(self, event: events.Click) -> None:
        event.stop()
