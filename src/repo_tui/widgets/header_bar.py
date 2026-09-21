"""Top status bar: manifest/branch info and aggregate change counts."""

from __future__ import annotations

import time

from textual.reactive import reactive
from textual.widgets import Static

from ..models import Project, TreeInfo


class HeaderBar(Static):
    """Shows manifest name/branch, project counts, and last refresh time."""

    tree_info: reactive[TreeInfo | None] = reactive(None)
    total: reactive[int] = reactive(0)
    changed: reactive[int] = reactive(0)
    syncing: reactive[bool] = reactive(False)
    last_refresh: reactive[float] = reactive(0.0)

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

    def _refresh_display(self) -> None:
        info = self.tree_info
        if info is None:
            self.update("repo-tui")
            return

        manifest_bit = info.manifest_branch or "?"
        age = "just now"
        if self.last_refresh:
            secs = int(time.time() - self.last_refresh)
            age = "just now" if secs < 2 else f"{secs}s ago"

        status_bit = "[b yellow]syncing…[/]" if self.syncing else f"refreshed {age}"
        changed_bit = (
            f"[b red]{self.changed} changed[/]" if self.changed else "[b green]clean[/]"
        )

        self.update(
            f"[b]repo-tui[/]  │  manifest: [b cyan]{manifest_bit}[/]  │  "
            f"{self.total} projects, {changed_bit}  │  {status_bit}  │  "
            f"root: {info.root}"
        )
