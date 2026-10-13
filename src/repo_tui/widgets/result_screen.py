"""The screen shown after a repo command finishes: its outcome, any error
and warning lines pulled out into a "Problems" box at the top, and the full
output below with those lines highlighted."""

from __future__ import annotations

from typing import Optional

from rich.markup import escape
from rich.text import Text
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static

from .. import problems
from ..hints import close_tip, key, tip
from ..models import SyncReport

_MAX_PROBLEMS_SHOWN = 10


def summarize(returncode: Optional[int], errors: int, warnings: int) -> tuple[str, str]:
    """(status text, severity) for a finished command; severity is one of
    Textual's notify severities ("information", "warning", "error")."""
    counts = []
    if errors:
        counts.append(f"{errors} error(s)")
    if warnings:
        counts.append(f"{warnings} warning(s)")
    if returncode is None:
        status = "failed to run"
    elif returncode != 0:
        status = f"failed (exit {returncode})"
    else:
        status = "done"
    if counts:
        status += (" with " if returncode == 0 else ", ") + " and ".join(counts)
    if returncode != 0 or errors:
        return status, "error"
    if warnings:
        return status, "warning"
    return status, "information"


class ResultScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "dismiss_screen", "Close"),
        ("q", "dismiss_screen", "Close"),
        ("u", "show_report", "Sync report"),
    ]

    def __init__(
        self,
        title: str,
        lines: list[str],
        returncode: Optional[int],
        report: Optional[SyncReport] = None,
    ) -> None:
        self._title = title
        self._report = report
        self._lines = lines
        self._levels = [problems.classify(line) for line in lines]
        self.problem_lines = [
            (level, line) for level, line in zip(self._levels, lines) if level
        ]
        errors = sum(1 for level, _ in self.problem_lines if level == problems.ERROR)
        self.status, self.severity = summarize(
            returncode, errors, len(self.problem_lines) - errors
        )
        super().__init__()

    def compose(self):
        colour = {"error": "red", "warning": "yellow"}.get(self.severity, "green")
        with Vertical(id="result-box", classes=f"-{self.severity}"):
            yield Static(
                f"[b]{escape(self._title)}[/] — [b {colour}]{escape(self.status)}[/]"
                f"  {close_tip('esc', 'q')}",
                id="result-title",
            )
            if self._report is not None:
                count = len(self._report.changes)
                yield Static(
                    f"[b cyan]{count} project(s) moved to a new commit[/]  "
                    + tip(f"press {key('u')} to see which, and what came in"),
                    id="result-updates",
                )
            if self.problem_lines:
                shown = self.problem_lines[:_MAX_PROBLEMS_SHOWN]
                body = Text()
                for level, line in shown:
                    body.append(line + "\n", style=problems.style_for(level))
                more = len(self.problem_lines) - len(shown)
                if more:
                    body.append(f"…and {more} more (see the full output below)", style="dim")
                body.rstrip()
                yield Static(body, id="result-problems")
            yield RichLog(id="result-log", wrap=False, highlight=False, markup=False)

    def on_mount(self) -> None:
        if self.problem_lines:
            self.query_one("#result-problems", Static).border_title = "Problems"
        log = self.query_one("#result-log", RichLog)
        if not self._lines:
            log.write(Text("(no output)", style="dim"))
        for level, line in zip(self._levels, self._lines):
            log.write(Text(line, style=problems.style_for(level)))

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_show_report(self) -> None:
        if self._report is not None:
            self.dismiss(None)
            self.app.action_show_sync_report()
