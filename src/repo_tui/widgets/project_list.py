"""Left pane: the per-project status list."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import DataTable

from ..models import Project


class ProjectHighlighted(Message):
    """Sent whenever the highlighted row changes (keyboard or mouse)."""

    def __init__(self, project: Project | None) -> None:
        self.project = project
        super().__init__()


class ProjectList(DataTable):
    """A DataTable of projects, filterable and sortable by "changed" state."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._all_projects: list[Project] = []
        self._row_paths: list[str] = []

    def on_mount(self) -> None:
        # Branch/Stat get fixed widths so they always stay visible; Path is
        # last and auto-width, so an unusually long project path (common in
        # real AOSP trees, e.g. deep vendor/ paths) truncates at the pane's
        # edge instead of pushing the branch/status columns off-screen.
        self.add_column("", width=2)
        self.add_column("Branch", width=18)
        self.add_column("Stat", width=12)
        self.add_column("Path")

    def set_projects(
        self, projects: list[Project], show_all: bool, search: str = ""
    ) -> None:
        self._all_projects = projects
        prior_path = self._row_paths[self.cursor_row] if self._row_paths and self.cursor_row is not None and self.cursor_row < len(self._row_paths) else None

        self.clear()
        self._row_paths = []

        needle = search.strip().lower()
        visible = [
            p
            for p in projects
            if (show_all or p.is_changed)
            and (not needle or needle in p.path.lower() or needle in p.name.lower())
        ]
        visible.sort(key=lambda p: (not p.is_changed, p.path))

        for p in visible:
            marker = "!" if p.error else ("●" if p.is_changed else " ")
            style = "red" if p.error else ("yellow" if p.is_changed else "dim")
            self.add_row(
                f"[{style}]{marker}[/]",
                p.branch_summary,
                p.stat_summary,
                p.path,
                key=p.path,
            )
            self._row_paths.append(p.path)

        if prior_path and prior_path in self._row_paths:
            self.move_cursor(row=self._row_paths.index(prior_path))
        elif self._row_paths:
            self.move_cursor(row=0)

    def project_at_cursor(self) -> Project | None:
        if not self._row_paths or self.cursor_row is None:
            return None
        if self.cursor_row >= len(self._row_paths):
            return None
        path = self._row_paths[self.cursor_row]
        for p in self._all_projects:
            if p.path == path:
                return p
        return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.post_message(ProjectHighlighted(self.project_at_cursor()))
