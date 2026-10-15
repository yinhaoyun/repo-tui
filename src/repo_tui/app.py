"""The repo-tui Textual application."""

from __future__ import annotations

import asyncio
import copy
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DataTable, Input

from . import __version__
from . import config as config_mod
from . import git_backend
from . import hints
from . import repo_backend
from . import self_update
from . import themes
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
from .widgets.picker_modal import PickerModal
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
        # Lowercase acts on the selected project, Shift on the whole tree --
        # the same rule as s/S in the Space action panel.
        Binding("r", "refresh_project", "Refresh project"),
        Binding("R", "refresh_status", "Refresh all"),
        Binding("w", "show_changed", "Why changed"),
        Binding("u", "show_sync_report", "Last sync report"),
        Binding("o", "settings", "Settings"),
        # Updates repo-tui itself (asks first), never the tree.
        Binding("U", "update_app", "Update repo-tui"),
        Binding("v", "check_update", "Version / check for update"),
        Binding("q", "quit", "Quit"),
        # The top layer only looks: anything that changes a checkout (sync,
        # detach, branch, checkout, discard, forall) is behind Space.
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
        self._update_timer = None
        # What the update checks last told the user, so a periodic check
        # only pops up when something changed.
        self._update_behind_notified = 0
        self._update_error_notified = ""
        self.sync_report: SyncReport | None = None
        # Bumped by every foreground status scan, so a background scan that
        # was overtaken by one throws its (now older) results away.
        self._scan_generation = 0
        # Set when the user asks to restart into a freshly updated repo-tui;
        # __main__ re-execs after the app exits.
        self.restart_requested = False

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
        self._register_themes()
        if not self._apply_theme(self.config.theme):
            self.notify(
                f"Unknown theme {escape(repr(self.config.theme))}; using {escape(self.theme)}. "
                "Pick one with o → Display → Theme.",
                severity="warning",
                timeout=10,
            )
        self._restart_auto_refresh()
        self.run_worker(self._refresh(), exclusive=True, group="refresh")
        self._restart_update_checks(check_now=True)

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
        busy = any(w.is_running and w.group != "update-check" for w in self.workers)
        if len(self.screen_stack) > 1 or busy:
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

    def on_descendant_focus(self, event) -> None:  # noqa: ANN001 - textual event
        """Switch the key bar's focus line to whatever just got focus on
        the main screen (focus inside a dialog doesn't change it)."""
        widget = event.widget
        if widget.screen is not self.screen_stack[0]:
            return
        context = {"project-list": "projects", "file-table": "files", "filter-bar": "filter"}
        if widget.id in context:
            self.query_one("#keybar", KeyBar).show_top_level(context[widget.id])

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
        self.run_worker(self._show_diff(project, file), exclusive=True, group="diff")

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

    def _register_themes(self) -> None:
        """repo-tui's extra themes, then the user's own from the config file
        (which may replace one of the extras by using its name)."""
        mine, errors = themes.custom_themes(self.config.custom_themes)
        for theme in themes.EXTRA_THEMES + mine:
            self.register_theme(theme)
        if errors:
            source = f" in {self.config.source}" if self.config.source else ""
            shown = "\n".join(escape(e) for e in errors[:6])
            more = f"\n…and {len(errors) - 6} more" if len(errors) > 6 else ""
            self.notify(
                shown + more,
                title=f"Skipped custom theme(s): problems{escape(source)}",
                severity="warning",
                timeout=15,
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
        elif name in ("check_updates", "update_check_hours"):
            self._restart_update_checks(check_now=name == "check_updates" and value)
        try:
            path = config_mod.save_setting(name, value)
        except OSError as exc:
            return f"[b $text-error]Applied, but not saved: {escape(str(exc))}[/]"
        return f"[$text-success]✓ Saved[/] [dim]to {escape(str(path))}[/]"

    # -- updating repo-tui itself ----------------------------------------

    def _restart_update_checks(self, check_now: bool = False) -> None:
        """(Re)start the periodic update check from the settings; with
        `check_now`, also check right away (startup, or just switched on)."""
        if self._update_timer is not None:
            self._update_timer.stop()
            self._update_timer = None
        if not self.config.check_updates:
            return
        self._update_timer = self.set_interval(
            self.config.update_check_hours * 3600, self._update_check_tick
        )
        if check_now:
            self._update_check_tick()

    def _update_check_tick(self) -> None:
        # Not while an update itself (or another check) is running.
        if any(w.is_running and w.group in ("update", "update-check") for w in self.workers):
            return
        self.run_worker(self._check_for_update(), group="update-check")

    async def _check_for_update(self) -> None:
        """Background check: say so if GitHub has a newer repo-tui; say why
        if the check failed (except for installs that can't self-update
        anyway, which would only nag). The header always shows the latest
        result; a popup only when it's news: more new commits than last
        told, or a different error."""
        check = await asyncio.to_thread(self_update.check_for_update)
        if check.not_a_checkout:
            return
        if check.error:
            if check.error != self._update_error_notified:
                self.notify(
                    escape(check.error),
                    title="Couldn't check for repo-tui updates",
                    severity="warning",
                    timeout=8,
                )
            self._update_error_notified = check.error
            return
        self._update_error_notified = ""
        self.query_one("#header", HeaderBar).set_update_available(check.behind)
        if check.behind > self._update_behind_notified:
            self.notify(
                f"{check.behind} new commit(s) on GitHub. Press [b]U[/] to update.",
                title="repo-tui update available",
                timeout=10,
            )
        self._update_behind_notified = check.behind

    def action_check_update(self) -> None:
        self.run_worker(self._check_update_now(), exclusive=True, group="update-check")

    async def _check_update_now(self) -> None:
        """`v`: check GitHub right now and always say what was found --
        unlike the background check, "up to date" and repeated errors too."""
        busy = BusyModal("Checking GitHub for a newer repo-tui", show_log=False)
        await self.push_screen(busy)
        busy.set_phase("Fetching…")
        try:
            check = await asyncio.to_thread(self_update.check_for_update)
        finally:
            await busy.dismiss()
        version = f"repo-tui {__version__}" + (
            f" ({check.head})" if check.head else ""
        )
        if check.error:
            self._update_error_notified = check.error
            self.notify(
                escape(check.error),
                title=f"{escape(version)} — couldn't check for updates",
                severity="error",
                timeout=15,
            )
            return
        self._update_error_notified = ""
        self.query_one("#header", HeaderBar).set_update_available(check.behind)
        self._update_behind_notified = check.behind
        if not check.available:
            local = f"\n{check.ahead} local commit(s) not on GitHub." if check.ahead else ""
            self.notify(
                f"You're up to date.{local}", title=escape(version), timeout=6
            )
            return
        shown = "\n".join(escape(line) for line in check.commits[:5])
        if check.behind > 5:
            shown += f"\n…and {check.behind - 5} more"
        blocker = check.blocker
        self.notify(
            shown
            + (
                f"\n\n[b]Can't update yet:[/] {escape(blocker)}"
                if blocker
                else "\n\nPress [b]U[/] to update."
            ),
            title=f"{escape(version)} — {check.behind} new commit(s) on GitHub",
            severity="warning" if blocker else "information",
            timeout=15,
        )

    def action_update_app(self) -> None:
        self.run_worker(self._prepare_update(), exclusive=True, group="update")

    async def _prepare_update(self) -> None:
        busy = BusyModal("Checking for repo-tui updates", show_log=False)
        await self.push_screen(busy)
        busy.set_phase("Fetching from GitHub…")
        try:
            check = await asyncio.to_thread(self_update.check_for_update)
        finally:
            await busy.dismiss()
        header = self.query_one("#header", HeaderBar)
        if not check.error:
            header.set_update_available(check.behind)
        if check.blocker:
            self.notify(
                escape(check.blocker),
                title="Can't update repo-tui",
                severity="error",
                timeout=15,
            )
            return
        if not check.available:
            local = (
                f" ({check.ahead} local commit(s) not on GitHub)" if check.ahead else ""
            )
            self.notify(f"repo-tui is up to date{escape(local)}.", timeout=4)
            return
        shown = "\n".join(f"  {escape(line)}" for line in check.commits)
        if check.behind > len(check.commits):
            shown += f"\n  …and {check.behind - len(check.commits)} more"
        body = (
            f"{shown}\n\n[dim]in {escape(str(check.root))}\n"
            "$ git pull --ff-only, then reinstall[/]\n"
            "Restart repo-tui afterwards to use the new version (it offers to)."
        )

        def handle(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(self._run_update(), exclusive=True, group="update")

        self.push_screen(
            ConfirmModal(f"Update repo-tui? {check.behind} new commit(s)", body), handle
        )

    async def _run_update(self) -> None:
        busy = BusyModal("Updating repo-tui", command="git pull --ff-only && reinstall")
        await self.push_screen(busy)
        lines: list[str] = []
        loop = asyncio.get_running_loop()

        def say(message: str, is_error: bool) -> None:
            for i, line in enumerate(message.splitlines()):
                line = f"error: {line}" if is_error and i == 0 else line
                lines.append(line)
                loop.call_soon_threadsafe(busy.add_output, line)

        try:
            returncode = await asyncio.to_thread(self_update.update, say, True)
        except Exception as exc:  # noqa: BLE001 - shown, not raised into the UI
            say(str(exc), True)
            returncode = None
        finally:
            await busy.dismiss()
        updated = returncode == 0 and not any("already up to date" in l for l in lines)
        if updated:
            self.query_one("#header", HeaderBar).set_update_available(0)
            self._update_behind_notified = 0
        result = ResultScreen("update repo-tui", lines, returncode)

        def offer_restart(_=None) -> None:
            if updated:
                self.push_screen(
                    ConfirmModal(
                        "repo-tui was updated. Restart it now?",
                        "The running copy is still the old version until it restarts.",
                    ),
                    self._restart_if_confirmed,
                )

        self.push_screen(result, offer_restart)
        if result.severity != "information":
            first = next((line for _, line in result.problem_lines), "")
            self.notify(
                escape(first or result.status),
                title=f"update repo-tui — {result.status}",
                severity=result.severity,
                timeout=10,
            )

    def _restart_if_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self.restart_requested = True
            self.exit()

    async def _show_diff(self, project: Project, file) -> None:
        text = await asyncio.to_thread(git_backend.read_file_diff, project, file)
        self.push_screen(DiffScreen(f"{project.path}/{file.path}", text))

    # -- file actions: the file at the file list's cursor ----------------
    #    (l/b/e/y while the list has focus; a/u/r in the Space panel)

    def _selected_file(self):
        """(project, file) at the file list's cursor, or None (and says so)."""
        project = self._selected_project()
        file = self.query_one("#file-table", FileTable).file_at_cursor()
        if project is None or file is None:
            self.notify("No file selected.", severity="warning", timeout=3)
            return None
        return project, file

    def action_file_log(self) -> None:
        selected = self._selected_file()
        if selected is None:
            return
        project, file = selected

        async def show() -> None:
            text = await asyncio.to_thread(git_backend.file_log, project, file.path)
            if text and project.is_shallow:
                text += (
                    "\n(shallow clone: older history isn't here. "
                    "Space h in the project list fetches it.)"
                )
            self.push_screen(
                DiffScreen(
                    f"history: {project.path}/{file.path}",
                    text,
                    empty="(no commits touch this file yet)",
                )
            )

        self.run_worker(show(), exclusive=True, group="diff")

    def action_file_blame(self) -> None:
        selected = self._selected_file()
        if selected is None:
            return
        project, file = selected
        if file.is_untracked:
            self.notify(f"{escape(file.path)} is untracked: nothing to blame yet.", timeout=4)
            return

        async def show() -> None:
            text = await asyncio.to_thread(git_backend.file_blame, project, file.path)
            self.push_screen(
                DiffScreen(
                    f"blame: {project.path}/{file.path}",
                    text,
                    empty="(no blame — binary, deleted, or not committed yet)",
                )
            )

        self.run_worker(show(), exclusive=True, group="diff")

    def action_copy_file_path(self) -> None:
        selected = self._selected_file()
        if selected is not None:
            project, file = selected
            self._copy_text(str(project.abs_path / file.path), "File path")

    def action_edit_file(self) -> None:
        """Open the file in $VISUAL / $EDITOR (vi if neither is set), with
        the TUI suspended, then rescan the project."""
        selected = self._selected_file()
        if selected is None:
            return
        project, file = selected
        path = project.abs_path / file.path
        if not path.is_file():
            self.notify(
                f"{escape(file.path)} doesn't exist in the working tree (deleted?).",
                severity="warning",
                timeout=5,
            )
            return
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        try:
            argv = shlex.split(editor)
        except ValueError as exc:
            self.notify(f"Can't parse $EDITOR {escape(editor)!r}: {escape(str(exc))}",
                        severity="error", timeout=10)
            return
        if not argv or shutil.which(argv[0]) is None:
            self.notify(
                f"Editor {escape(editor)!r} not found. Set $VISUAL or $EDITOR.",
                severity="error",
                timeout=10,
            )
            return
        try:
            with self.suspend():
                returncode = subprocess.call([*argv, str(path)], cwd=project.abs_path)
        except Exception as exc:  # noqa: BLE001 - e.g. suspend unsupported here
            self.notify(f"Couldn't run the editor: {escape(str(exc))}", severity="error",
                        timeout=10)
            return
        if returncode:
            self.notify(f"{escape(argv[0])} exited with {returncode}.", severity="warning",
                        timeout=5)
        self._refresh_project(project)

    async def _run_file_op(self, project: Project, done: str, func, *args) -> None:
        """Run a quick git file operation, rescan the project, and say how
        it went: a toast on success, git's own message on failure."""
        try:
            returncode, lines = await asyncio.to_thread(func, project, *args)
        except Exception as exc:  # noqa: BLE001 - e.g. git timed out
            returncode, lines = 1, [str(exc)]
        await self._refresh(only=[project])
        if returncode:
            problems = [line for line in lines if line.startswith(("error", "fatal"))]
            self.notify(
                escape("\n".join(problems or lines[-3:]) or f"git exited with {returncode}"),
                title=f"Failed: {done}",
                severity="error",
                timeout=12,
            )
        else:
            self.notify(escape(done), timeout=3)

    def action_stage_file(self) -> None:
        selected = self._selected_file()
        if selected is None:
            return
        project, file = selected
        self.run_worker(
            self._run_file_op(project, f"Staged {file.path}", git_backend.stage_file, file),
            exclusive=True,
            group="file",
        )

    def action_unstage_file(self) -> None:
        selected = self._selected_file()
        if selected is None:
            return
        project, file = selected
        if not file.is_staged:
            self.notify(f"{escape(file.path)} has nothing staged.", timeout=3)
            return
        self.run_worker(
            self._run_file_op(project, f"Unstaged {file.path}", git_backend.unstage_file, file),
            exclusive=True,
            group="file",
        )

    def action_revert_file(self) -> None:
        selected = self._selected_file()
        if selected is None:
            return
        project, file = selected
        if file.is_untracked:
            what = "[b]This untracked file is deleted.[/]"
        elif file.orig_path:
            what = f"The rename from {escape(file.orig_path)} is undone, back to HEAD."
        else:
            what = "Its staged and unstaged changes are reverted to HEAD."
        body = (
            f"  {escape(file.code)}  {escape(file.path)}\n\n{what}\n"
            "[b red]This cannot be undone.[/]"
            + ("\n[dim]It's matched by your ignore rules.[/]" if file.ignored else "")
        )

        def handle(confirmed: bool | None) -> None:
            if confirmed:
                self.run_worker(
                    self._run_file_op(
                        project, f"Reverted {file.path}", git_backend.discard_files, [file]
                    ),
                    exclusive=True,
                    group="file",
                )

        self.push_screen(ConfirmModal(f"Revert {escape(file.path)} in {escape(project.path)}?", body), handle)

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
        reload_tree: bool = False,
    ) -> None:
        """Run a repo command behind a blocking BusyModal, refresh status,
        then show the command's output in a ResultScreen that pulls out any
        error/warning lines, plus a toast when there were problems.
        `coro_factory(on_output, on_progress)` starts the command;
        `command`/`jobs` are only shown in the dialog. With `project`, only
        that project is re-scanned afterwards. With `track_updates` (sync,
        detach), each project's HEAD is recorded before and compared after,
        and what moved becomes the new sync report. With `reload_tree`
        (manifest switch), the project list is re-read from the manifest
        before the rescan."""
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
            if reload_tree:
                log_lines += self._reload_tree()
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

    def _reload_tree(self) -> list[str]:
        """Re-read the manifest after it changed: header info and the
        project list. Returns lines saying what changed, for the output."""
        old_paths = {p.path for p in self.projects}
        self.tree_info = repo_backend.load_tree_info(self.repo_root)
        self.query_one("#header", HeaderBar).set_tree_info(self.tree_info)
        self.projects = repo_backend.load_projects(self.repo_root)
        self.tree_info.total_projects = len(self.projects)
        new_paths = {p.path for p in self.projects}
        added, removed = new_paths - old_paths, old_paths - new_paths
        lines = [
            f"repo-tui: now on manifest {self.tree_info.manifest_branch or '?'}"
            f" / {self.tree_info.manifest_name or '?'}, {len(self.projects)} projects"
            f" ({len(added)} added, {len(removed)} removed)"
        ]
        lines += [f"  + {path}" for path in sorted(added)]
        lines += [f"  - {path}" for path in sorted(removed)]
        leftover = sorted(path for path in removed if (self.repo_root / path / ".git").exists())
        if leftover:
            lines.append(
                f"warning: {len(leftover)} project(s) are no longer in the manifest but "
                "are still on disk (repo keeps checkouts with local changes): "
                + ", ".join(leftover[:10])
                + (" …" if len(leftover) > 10 else "")
            )
        return lines

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

    def _unshallow_project(self, project: Project) -> None:
        """Fetch one project's full history, after a y/N that says what it
        costs and when a later sync could undo it."""
        if project.error:
            self.notify(
                f"{escape(project.path)} couldn't be scanned: {escape(project.error)}",
                severity="error",
                timeout=8,
            )
            return
        if not project.is_shallow:
            self.notify(f"{escape(project.path)} already has its full history.", timeout=4)
            return
        remote, why = git_backend.fetch_remote(project)
        if why:
            self.notify(escape(why), title="Can't unshallow", severity="error", timeout=10)
            return
        command = " ".join(repo_backend.unshallow_args(remote))
        reshallow = []
        if project.clone_depth:
            reshallow.append(f"the manifest sets clone-depth=\"{escape(project.clone_depth)}\"")
        depth = repo_backend.init_depth(self.repo_root)
        if depth:
            reshallow.append(f"this tree was set up with repo init --depth={escape(depth)}")
        body = (
            f"Fetches all of {escape(project.path)}'s history from "
            f"[b]{escape(remote)}[/]. Nothing in the working tree changes.\n"
            f"[dim]$ {escape(command)}[/]\n\n"
            "[b yellow]![/] This can be a big download (gigabytes for large "
            "projects) and take a while; it needs network access to the server."
        )
        if reshallow:
            body += (
                f"\n[b yellow]![/] {' and '.join(reshallow)}, so a later repo sync "
                "may fetch it shallow again."
            )

        def handle(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self.run_worker(
                self._run_and_refresh(
                    f"unshallow: {project.path}",
                    lambda on_output, on_progress: repo_backend.unshallow(
                        project, remote, on_output=on_output, on_progress=on_progress
                    ),
                    command=command,
                    project=project,
                ),
                exclusive=True,
                group="sync",
            )

        self.push_screen(
            ConfirmModal(f"Unshallow {escape(project.path)}?", body), handle
        )

    def _copy_project_path(self, project: Project) -> None:
        self._copy_text(str(project.abs_path), "Project path")

    def _copy_text(self, text: str, what: str) -> None:
        try:
            self.copy_to_clipboard(text)
            self.notify(f"Copied: {escape(text)}")
        except Exception:
            self.notify(escape(text), title=what)

    def _open_context_menu(self, project: Project) -> None:
        options = [
            ("refresh", "Refresh status"),
            ("sync", "Sync (repo sync)"),
            ("detach", "Detach to manifest revision (repo sync -d)"),
            ("switch", "Checkout branch…"),
            ("start", "Start new branch…"),
            ("discard", "Discard changes…"),
            ("copy", "Copy path"),
        ]
        if project.is_shallow:
            options.insert(-1, ("unshallow", "Unshallow (fetch full history)…"))

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
            elif choice == "unshallow":
                self._unshallow_project(project)

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

        # Focus-based: from the file list, the panel also offers the file's actions.
        file = None
        if isinstance(self.focused, FileTable):
            file = self.focused.file_at_cursor()
        self.push_screen(
            ActionPanel(project.path if project else None, file.path if file else None),
            handle,
        )

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

    # -- switch manifest branch / file (whole tree) ----------------------

    def action_switch_manifest(self) -> None:
        self.run_worker(self._prepare_switch_manifest(), exclusive=True, group="manifest")

    async def _prepare_switch_manifest(self) -> None:
        """Fetch the manifest repo's branches and rescan status first, so
        the choices and the warnings before the switch are current."""
        busy = BusyModal("Switch manifest", show_log=False)
        await self.push_screen(busy)
        try:
            busy.set_phase("Fetching manifest branches…")
            fetch_error = await asyncio.to_thread(repo_backend.fetch_manifests, self.repo_root)
            branches = await asyncio.to_thread(repo_backend.manifest_branches, self.repo_root)
            await self._refresh(busy)
        finally:
            await busy.dismiss()
        if fetch_error:
            self.notify(
                escape(fetch_error),
                title="Couldn't fetch the manifest repo — branch list may be out of date",
                severity="warning",
                timeout=10,
            )
        current = self.tree_info.manifest_branch

        def handle(branch: str | None) -> None:
            if branch:
                self.run_worker(
                    self._pick_manifest_file(branch, branches, fetch_error),
                    exclusive=True,
                    group="manifest",
                )

        self.push_screen(
            PickerModal(
                "[b]Switch manifest[/] — step 1/2: manifest branch "
                f"[dim](now: {escape(current or '?')})[/]",
                branches,
                current,
                "filter, or type a branch name",
            ),
            handle,
        )

    async def _pick_manifest_file(
        self, branch: str, branches: list[str], fetch_error: str
    ) -> None:
        problem = _bad_name(branch, "Branch")
        if problem:
            self.notify(problem, severity="error", timeout=8)
            return
        files = await asyncio.to_thread(repo_backend.manifest_files, self.repo_root, branch)
        current = self.tree_info.manifest_name
        title = (
            f"[b]Switch manifest[/] — step 2/2: manifest file on [b]{escape(branch)}[/] "
            f"[dim](now: {escape(current or '?')})[/]"
        )
        if files is None:
            title += (
                "\n[b $text-warning]This branch isn't in the local copy of the manifest "
                "repo, so its files can't be listed — type the file name.[/]"
            )
        elif not files:
            title += "\n[b $text-warning]No .xml files found on this branch.[/]"

        def handle(manifest: str | None) -> None:
            if manifest:
                self._confirm_switch_manifest(
                    branch, manifest, branches, files, fetch_error
                )

        self.push_screen(
            PickerModal(title, files or [], current, "filter, or type a file name"),
            handle,
        )

    def _manifest_switch_warnings(
        self,
        branch: str,
        manifest: str,
        branches: list[str],
        files: list[str] | None,
        fetch_error: str,
    ) -> list[str]:
        """Everything worth knowing before switching, as markup lines:
        errors (it will fail) in red, warnings (it may hurt) in yellow."""
        err, warn = "[b red]✗[/] ", "[b yellow]![/] "
        out = []
        if shutil.which("repo") is None:
            out.append(err + "[b red]`repo` isn't on PATH — this will fail.[/]")
        if fetch_error:
            out.append(
                warn + "Couldn't fetch the manifest repo, so the lists may be out of date: "
                + f"[dim]{escape(fetch_error)}[/]"
            )
        if branch not in branches:
            out.append(
                warn + f"Branch [b]{escape(branch)}[/] isn't among the known manifest "
                "branches; repo init will fail if the server doesn't have it either."
            )
        if files is not None and manifest not in files:
            out.append(
                warn + f"[b]{escape(manifest)}[/] doesn't exist on {escape(branch)}; "
                "repo init will fail."
            )
        if not manifest.endswith(".xml"):
            out.append(warn + f"[b]{escape(manifest)}[/] doesn't end in .xml.")
        info = self.tree_info
        if branch == info.manifest_branch and manifest == info.manifest_name:
            out.append(
                warn + "That is the current branch and file; this just re-runs "
                "repo init and syncs the whole tree."
            )

        def listed(projects: list[Project], what: str, detail) -> None:
            if not projects:
                return
            names = ", ".join(escape(detail(p)) for p in projects[:6])
            more = f" …and {len(projects) - 6} more" if len(projects) > 6 else ""
            out.append(warn + f"{len(projects)} project(s) {what}: [dim]{names}{more}[/]")

        listed(
            [p for p in self.projects if p.error],
            "couldn't be scanned",
            lambda p: p.path,
        )
        listed(
            [p for p in self.projects if p.has_visible_changes],
            "have uncommitted changes — repo sync can fail on them or leave them "
            "on the old revision",
            lambda p: p.path,
        )
        listed(
            [p for p in self.projects if p.ahead],
            "have unpushed commits — they may be rebased onto the new revision",
            lambda p: f"{p.path} ↑{p.ahead}",
        )
        listed(
            [p for p in self.projects if p.current_branch and not p.is_detached],
            "are on a local branch — repo sync keeps them on it, not on the new "
            "manifest revision",
            lambda p: f"{p.path} ({p.current_branch})",
        )
        return out

    def _confirm_switch_manifest(
        self,
        branch: str,
        manifest: str,
        branches: list[str],
        files: list[str] | None,
        fetch_error: str,
    ) -> None:
        problem = _bad_name(manifest, "Manifest file")
        if problem:
            self.notify(problem, severity="error", timeout=8)
            return
        info = self.tree_info
        init_cmd = " ".join(repo_backend.init_args(branch, manifest))
        sync_cmd = " ".join(repo_backend.sync_args(jobs=self.config.sync_jobs))
        warnings = self._manifest_switch_warnings(branch, manifest, branches, files, fetch_error)
        body = (
            f"[dim]from[/] {escape(info.manifest_branch or '?')} / "
            f"{escape(info.manifest_name or '?')}\n"
            f"[dim]to  [/] [b]{escape(branch)}[/] / [b]{escape(manifest)}[/]\n\n"
            f"[dim]$ {escape(init_cmd)}\n$ {escape(sync_cmd)}[/]\n"
            "Every project is synced to the new manifest; projects it drops are "
            "removed (repo keeps ones with local changes)."
        )
        if warnings:
            body += "\n\n[b u]Check before you go on[/]\n" + "\n".join(warnings)

        def handle(confirmed: bool | None) -> None:
            if confirmed:
                self._start_switch_manifest(branch, manifest)

        self.push_screen(
            ConfirmModal(f"Switch the whole tree to {escape(branch)} / {escape(manifest)}?", body),
            handle,
        )

    def _start_switch_manifest(self, branch: str, manifest: str) -> None:
        jobs = self.config.sync_jobs
        self.run_worker(
            self._run_and_refresh(
                f"switch manifest: {branch} / {manifest}",
                lambda on_output, on_progress: repo_backend.switch_manifest(
                    self.repo_root,
                    branch,
                    manifest,
                    on_output=on_output,
                    on_progress=on_progress,
                    jobs=jobs,
                ),
                command=" ".join(repo_backend.init_args(branch, manifest))
                + " && "
                + " ".join(repo_backend.sync_args(jobs=jobs)),
                jobs=jobs,
                track_updates=True,
                reload_tree=True,
            ),
            exclusive=True,
            group="sync",
        )

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

    def action_unshallow(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._unshallow_project(project)

    def action_discard(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._discard_changes(project)

    def action_copy_path(self) -> None:
        project = self._selected_project()
        if project is not None:
            self._copy_project_path(project)


def _bad_name(value: str, what: str) -> str:
    """Why `value` can't be a branch/file name passed to repo init, or ""."""
    if value.startswith("-"):
        return f"{what} can't start with '-': {escape(value)}"
    if any(ch.isspace() for ch in value):
        return f"{what} can't contain spaces: {escape(value)}"
    return ""
