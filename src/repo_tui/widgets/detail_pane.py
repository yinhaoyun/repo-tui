"""Right pane: detail on the currently selected project and its files."""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ..models import FileStatus, Project

_STATUS_LABELS = {
    "??": "untracked",
    "A ": "added (staged)",
    " M": "modified",
    "M ": "modified (staged)",
    "MM": "modified (+staged)",
    " D": "deleted",
    "D ": "deleted (staged)",
    "R ": "renamed (staged)",
    "UU": "conflict",
}


class ProjectSummary(Static):
    def show_project(self, project: Project | None) -> None:
        if project is None:
            self.update("[dim]No project selected.[/]")
            return

        lines = [f"[b]{project.path}[/]  [dim]({project.name})[/]"]
        if project.error:
            lines.append(f"[b red]error:[/] {project.error}")

        branch_line = f"branch: [b]{project.branch_summary}[/]"
        if project.manifest_revision:
            branch_line += f"   manifest revision: [cyan]{project.manifest_revision}[/]"
        if project.off_manifest_branch:
            branch_line += "  [b red]※ off manifest branch[/]"
        lines.append(branch_line)

        lines.append(
            f"local branches: {project.local_branch_count}   "
            f"changes: [b]{project.stat_summary}[/]"
        )
        self.update("\n".join(lines))


class FileTable(DataTable):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self._files: list[FileStatus] = []

    def on_mount(self) -> None:
        self.add_columns("Status", "+/-", "Path")

    def show_files(self, files: list[FileStatus], show_ignored: bool) -> None:
        self._files = [f for f in files if show_ignored or not f.ignored]
        self.clear()
        for f in self._files:
            label = _STATUS_LABELS.get(f.code, f.code)
            stat = f"+{f.added}/-{f.deleted}" if (f.added or f.deleted) else ""
            style = "dim" if f.ignored else ""
            self.add_row(f"[{style}]{label}[/]" if style else label, stat, f.path)

    def file_at_cursor(self) -> FileStatus | None:
        if not self._files or self.cursor_row is None:
            return None
        if self.cursor_row >= len(self._files):
            return None
        return self._files[self.cursor_row]


class DetailPane(Vertical):
    def compose(self):
        yield ProjectSummary(id="project-summary")
        yield FileTable(id="file-table")

    def show_project(self, project: Project | None, show_ignored: bool) -> None:
        self.query_one("#project-summary", ProjectSummary).show_project(project)
        self.query_one("#file-table", FileTable).show_files(
            project.files if project else [], show_ignored
        )
