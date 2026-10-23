"""What the last sync changed: every project whose checked-out commit moved,
on the left, and for the highlighted one, on the right, old → new commit, a
diff summary and the commits it brought in (or dropped, after a detach).

Opened by clicking "last sync: N updated" in the header, `u`, or `u` on
the command's result screen; also on its own right after a clean sync that
moved something, in place of the output screen (`o` then shows that)."""

from __future__ import annotations

import time

from ..markup import escape
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .. import git_backend
from ..compat import to_thread
from ..hints import close_tip, key, tip
from ..models import Project, SyncReport


def report_summary(report: SyncReport) -> str:
    when = time.strftime("%H:%M:%S", time.localtime(report.finished_at))
    count = len(report.changes)
    if report.incoming:
        moved = f"{count} of {report.scanned} project(s) have new commits upstream"
    else:
        moved = f"{count} of {report.scanned} project(s) moved to a new commit"
    failed = "" if report.returncode == 0 else "  [b red]— the command failed[/]"
    return f"{escape(report.title)} at {when}: [b]{moved}[/]{failed}"


class SyncReportScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "dismiss_screen", "Close"),
        ("q", "dismiss_screen", "Close"),
        ("o", "show_output", "Command output"),
    ]

    def __init__(
        self,
        report: SyncReport,
        projects: dict[str, Project],
        output: ModalScreen | None = None,
    ) -> None:
        self._report = report
        self._projects = projects
        self._output = output  # the sync's ResultScreen, when it opened in its place
        super().__init__()

    def compose(self):
        with Vertical(id="report-box"):
            heading = (
                "Incoming — fetched, not checked out yet" if self._report.incoming
                else "Last sync report"
            )
            yield Static(f"[b]{heading}[/]  {close_tip('esc', 'q')}", id="report-title")
            yield Static(report_summary(self._report), id="report-summary")
            if self._report.changes:
                with Horizontal(id="report-body"):
                    yield OptionList(
                        *(self._option(i) for i in range(len(self._report.changes))),
                        id="report-list",
                    )
                    yield RichLog(id="report-detail", wrap=False, markup=False)
                keys = f"{key('↑↓')} pick a project to see its new commits"
                if self._report.incoming:
                    keys += f"   {key('space')} {key('S')} syncs them in"
                if self._output is not None:
                    keys += f"   {key('o')} full command output"
                yield Static(tip(keys), id="report-keys")
            else:
                empty = (
                    "Up to date: no project has new commits upstream."
                    if self._report.incoming
                    else "No project moved to a different commit — nothing new came in."
                )
                yield Static(f"[dim]{empty}[/]", id="report-empty")

    def _option(self, index: int) -> Option:
        change = self._report.changes[index]
        label = Text(change.path)
        if change.added:
            label.append(f"  +{change.added}", style="b green")
        if change.dropped:
            label.append(f"  −{change.dropped}", style="b yellow")
        return Option(label, id=str(index))

    def on_mount(self) -> None:
        if self._report.changes:
            option_list = self.query_one("#report-list", OptionList)
            option_list.focus()
            option_list.highlighted = 0

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        change = self._report.changes[int(event.option.id)]
        self.run_worker(self._show_detail(change), exclusive=True, group="report-detail")

    async def _show_detail(self, change) -> None:
        log = self.query_one("#report-detail", RichLog)
        project = self._projects.get(change.path)
        if project is None:
            text = f"{change.path} is no longer in the project list."
        else:
            text = await to_thread(
                git_backend.describe_head_change, project, change, self._report.incoming
            )
        log.clear()
        log.write(Text(change.path, style="b"))
        log.write(text)

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_show_output(self) -> None:
        if self._output is not None:
            output, self._output = self._output, None
            self.dismiss(None)
            self.app.push_screen(output)
