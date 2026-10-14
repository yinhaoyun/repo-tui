"""The repo-tui Textual application."""

from __future__ import annotations

import asyncio
import copy
import time
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Input

from . import config as config_mod
from . import git_backend
from . import hints
from . import repo_backend
from .models import Project, SyncReport, TreeInfo
from .widgets.action_panel import ActionPanel
from .widgets.busy_modal import BusyModal
from .widgets.changed_screen import ChangedScreen
from .widgets.confirm_modal import ConfirmModal
from .widgets.context_menu import ContextMenu
from .widgets.detail_pane import DetailPane, FileTable
from .widgets.diff_screen import DiffScreen
from .widgets.header_bar import HeaderBar
from .widgets.help_modal import HelpModal
from .widgets.keybar import KeyBar
from .widgets.project_list import ProjectContextMenuRequested, ProjectHighlighted, ProjectList
from .widgets.prompt_modal import PromptModal
from .widgets.result_screen import ResultScreen
from .widgets.settings_screen import SettingsScreen
from .widgets.splitter import Splitter
from .widgets.sync_report import SyncReportScreen


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
        Binding("R", "refresh_project", "Refresh project"),
        Binding("o", "settings", "Settings"),
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
        hints.set_highlight(self.config.highlight_hints)
        self.tree_info: TreeInfo = repo_backend.load_tree_info(repo_root)
        self.projects: list[Project] = []
        self.show_all = self.config.show_all_default
        self.show_ignored = False
        self.search = ""
        self._auto_timer = None
        self.sync_report: SyncReport | None = None
        # Bumped by every foreground status scan, so a background scan that
        # was overtaken by one throws its (now older) results away.
        self._scan_generation = 0

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Horizontal(id="body"):
            yield ProjectList(id="project-list")
            yield Splitter("project-list", id="pane-splitter")
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
        self._apply_theme(self.config.theme)
        self._restart_auto_refresh()
        self.run_worker(self._refresh(), exclusive=True, group="refresh")

    # -- data plumbing ----------------------------------------------------

    def _apply_filters(self) -> None:
        self.query_one("#project-list", ProjectList).set_projects(
            self.projects, self.show_all, self.search, self.config.sort
        )
        self._sync_detail_pane()
        self.query_one("#header", HeaderBar).set_counts(self.projects)

    def _sync_detail_pane(self) -> None:
        project = self.query_one("#project-list", ProjectList).project_at_cursor()
        self.query_one("#detail-pane", DetailPane).show_project(project, self.show_ignored)

    def _selected_project(self) -> Project | None:
        return self.query_one("#project-list", ProjectList).project_at_cursor()

    async def _refresh(
        self, busy: BusyModal | None = None, only: list[Project] | None = None
    ) -> None:
        """Re-scan git status behind a blocking BusyModal with a per-project
        count: every project, or just `only` (after an action on one project
        the rest of the tree can't have changed). Reuses `busy` when a repo
        command already has one open; otherwise opens (and closes) its own."""
        self._scan_generation += 1
        own_dialog = busy is None
        if busy is None:
            what = only[0].path if only is not None and len(only) == 1 else "status"
            busy = BusyModal(f"Refreshing {what}", show_log=False)
            await self.push_screen(busy)
        busy.set_phase("Refreshing status…")
        header = self.query_one("#header", HeaderBar)
        header.set_syncing(True)
        loop = asyncio.get_running_loop()

        def on_progress(done: int, total: int, project: Project) -> None:
            loop.call_soon_threadsafe(busy.set_count, done, total, project.path)

        try:
            # collect_all updates the Project objects in place
            await asyncio.to_thread(
                git_backend.collect_all,
                self.projects if only is None else only,
                self.config.ignore,
                self.config.max_workers,
                on_progress,
            )
        finally:
            header.set_syncing(False)
            if own_dialog:
                await busy.dismiss()
        self._apply_filters()

    async def _background_refresh(self) -> None:
        """Auto-refresh: rescan a copy of the projects with no dialog, then
        copy the results in -- unless a foreground scan started meanwhile."""
        generation = self._scan_generation
        snapshot = copy.deepcopy(self.projects)
        header = self.query_one("#header", HeaderBar)
        header.set_syncing(True)
        try:
            await asyncio.to_thread(
                git_backend.collect_all, snapshot, self.config.ignore, self.config.max_workers
            )
        finally:
            header.set_syncing(False)
        if generation == self._scan_generation:
            # Copy the results onto the live objects rather than swapping the
            # list: an open menu or a running command may hold a Project, and
            # a later refresh of it must update what the list shows.
            for live, fresh in zip(self.projects, snapshot):
                vars(live).update(vars(fresh))
            self._apply_filters()

    def _auto_refresh_tick(self) -> None:
        # Never under a dialog or while a command/scan is running.
        if len(self.screen_stack) > 1 or any(w.is_running for w in self.workers):
            return
        self.run_worker(self._background_refresh(), exclusive=True, group="auto-refresh")

    def _restart_auto_refresh(self) -> None:
        if self._auto_timer is not None:
            self._auto_timer.stop()
            self._auto_timer = None
        if self.config.auto_refresh_minutes:
            self._auto_timer = self.set_interval(
                self.config.auto_refresh_minutes * 60, self._auto_refresh_tick
            )

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

    def action_refresh_project(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._refresh_project(project)

    def _refresh_project(self, project: Project) -> None:
        """Re-scan just this project, e.g. after editing it outside repo-tui."""
        self.run_worker(self._refresh(only=[project]), exclusive=True, group="refresh")

    def action_settings(self) -> None:
        self.push_screen(
            SettingsScreen(self.config, list(self.available_themes), self._apply_setting)
        )

    def _apply_theme(self, theme: str) -> bool:
        try:
            self.theme = theme
        except Exception:  # noqa: BLE001 - unknown theme name from a config file
            return False
        return True

    def _apply_setting(self, name: str, value) -> str:
        """Apply one change from the settings screen right away, and save
        it. Returns the status line the settings screen shows."""
        if name == "theme" and not self._apply_theme(value):
            return f"[b $text-error]Unknown theme: {escape(str(value))}[/]"
        setattr(self.config, name, value)
        if name == "highlight_hints":
            hints.set_highlight(value)
            self.query_one("#keybar", KeyBar).show_top_level()
            self._sync_detail_pane()
        elif name == "sort":
            self._apply_filters()
        elif name == "auto_refresh_minutes":
            self._restart_auto_refresh()
        try:
            path = config_mod.save_setting(name, value)
        except OSError as exc:
            return f"[b $text-error]Applied, but not saved: {escape(str(exc))}[/]"
        return f"[$text-success]✓ Saved[/] [dim]to {escape(str(path))}[/]"

    async def _show_diff(self, project: Project, rel_path: str) -> None:
        text = await asyncio.to_thread(git_backend.read_file_diff, project, rel_path)
        self.push_screen(DiffScreen(f"{project.path}/{rel_path}", text))

    # -- per-project actions: shared by the action keys and the
    #    right-click context menu ----------------------------------------

    async def _run_and_refresh(
        self,
        title: str,
        coro_factory,
        command: str = "",
        jobs: int | None = None,
        project: Project | None = None,
        track_updates: bool = False,
    ) -> None:
        """Run a repo command behind a blocking BusyModal, refresh status,
        then show the command's output in a ResultScreen that pulls out any
        error/warning lines, plus a toast when there were problems.
        `coro_factory(on_output, on_progress)` starts the command;
        `command`/`jobs` are only shown in the dialog. With `project`, only
        that project is re-scanned afterwards. With `track_updates` (sync,
        detach), each project's HEAD is recorded before and compared after,
        and what moved becomes the new sync report."""
        header = self.query_one("#header", HeaderBar)
        header.set_syncing(True)
        busy = BusyModal(title, command=command, jobs=jobs)
        await self.push_screen(busy)
        log_lines: list[str] = []
        targets = [project] if project else list(self.projects)
        before: dict[str, str] = {}
        report: SyncReport | None = None
        if track_updates:
            busy.set_phase("Recording current commits…")
            before = await asyncio.to_thread(
                git_backend.head_commits, targets, self.config.max_workers
            )
            busy.set_phase("Running…")

        async def on_output(line: str) -> None:
            log_lines.append(line)
            busy.add_output(line)

        async def on_progress(line: str) -> None:
            busy.set_progress(line)

        try:
            try:
                returncode = await coro_factory(on_output, on_progress)
            except Exception as exc:  # noqa: BLE001 - e.g. `repo` not on PATH
                log_lines.append(f"error: {exc}")
                returncode = None
            if track_updates:
                report = await self._compare_commits(busy, title, returncode, targets, before)
            await self._refresh(busy, only=[project] if project else None)
        finally:
            header.set_syncing(False)
            await busy.dismiss()
        result = ResultScreen(title, log_lines, returncode, report)
        if result.severity == "information" and self.config.result_screen == "problems":
            updated = f", {len(report.changes)} updated" if report else ""
            self.notify(f"{escape(title)} — {result.status}{updated}", timeout=4)
            return
        self.push_screen(result)
        if result.severity != "information":
            first = next((line for _, line in result.problem_lines), "")
            self.notify(
                escape(first or result.status),
                title=f"{title} — {result.status}",
                severity=result.severity,
                timeout=10,
            )

    async def _compare_commits(
        self,
        busy: BusyModal,
        title: str,
        returncode: int | None,
        targets: list[Project],
        before: dict[str, str],
    ) -> SyncReport:
        busy.set_phase("Comparing commits…")
        workers = self.config.max_workers
        after = await asyncio.to_thread(git_backend.head_commits, targets, workers)
        changes = await asyncio.to_thread(
            git_backend.head_changes, targets, before, after, workers
        )
        report = SyncReport(title, time.time(), returncode, len(targets), changes)
        self.sync_report = report
        self.query_one("#header", HeaderBar).set_report(report)
        return report

    def action_show_sync_report(self) -> None:
        if self.sync_report is None:
            self.notify("No sync has run yet in this session.", timeout=3)
            return
        self.push_screen(
            SyncReportScreen(self.sync_report, {p.path: p for p in self.projects})
        )

    def action_show_changed(self) -> None:
        def handle(path: str | None) -> None:
            if path:
                self._jump_to_project(path)

        self.push_screen(ChangedScreen(self.projects), handle)

    def _jump_to_project(self, path: str) -> None:
        project_list = self.query_one("#project-list", ProjectList)
        if not project_list.select_path(path):  # hidden by the filter
            self.search = ""
            self.query_one("#filter-bar", FilterInput).value = ""
            self._apply_filters()
            project_list.select_path(path)
        project_list.focus()

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
                project=project,
                track_updates=True,
            ),
            exclusive=True,
            group="sync",
        )

    def _sync_all(self) -> None:
        if self.config.confirm_sync_all:
            command = " ".join(repo_backend.sync_args(jobs=self.config.sync_jobs))

            def handle(confirmed: bool | None) -> None:
                if confirmed:
                    self._start_sync_all()

            self.push_screen(
                ConfirmModal(
                    f"Sync all {len(self.projects)} projects?",
                    f"[dim]$ {escape(command)}[/]",
                ),
                handle,
            )
        else:
            self._start_sync_all()

    def _start_sync_all(self) -> None:
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
                track_updates=True,
            ),
            exclusive=True,
            group="sync",
        )

    def _detach_project(self, project: Project) -> None:
        self.run_worker(
            self._run_and_refresh(
                f"repo sync -d: {project.path}",
                lambda on_output, on_progress: repo_backend.sync_detach(
                    self.repo_root, [project.path], on_output=on_output, on_progress=on_progress
                ),
                command=" ".join(repo_backend.sync_args([project.path], detach=True)),
                project=project,
                track_updates=True,
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
                project=project,
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
                    project=project,
                ),
                exclusive=True,
                group="branch",
            )

        self.push_screen(
            PromptModal(f"New branch name for {project.path}:", "branch name"),
            handle,
        )

    def _discard_changes(self, project: Project) -> None:
        files = [f for f in project.files if not f.ignored]
        if not files:
            self.notify(f"{project.path} is already clean")
            return
        untracked = sum(1 for f in files if f.is_untracked)
        shown = "\n".join(f"  {escape(f.code)}  {escape(f.path)}" for f in files[:12])
        if len(files) > 12:
            shown += f"\n  …and {len(files) - 12} more"
        body = (
            f"{shown}\n\n"
            f"Tracked changes are reverted to HEAD"
            + (f" and [b]{untracked} untracked file(s) are deleted[/]" if untracked else "")
            + ".\n[b red]This cannot be undone.[/] Ignored files, commits and the branch are kept."
        )

        async def discard(on_output, _progress) -> int:
            returncode, lines = await asyncio.to_thread(git_backend.discard_changes, project)
            for line in lines:
                await on_output(line)
            return returncode

        def handle(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self.run_worker(
                self._run_and_refresh(
                    f"discard changes: {project.path}",
                    discard,
                    command="git restore --source=HEAD --staged --worktree … && git clean -f …",
                    project=project,
                ),
                exclusive=True,
                group="discard",
            )

        self.push_screen(
            ConfirmModal(f"Discard {len(files)} changed file(s) in {project.path}?", body),
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
            ("refresh", "Refresh status"),
            ("sync", "Sync (repo sync)"),
            ("detach", "Detach to manifest revision (repo sync -d)"),
            ("switch", "Switch branch…"),
            ("start", "Start new branch…"),
            ("discard", "Discard changes…"),
            ("copy", "Copy path"),
        ]

        def handle(choice: str | None) -> None:
            if choice == "refresh":
                self._refresh_project(project)
            elif choice == "sync":
                self._sync_project(project)
            elif choice == "detach":
                self._detach_project(project)
            elif choice == "switch":
                self._prompt_switch_branch(project)
            elif choice == "start":
                self._prompt_start_branch(project)
            elif choice == "discard":
                self._discard_changes(project)
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

    def action_discard(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._discard_changes(project)

    def action_copy_path(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._copy_project_path(project)
