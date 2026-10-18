"""repo-tui's actions for the command palette (ctrl+p), so each can be
found by name ("abandon", "unshallow", "manifest") instead of by key. Each
command's help ends with its key, so the palette also teaches the keys.

Only what fits the moment is offered: project commands need a selected
project, unshallow a shallow one, file commands a file in the file list."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Tuple

if TYPE_CHECKING:
    from .app import RepoTuiApp

# (title, help, app action, key, what it needs: "" | "project" | "branches"
#  | "shallow" | "file" | "staged")
COMMANDS = [
    ("Sync project", "repo sync the selected project", "sync_selected", "space s", "project"),
    ("Sync all projects", "repo sync the whole tree", "sync_all", "space S", ""),
    ("Detach project to manifest revision", "repo sync -d -l the selected project, no fetch",
     "detach", "space d", "project"),
    ("Start new branch", "repo start a branch in the selected project", "start_branch",
     "space b", "project"),
    ("Checkout branch", "switch the selected project to another local branch",
     "switch_branch", "space c", "project"),
    ("Abandon branch", "repo abandon: delete a local branch of the selected project",
     "abandon_branch", "space k", "branches"),
    ("Abandon branch in every project", "repo abandon a branch wherever it exists",
     "abandon_tree", "space K", ""),
    ("Discard project changes", "revert tracked changes, delete untracked files (asks y/N)",
     "discard", "space x", "project"),
    ("Unshallow project", "fetch the selected project's full history", "unshallow",
     "space h", "shallow"),
    ("Run a command in every project (forall)", "repo forall -c <command>", "forall",
     "space f", ""),
    ("Switch manifest branch / file", "repo init -b -m, then sync the whole tree",
     "switch_manifest", "space M", ""),
    ("Refresh project", "rescan the selected project's status", "refresh_project", "r",
     "project"),
    ("Refresh all", "rescan every project's status", "refresh_status", "R", ""),
    ("Why projects count as changed", "list each changed project and why", "show_changed",
     "w", ""),
    ("Last sync report", "which projects the last sync moved, and what came in",
     "show_sync_report", "u", ""),
    ("Filter projects", "filter the project list by path/name", "toggle_filter", "/", ""),
    ("Copy project path", "copy the selected project's absolute path", "copy_path", "y",
     "project"),
    ("File: diff", "the file's changes since HEAD", "file_diff", "enter → Diff", "file"),
    ("File: history", "commits that touched the file", "file_log", "l", "file"),
    ("File: blame", "git blame the file", "file_blame", "b", "file"),
    ("File: edit", "open the file in $VISUAL / $EDITOR", "edit_file", "e", "file"),
    ("File: copy path", "copy the file's absolute path", "copy_file_path", "y", "file"),
    ("File: stage", "git add the file", "stage_file", "space a", "file"),
    ("File: unstage", "take the file out of the index", "unstage_file", "space u", "staged"),
    ("File: revert", "revert the file to HEAD (asks y/N)", "revert_file", "space r", "file"),
    ("Settings", "theme, sync jobs, sort, auto-refresh, …", "settings", "o", ""),
    ("Theme", "pick a colour theme (saved to your settings)", "pick_theme", "o → Theme", ""),
    ("Check for repo-tui update", "show the running version and check GitHub now",
     "check_update", "v", ""),
    ("Update repo-tui", "pull and reinstall repo-tui from GitHub (asks first)", "update_app",
     "U", ""),
    ("Help: key reference", "every key, by where it works", "toggle_help", "?", ""),
]


def available(app: RepoTuiApp) -> Iterator[Tuple[str, str, str]]:
    """(title, help, action) for each command that fits right now."""
    project = app._selected_project()
    file = app.query_one("#file-table").file_at_cursor() if project else None
    ok = {
        "": True,
        "project": project is not None,
        "branches": project is not None and bool(project.local_branches),
        "shallow": project is not None and project.is_shallow,
        "file": file is not None,
        "staged": file is not None and file.is_staged,
    }
    where = f" — {project.path}" if project else ""
    for title, help_text, action, key, needs in COMMANDS:
        if not ok[needs]:
            continue
        if needs in ("project", "branches", "shallow"):
            help_text += where
        elif needs in ("file", "staged"):
            help_text += f" — {file.path}"
        yield title, f"{help_text}  ·  {key}", action
    # Toggles say what they'd do now.
    yield (
        "Hide unchanged projects" if app.show_all else "Show all projects",
        "toggle showing projects with nothing changed  ·  a",
        "toggle_show_all",
    )
    yield (
        "Hide ignored files" if app.show_ignored else "Show ignored files",
        "toggle files matched by your ignore rules in the file list  ·  i",
        "toggle_show_ignored",
    )
