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
from .widgets.action_panel import ActionPanel
from .widgets.busy_modal import BusyModal
from .widgets.context_menu import ContextMenu
from .widgets.detail_pane import DetailPane, FileTable
from .widgets.diff_screen import DiffScreen
from .widgets.header_bar import HeaderBar
from .widgets.help_modal import HelpModal
from .widgets.keybar import KeyBar
from .widgets.project_list import ProjectContextMenuRequested, ProjectHighlighted, ProjectList
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
        # Safe actions (each opens a prompt/picker first, or is harmless) sit on
        # the top layer; ones that run immediately (sync, detach, sync all) are
        # only reachable through the Space action panel.
        Binding("b", "start_branch", "New branch"),
        Binding("B", "switch_branch", "Switch branch"),
        Binding("c", "copy_path", "Copy path"),
        Binding("f", "forall", "Forall"),
        Binding("space", "open_action_panel", "Actions"),
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

    def _selected_project(self) -> Project | None:
        return self.query_one("#project-list", ProjectList).project_at_cursor()

    async def _refresh(self, busy: BusyModal | None = None) -> None:
        """Re-scan git status for every project behind a blocking BusyModal
        with a per-project count. Reuses `busy` when a repo command already
        has one open; otherwise opens (and closes) its own."""
        own_dialog = busy is None
        if busy is None:
            busy = BusyModal("Refreshing status", show_log=False)
            await self.push_screen(busy)
        busy.set_phase("Refreshing status…")
        header = self.query_one("#header", HeaderBar)
        header.set_syncing(True)
        loop = asyncio.get_running_loop()

        def on_progress(done: int, total: int, project: Project) -> None:
            loop.call_soon_threadsafe(busy.set_count, done, total, project.path)

        try:
            self.projects = await asyncio.to_thread(
                git_backend.collect_all,
                self.projects,
                self.config.ignore,
                self.config.max_workers,
                on_progress,
            )
        finally:
            header.set_syncing(False)
            if own_dialog:
                await busy.dismiss()
        self._apply_filters()

    # -- message handlers ---------------------------------------------------

    def on_project_highlighted(self, message: ProjectHighlighted) -> None:
        self.query_one("#detail-pane", DetailPane).show_project(
            message.project, self.show_ignored
        )

    def on_project_context_menu_requested(self, message: ProjectContextMenuRequested) -> None:
        self._open_context_menu(message.project)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # Fires for both ProjectList and FileTable (Enter key, or mouse click
        # on a row) since both are DataTables; only FileTable rows open a diff.
        if event.data_table.id != "file-table":
            return
        project = self._selected_project()
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

    # -- per-project actions: shared by the action keys and the
    #    right-click context menu ----------------------------------------

    async def _run_and_refresh(
        self, title: str, coro_factory, command: str = "", jobs: int | None = None
    ) -> None:
        """Run a repo command behind a blocking BusyModal, refresh status,
        then show the command's output. `coro_factory(on_output, on_progress)`
        starts the command; `command`/`jobs` are only shown in the dialog."""
        header = self.query_one("#header", HeaderBar)
        header.set_syncing(True)
        busy = BusyModal(title, command=command, jobs=jobs)
        await self.push_screen(busy)
        log_lines: list[str] = []

        async def on_output(line: str) -> None:
            log_lines.append(line)
            busy.add_output(line)

        async def on_progress(line: str) -> None:
            busy.set_progress(line)

        try:
            try:
                returncode = await coro_factory(on_output, on_progress)
            except OSError as exc:  # e.g. `repo` not on PATH
                log_lines.append(f"error: {exc}")
                returncode = None
            await self._refresh(busy)
        finally:
            header.set_syncing(False)
            await busy.dismiss()
        result = "done" if returncode == 0 else f"failed (exit {returncode})"
        self.push_screen(DiffScreen(f"{title} — {result}", "\n".join(log_lines)))

    def _sync_project(self, project: Project) -> None:
        self.run_worker(
            self._run_and_refresh(
                f"repo sync: {project.path}",
                lambda on_output, on_progress: repo_backend.sync_projects(
                    self.repo_root,
                    [project.path],
                    on_output=on_output,
                    on_progress=on_progress,
                    jobs=self.config.sync_jobs,
                ),
                command=" ".join(repo_backend.sync_args([project.path], self.config.sync_jobs)),
                jobs=self.config.sync_jobs,
            ),
            exclusive=True,
            group="sync",
        )

    def _sync_all(self) -> None:
        self.run_worker(
            self._run_and_refresh(
                "repo sync (all)",
                lambda on_output, on_progress: repo_backend.sync_projects(
                    self.repo_root,
                    on_output=on_output,
                    on_progress=on_progress,
                    jobs=self.config.sync_jobs,
                ),
                command=" ".join(repo_backend.sync_args(jobs=self.config.sync_jobs)),
                jobs=self.config.sync_jobs,
            ),
            exclusive=True,
            group="sync",
        )

    def _detach_project(self, project: Project) -> None:
        self.run_worker(
            self._run_and_refresh(
                f"repo sync -d: {project.path}",
                lambda on_output, on_progress: repo_backend.sync_detach(
                    self.repo_root,
                    [project.path],
                    on_output=on_output,
                    on_progress=on_progress,
                    jobs=self.config.sync_jobs,
                ),
                command=" ".join(
                    repo_backend.sync_args([project.path], self.config.sync_jobs, detach=True)
                ),
                jobs=self.config.sync_jobs,
            ),
            exclusive=True,
            group="sync",
        )

    def _checkout_branch(self, project: Project, branch: str) -> None:
        self.run_worker(
            self._run_and_refresh(
                f"repo checkout {branch}: {project.path}",
                lambda on_output, _progress: repo_backend.checkout_branch(
                    self.repo_root, branch, [project.path], on_output=on_output
                ),
            ),
            exclusive=True,
            group="branch",
        )

    def _prompt_switch_branch(self, project: Project) -> None:
        other_branches = [b for b in project.local_branches if b != project.current_branch]

        if other_branches:

            def handle_choice(branch: str | None) -> None:
                if branch:
                    self._checkout_branch(project, branch)

            self.push_screen(
                ContextMenu(
                    f"Switch branch — {project.path}",
                    [(b, b) for b in other_branches],
                ),
                handle_choice,
            )
        else:

            def handle_text(branch: str | None) -> None:
                if branch:
                    self._checkout_branch(project, branch)

            self.push_screen(
                PromptModal(
                    f"{project.path} has no other local branches. "
                    "Type an existing topic branch name to switch to:",
                    "branch name",
                ),
                handle_text,
            )

    def _prompt_start_branch(self, project: Project) -> None:
        def handle(branch: str | None) -> None:
            if not branch:
                return
            self.run_worker(
                self._run_and_refresh(
                    f"repo start {branch}: {project.path}",
                    lambda on_output, _progress: repo_backend.start_branch(
                        self.repo_root, branch, [project.path], on_output=on_output
                    ),
                ),
                exclusive=True,
                group="branch",
            )

        self.push_screen(
            PromptModal(f"New branch name for {project.path}:", "branch name"),
            handle,
        )

    def _copy_project_path(self, project: Project) -> None:
        text = str(project.abs_path)
        try:
            self.copy_to_clipboard(text)
            self.notify(f"Copied: {text}")
        except Exception:
            self.notify(text, title="Project path")

    def _open_context_menu(self, project: Project) -> None:
        options = [
            ("sync", "Sync (repo sync)"),
            ("detach", "Detach to manifest revision (repo sync -d)"),
            ("switch", "Switch branch…"),
            ("start", "Start new branch…"),
            ("copy", "Copy path"),
        ]

        def handle(choice: str | None) -> None:
            if choice == "sync":
                self._sync_project(project)
            elif choice == "detach":
                self._detach_project(project)
            elif choice == "switch":
                self._prompt_switch_branch(project)
            elif choice == "start":
                self._prompt_start_branch(project)
            elif choice == "copy":
                self._copy_project_path(project)

        self.push_screen(ContextMenu(project.path, options), handle)

    # -- action panel (Space) and the actions it dispatches -------------

    @property
    def action_mode_active(self) -> bool:
        return isinstance(self.screen, ActionPanel)

    def action_open_action_panel(self) -> None:
        project = self._selected_project()

        def handle(action: str | None) -> None:
            if action:
                getattr(self, f"action_{action}")()

        self.push_screen(ActionPanel(project.path if project else None), handle)

    def action_sync_selected(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._sync_project(project)

    def action_sync_all(self) -> None:
        self._sync_all()

    def action_detach(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._detach_project(project)

    def action_forall(self) -> None:
        def handle(command: str | None) -> None:
            if not command:
                return
            self.run_worker(
                self._run_and_refresh(
                    f"forall: {command}",
                    lambda on_output, _progress: repo_backend.forall(
                        self.repo_root, command, on_output=on_output
                    ),
                ),
                exclusive=True,
                group="forall",
            )

        self.push_screen(
            PromptModal("Command to run in every project (repo forall -c):", "e.g. git log -1"),
            handle,
        )

    def action_start_branch(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._prompt_start_branch(project)

    def action_switch_branch(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._prompt_switch_branch(project)

    def action_copy_path(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._copy_project_path(project)
