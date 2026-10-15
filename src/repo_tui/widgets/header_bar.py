"""Top status bar: manifest/branch info, aggregate change counts, what the
last sync changed, last refresh time, and whether a newer repo-tui is out.

"N changed", "last sync: N updated" and "⬆ repo-tui update" are clickable:
they open the changed projects summary, the last sync's report (also `w` /
`u`) and the repo-tui update (also `U`)."""

from __future__ import annotations

import time

from rich.markup import escape
from textual import events
from textual.containers import Horizontal
from textual.widgets import Static

from ..models import Project, SyncReport, TreeInfo


class HeaderItem(Static):
    """A header segment that runs an app action when clicked."""

    def __init__(self, action: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._action = action

    async def on_click(self, event: events.Click) -> None:
        event.stop()
        await self.app.run_action(self._action)


class HeaderBar(Horizontal):
    """Shows manifest name/branch, project counts, and last refresh time."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tree_info: TreeInfo | None = None
        self.total = 0
        self.changed = 0
        self.syncing = False
        self.last_refresh = 0.0
        self.report: SyncReport | None = None
        self.update_behind = 0  # new repo-tui commits on GitHub

    def compose(self):
        yield Static(id="hdr-info")
        yield HeaderItem("show_changed", id="hdr-changed", classes="hdr-link")
        yield Static("  │  ", id="hdr-sync-sep")
        yield HeaderItem("show_sync_report", id="hdr-sync", classes="hdr-link")
        yield Static(id="hdr-status")
        yield HeaderItem("update_app", id="hdr-update", classes="hdr-link")

    def on_mount(self) -> None:
        self._refresh_display()

    def set_tree_info(self, info: TreeInfo) -> None:
        self.tree_info = info
        self._refresh_display()

    def set_counts(self, projects: list[Project]) -> None:
        self.total = len(projects)
        self.changed = sum(1 for p in projects if p.is_changed)
        self.last_refresh = time.time()
        self._refresh_display()

    def set_syncing(self, value: bool) -> None:
        self.syncing = value
        self._refresh_display()

    def set_report(self, report: SyncReport | None) -> None:
        self.report = report
        self._refresh_display()

    def set_update_available(self, behind: int) -> None:
        self.update_behind = behind
        self._refresh_display()

    def _refresh_display(self) -> None:
        if not self.is_mounted:
            return
        info = self.tree_info
        manifest_bit = f"[b cyan]{escape(info.manifest_branch or '?') if info else '?'}[/]"
        if info and info.manifest_name:
            manifest_bit += f" [dim]/ {escape(info.manifest_name)}[/]"
        self.query_one("#hdr-info", Static).update(
            f"[b]repo-tui[/]  │  manifest: {manifest_bit}  │  "
            f"{self.total} projects, "
        )
        self.query_one("#hdr-changed", Static).update(
            f"[b red]{self.changed} changed[/]" if self.changed else "[b green]clean[/]"
        )

        report = self.report
        self.query_one("#hdr-sync-sep").display = report is not None
        sync = self.query_one("#hdr-sync", Static)
        sync.display = report is not None
        if report is not None:
            when = time.strftime("%H:%M", time.localtime(report.finished_at))
            count = len(report.changes)
            colour = "red" if report.returncode != 0 else ("cyan" if count else "green")
            sync.update(
                f"last sync: [b {colour}]{count} updated[/]"
                + (" [b red](failed)[/]" if report.returncode != 0 else "")
                + f" [dim]{when}[/]"
            )

        age = "just now"
        if self.last_refresh:
            secs = int(time.time() - self.last_refresh)
            age = "just now" if secs < 2 else f"{secs}s ago"
        status_bit = "[b yellow]syncing…[/]" if self.syncing else f"refreshed {age}"
        root = escape(str(info.root)) if info else ""
        self.query_one("#hdr-status", Static).update(
            f"  │  {status_bit}  │  root: {root}"
        )
        update = self.query_one("#hdr-update", Static)
        update.display = self.update_behind > 0
        update.update(f"[b $text-success]⬆ repo-tui update ({self.update_behind})[/]")
