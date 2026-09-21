"""The repo-tui Textual application."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Input

from . import config as config_mod
from . import git_backend
from . import repo_backend
from .models import Project, TreeInfo
from .widgets.detail_pane import DetailPane, FileTable
from .widgets.diff_screen import DiffScreen
from .widgets.header_bar import HeaderBar
from .widgets.help_modal import HelpModal
from .widgets.keybar import KeyBar
from .widgets.project_list import ProjectHighlighted, ProjectList
from .widgets.prompt_modal import PromptModal


class FilterInput(Input):
    BINDINGS = [Binding("escape", "cancel_filter", "Cancel", show=False)]

    def action_cancel_filter(self) -> None:
        self.value = ""
        self.post_message(Input.Changed(self, ""))
        self.display = False
        self.screen.focus_next()


class RepoTuiApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "repo-tui"

    BINDINGS = [
        Binding("question_mark", "toggle_help", "Help", key_display="?"),
        Binding("?", "toggle_help", "Help", show=False),
        Binding("slash", "toggle_filter", "Filter", key_display="/"),
        Binding("/", "toggle_filter", "Filter", show=False),
        Binding("a", "toggle_show_all", "Show all"),
        Binding("i", "toggle_show_ignored", "Show ignored"),
        Binding("r", "refresh_status", "Refresh"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+b", "activate_leader", "Leader", show=False),
        Binding("escape", "cancel_leader", "Cancel leader", show=False),
        Binding("s", "leader_sync_selected", "Sync project", show=False),
        Binding("S", "leader_sync_all", "Sync all", show=False),
        Binding("f", "leader_forall", "Forall", show=False),
        Binding("b", "leader_start_branch", "Start branch", show=False),
        Binding("c", "leader_copy_path", "Copy path", show=False),
    ]

    def __init__(self, repo_root: Path) -> None:
        super().__init__()
        self.repo_root = repo_root
        self.config = config_mod.load_config(repo_root)
        self.tree_info: TreeInfo = repo_backend.load_tree_info(repo_root)
        self.projects: list[Project] = []
        self.show_all = self.config.show_all_default
        self.show_ignored = False
        self.search = ""
        self.leader_active = False
        self._leader_timer = None

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Horizontal(id="body"):
            yield ProjectList(id="project-list")
            yield DetailPane(id="detail-pane")
        yield FilterInput(placeholder="filter by path/name…", id="filter-bar")
        yield KeyBar(id="keybar")

    def on_mount(self) -> None:
        self.query_one("#filter-bar", FilterInput).display = False
        self.query_one("#keybar", KeyBar).show_top_level()
        self.query_one("#header", HeaderBar).set_tree_info(self.tree_info)
        self.projects = repo_backend.load_projects(self.repo_root)
        self.tree_info.total_projects = len(self.projects)
        self._apply_filters()
        self.query_one("#project-list", ProjectList).focus()
        self.run_worker(self._refresh(), exclusive=True, group="refresh")

    # -- data plumbing ----------------------------------------------------

    def _apply_filters(self) -> None:
        self.query_one("#project-list", ProjectList).set_projects(
            self.projects, self.show_all, self.search
        )
        self._sync_detail_pane()
        self.query_one("#header", HeaderBar).set_counts(self.projects)

    def _sync_detail_pane(self) -> None:
        project = self.query_one("#project-list", ProjectList).project_at_cursor()
        self.query_one("#detail-pane", DetailPane).show_project(project, self.show_ignored)

    async def _refresh(self) -> None:
        header = self.query_one("#header", HeaderBar)
        header.set_syncing(True)
        try:
            self.projects = await asyncio.to_thread(
                git_backend.collect_all,
                self.projects,
                self.config.ignore,
                self.config.max_workers,
            )
        finally:
            header.set_syncing(False)
        self._apply_filters()

    # -- message handlers ---------------------------------------------------

    def on_project_highlighted(self, message: ProjectHighlighted) -> None:
        self.query_one("#detail-pane", DetailPane).show_project(
            message.project, self.show_ignored
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Fires for both ProjectList and FileTable (Enter key, or mouse click
        # on a row) since both are DataTables; only FileTable rows open a diff.
        if event.data_table.id != "file-table":
            return
        project = self.query_one("#project-list", ProjectList).project_at_cursor()
        file_table = self.query_one("#file-table", FileTable)
        file = file_table.file_at_cursor()
        if project is None or file is None:
            return
        self.run_worker(self._show_diff(project, file.path), exclusive=True, group="diff")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-bar":
            self.search = event.value
            self._apply_filters()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-bar":
            event.input.display = False
            self.query_one("#project-list", ProjectList).focus()

    # -- always-on actions ----------------------------------------------

    def action_toggle_help(self) -> None:
        self.push_screen(HelpModal())

    def action_toggle_filter(self) -> None:
        bar = self.query_one("#filter-bar", FilterInput)
        bar.display = True
        bar.focus()

    def action_toggle_show_all(self) -> None:
        self.show_all = not self.show_all
        self._apply_filters()

    def action_toggle_show_ignored(self) -> None:
        self.show_ignored = not self.show_ignored
        self._sync_detail_pane()

    def action_refresh_status(self) -> None:
        self.run_worker(self._refresh(), exclusive=True, group="refresh")

    async def _show_diff(self, project: Project, rel_path: str) -> None:
        text = await asyncio.to_thread(git_backend.read_file_diff, project, rel_path)
        self.push_screen(DiffScreen(f"{project.path}/{rel_path}", text))

    # -- leader (Ctrl+B) actions -----------------------------------------

    def action_activate_leader(self) -> None:
        self.leader_active = True
        self.query_one("#keybar", KeyBar).show_leader()
        self._leader_timer = self.set_timer(3.0, self.action_cancel_leader)

    def action_cancel_leader(self) -> None:
        self.leader_active = False
        self.query_one("#keybar", KeyBar).show_top_level()

    def _consume_leader(self) -> bool:
        """Return True and reset leader state, iff leader mode was active."""
        if not self.leader_active:
            return False
        self.action_cancel_leader()
        return True

    def action_leader_sync_selected(self) -> None:
        if not self._consume_leader():
            return
        project = self.query_one("#project-list", ProjectList).project_at_cursor()
        if project is None:
            return
        self.run_worker(
            self._run_and_refresh(
                "repo sync (selected)",
                lambda on_output: repo_backend.sync_projects(
                    self.repo_root, [project.path], on_output=on_output
                ),
            ),
            exclusive=True,
            group="sync",
        )

    def action_leader_sync_all(self) -> None:
        if not self._consume_leader():
            return
        self.run_worker(
            self._run_and_refresh(
                "repo sync (all)",
                lambda on_output: repo_backend.sync_projects(
                    self.repo_root, on_output=on_output
                ),
            ),
            exclusive=True,
            group="sync",
        )

    async def _run_and_refresh(self, title: str, coro_factory) -> None:
        header = self.query_one("#header", HeaderBar)
        header.set_syncing(True)
        log_lines: list[str] = []

        async def on_output(line: str) -> None:
            log_lines.append(line)

        try:
            await coro_factory(on_output)
        finally:
            header.set_syncing(False)
        await self._refresh()
        self.push_screen(DiffScreen(title, "\n".join(log_lines)))

    def action_leader_forall(self) -> None:
        if not self._consume_leader():
            return

        async def handle(command: str | None) -> None:
            if not command:
                return
            log_lines: list[str] = []

            async def on_output(line: str) -> None:
                log_lines.append(line)

            header = self.query_one("#header", HeaderBar)
            header.set_syncing(True)
            try:
                await repo_backend.forall(self.repo_root, command, on_output=on_output)
            finally:
                header.set_syncing(False)
            self.push_screen(DiffScreen(f"forall: {command}", "\n".join(log_lines)))
            await self._refresh()

        self.push_screen(
            PromptModal("Command to run in every project (repo forall -c):", "e.g. git log -1"),
            handle,
        )

    def action_leader_start_branch(self) -> None:
        if not self._consume_leader():
            return
        project = self.query_one("#project-list", ProjectList).project_at_cursor()
        if project is None:
            return

        async def handle(branch: str | None) -> None:
            if not branch:
                return
            log_lines: list[str] = []

            async def on_output(line: str) -> None:
                log_lines.append(line)

            await repo_backend.start_branch(
                self.repo_root, branch, [project.path], on_output=on_output
            )
            self.push_screen(DiffScreen(f"repo start {branch}", "\n".join(log_lines)))
            await self._refresh()

        self.push_screen(
            PromptModal(f"New/checkout branch name for {project.path}:", "branch name"),
            handle,
        )

    def action_leader_copy_path(self) -> None:
        if not self._consume_leader():
            return
        project = self.query_one("#project-list", ProjectList).project_at_cursor()
        if project is None:
            return
        text = str(project.abs_path)
        try:
            self.copy_to_clipboard(text)
            self.notify(f"Copied: {text}")
        except Exception:
            self.notify(text, title="Project path")
