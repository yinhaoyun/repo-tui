"""Why each project counts as "changed": opened by clicking "N changed" in
the header (or `Space w`). Enter or a click on a row jumps to that project
in the main list."""

from __future__ import annotations

from typing import Optional

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ..hints import close_tip, key, tip
from ..models import Project


def change_reasons(project: Project) -> list[str]:
    """Plain-language reasons, matching Project.is_changed."""
    reasons = []
    if project.error:
        reasons.append(f"error: {project.error}")
    visible = [f for f in project.files if not f.ignored]
    if visible:
        reasons.append(f"{len(visible)} file(s) changed ({project.stat_summary})")
    if project.ahead:
        reasons.append(f"{project.ahead} commit(s) not pushed")
    if project.behind:
        reasons.append(f"{project.behind} commit(s) behind upstream")
    if project.current_branch and not project.is_detached:
        branch = f"on branch {project.current_branch}"
        if project.off_manifest_branch:
            branch += f" (manifest: {project.manifest_revision})"
        reasons.append(branch)
    return reasons


class ChangedScreen(ModalScreen[Optional[str]]):
    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(self, projects: list[Project]) -> None:
        self._changed = sorted((p for p in projects if p.is_changed), key=lambda p: p.path)
        super().__init__()

    def compose(self):
        with Vertical(id="changed-box"):
            yield Static(
                f"[b]{len(self._changed)} changed project(s)[/]  {close_tip('esc', 'q')}",
                id="changed-title",
            )
            if self._changed:
                yield DataTable(id="changed-table", cursor_type="row", zebra_stripes=True)
                yield Static(
                    tip(f"{key('enter')}/click a row to jump to that project"),
                    id="changed-keys",
                )
            else:
                yield Static("[dim]Nothing changed — the tree is clean.[/]")

    def on_mount(self) -> None:
        if not self._changed:
            return
        table = self.query_one("#changed-table", DataTable)
        table.add_columns("Path", "Branch", "Why")
        for project in self._changed:
            table.add_row(
                project.path,
                project.branch_summary,
                "; ".join(change_reasons(project)),
                key=project.path,
            )
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.dismiss(event.row_key.value)

    def action_close(self) -> None:
        self.dismiss(None)
