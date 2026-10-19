"""Left pane: the per-project status list."""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable

from ..models import Project


# config.SORT_CHOICES -> sort key for the visible rows.
_SORT_KEYS = {
    "changed": lambda p: (not p.is_changed, p.path),
    "changes": lambda p: (
        not p.is_changed,
        -(p.added + p.deleted + p.untracked_count),
        p.path,
    ),
    "branch": lambda p: (p.is_detached or not p.current_branch, p.current_branch, p.path),
    "path": lambda p: p.path,
}


# Status filter id -> (menu label, test). On top of "show all" and the name
# filter; every one of these projects counts as changed anyway.
STATUS_FILTERS = {
    "": ("Everything", lambda p: True),
    "modified": ("Uncommitted changes", lambda p: p.has_visible_changes),
    "ahead": ("Unpushed commits (ahead)", lambda p: p.ahead > 0),
    "behind": ("Behind upstream", lambda p: p.behind > 0),
    "branch": ("On a local branch", lambda p: bool(p.current_branch) and not p.is_detached),
    "detached": (
        "Detached, with uncommitted changes",
        lambda p: p.is_detached and p.has_visible_changes,
    ),
    "error": ("Errors", lambda p: bool(p.error)),
}

# Short names for the header: "showing: unpushed".
STATUS_SHORT = {
    "modified": "uncommitted", "ahead": "unpushed", "behind": "behind",
    "branch": "on a branch", "detached": "detached + changes", "error": "errors",
}


class ProjectHighlighted(Message):
    """Sent whenever the highlighted row changes (keyboard or mouse)."""

    def __init__(self, project: Project | None) -> None:
        self.project = project
        super().__init__()


class ProjectContextMenuRequested(Message):
    """Sent on a right-click over a project row."""

    def __init__(self, project: Project) -> None:
        self.project = project
        super().__init__()


class MarksChanged(Message):
    """Sent when projects are marked or unmarked; `count` is how many are."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__()


class ProjectList(DataTable):
    """A DataTable of projects, filterable and sortable by "changed" state.

    Projects can be marked (m, M for all/none, esc to clear) so the Space
    actions work on several at once; a mark shows as ✓ in the first column
    and lasts until cleared or used, across refreshes and filtering."""

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("m", "toggle_mark", "Mark", show=False),
        Binding("M", "mark_all", "Mark all / none", show=False),
        Binding("escape", "clear_marks", "Clear marks", show=False),
        # Focus keys: in the file list, y copies the file's path instead.
        Binding("y", "app.copy_path", "Copy project path", show=False),
        # In the file list, l is that file's history instead.
        Binding("l", "app.project_log", "History", show=False),
        # The right-click menu from the keyboard (shift+F10 is the usual
        # "context menu" key in desktop apps).
        Binding("enter,shift+f10", "app.project_menu", "Menu", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self.zebra_stripes = True
        self._all_projects: list[Project] = []
        self._row_paths: list[str] = []
        self.marked: set[str] = set()

    def on_mount(self) -> None:
        # Branch/Stat get fixed widths so they always stay visible; Path is
        # last and auto-width, so an unusually long project path (common in
        # real AOSP trees, e.g. deep vendor/ paths) truncates at the pane's
        # edge instead of pushing the branch/status columns off-screen.
        self._marker_column = self.add_column("", width=2)
        self.add_column("Branch", width=18)
        self.add_column("Stat", width=12)
        self.add_column("Path")

    def set_projects(
        self,
        projects: list[Project],
        show_all: bool,
        search: str = "",
        sort: str = "changed",
        status: str = "",
    ) -> None:
        self._all_projects = projects
        known = {p.path for p in projects}
        if not self.marked <= known:  # e.g. dropped by a manifest switch
            self.marked &= known
            self.post_message(MarksChanged(len(self.marked)))
        prior_path = self._row_paths[self.cursor_row] if self._row_paths and self.cursor_row is not None and self.cursor_row < len(self._row_paths) else None

        self.clear()
        self._row_paths = []

        needle = search.strip().lower()
        wanted = STATUS_FILTERS.get(status, STATUS_FILTERS[""])[1]
        visible = [
            p
            for p in projects
            if (show_all or p.is_changed)
            and wanted(p)
            and (not needle or needle in p.path.lower() or needle in p.name.lower())
        ]
        visible.sort(key=_SORT_KEYS.get(sort, _SORT_KEYS["changed"]))

        for p in visible:
            self.add_row(
                self._marker(p),
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

    def _marker(self, p: Project) -> str:
        """✓ when marked, then the state: ! error, ● changed."""
        state = "!" if p.error else ("●" if p.is_changed else " ")
        style = "red" if p.error else ("yellow" if p.is_changed else "dim")
        mark = "[b $accent]✓[/]" if p.path in self.marked else " "
        return f"{mark}[{style}]{state}[/]"

    def _redraw_markers(self) -> None:
        by_path = {p.path: p for p in self._all_projects}
        for path in self._row_paths:
            self.update_cell(path, self._marker_column, self._marker(by_path[path]))
        self.post_message(MarksChanged(len(self.marked)))

    def set_marks(self, paths) -> None:  # noqa: ANN001 - any iterable of paths
        self.marked = set(paths)
        self._redraw_markers()

    def action_toggle_mark(self) -> None:
        """Mark or unmark the project at the cursor, then move down, so
        m m m marks a run of rows."""
        project = self.project_at_cursor()
        if project is None:
            return
        self.marked ^= {project.path}
        self._redraw_markers()
        if self.cursor_row is not None and self.cursor_row < len(self._row_paths) - 1:
            self.move_cursor(row=self.cursor_row + 1)

    def action_mark_all(self) -> None:
        """Clear the marks if there are any, else mark every shown row."""
        self.set_marks(set() if self.marked else self._row_paths)

    def action_clear_marks(self) -> None:
        if self.marked:
            self.set_marks(set())

    def select_path(self, path: str) -> bool:
        """Move the cursor to `path`'s row; False if it isn't shown."""
        if path not in self._row_paths:
            return False
        self.move_cursor(row=self._row_paths.index(path))
        return True

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

    def on_click(self, event: events.Click) -> None:
        if event.button != 3:
            return
        row_index = event.style.meta.get("row")
        if row_index is None or not (0 <= row_index < len(self._row_paths)):
            return
        self.move_cursor(row=row_index)
        project = self.project_at_cursor()
        if project is not None:
            self.post_message(ProjectContextMenuRequested(project))
