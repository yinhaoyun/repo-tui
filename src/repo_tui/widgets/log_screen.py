"""A project's (or one file's) history: the commits on the left, newest
first, with commits upstream doesn't have marked ↑; the highlighted one's
full message and changed files on the right. Enter opens its patch, / filters
by sha, subject, author or ref, y copies the sha.

Opened with l on the project list (the project's log) or the file list
(that file's, following renames), or from either's menu."""

from __future__ import annotations

import re
from typing import List, Optional, Set

from ..markup import escape
from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .. import git_backend
from ..compat import to_thread
from ..hints import close_tip, key, tip
from ..models import Commit, Project
from .diff_screen import DiffScreen, theme_palette

_STAT_RE = re.compile(r"^( .+? \| +\d+ )(\+*)(-*)$")


def details_text(text: str, palette: dict) -> Text:
    """commit_details' output with the headers dimmed, the commit id and
    refs coloured, and the --stat bars in the added/removed colours."""
    out: list = []
    for line in text.rstrip("\n").splitlines():
        if not line.strip() and out and not out[-1].plain.strip():
            continue  # one blank line between message and files is enough
        if line.startswith("commit "):
            out.append(Text(line, style=f"bold {palette['id']}"))
        elif line.startswith("Refs:"):
            if line[len("Refs:"):].strip():
                out.append(Text(line, style=palette["hunk"]))
        elif line.startswith(("Author:", "Date:", "Commit:")):
            out.append(Text(line, style=palette["muted"]))
        elif m := _STAT_RE.match(line):
            out.append(Text.assemble(
                m.group(1), (m.group(2), palette["added"]), (m.group(3), palette["removed"])
            ))
        else:
            out.append(Text(line))
    return Text("\n").join(out)


def matches(commit: Commit, needle: str) -> bool:
    needle = needle.strip().lower()
    return not needle or any(
        needle in field.lower()
        for field in (commit.sha, commit.subject, commit.author, commit.refs, commit.date)
    )


class LogScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
        ("slash", "filter", "Filter"),
        ("y", "copy_sha", "Copy sha"),
    ]

    def __init__(self, project: Project, path: str = "") -> None:
        self._project = project
        self._path = path
        self._commits: List[Commit] = []
        self._shown: List[Commit] = []
        self._local: Optional[Set[str]] = None
        super().__init__()

    def compose(self):
        what = f"{self._project.path}/{self._path}" if self._path else self._project.path
        heading = "File history" if self._path else "History"
        with Vertical(id="log-box"):
            yield Static(
                f"[b]{heading}: {escape(what)}[/]  {close_tip('esc', 'q')}", id="log-title"
            )
            yield Static("[dim]Loading…[/]", id="log-summary")
            yield Input(placeholder="filter by sha, subject, author, ref or date", id="log-filter")
            with Horizontal(id="log-body"):
                yield OptionList(id="log-list")
                yield RichLog(id="log-detail", wrap=True, markup=False, auto_scroll=False)
            yield Static(
                tip(
                    f"{key('↑↓')} pick  {key('enter')} full diff  {key('/')} filter  "
                    f"{key('y')} copy sha   [b]↑[/] = not upstream"
                ),
                id="log-keys",
            )

    def on_mount(self) -> None:
        self.query_one("#log-filter", Input).display = False
        self.query_one("#log-list", OptionList).focus()
        self.run_worker(self._load(), exclusive=True, group="log-load")

    async def _load(self) -> None:
        project = self._project
        self._commits = await to_thread(git_backend.history, project, self._path)
        self._local, behind = await to_thread(git_backend.history_context, project)
        count = len(self._commits)
        parts = [f"{count} commit{'s' if count != 1 else ''}"]
        if count >= git_backend.HISTORY_LIMIT:
            parts[0] += f" (the newest {count}; older ones not shown)"
        if self._local is not None:
            local = sum(1 for c in self._commits if c.sha in self._local)
            if local:
                parts.append(f"[b $text-warning]{local} not upstream[/]")
            if behind:
                parts.append(f"upstream has {behind} newer (space F to see, space S to sync)")
        if project.is_shallow:
            parts.append("[b $text-warning]shallow clone: older history isn't here[/] (space h)")
        self.query_one("#log-summary", Static).update(" · ".join(parts))
        self._fill()

    def _fill(self, needle: str = "") -> None:
        option_list = self.query_one("#log-list", OptionList)
        option_list.clear_options()
        self._shown = [c for c in self._commits if matches(c, needle)]
        palette = theme_palette(self.app)
        options = []
        for i, commit in enumerate(self._shown):
            local = self._local is not None and commit.sha in self._local
            label = Text.assemble(
                ("↑ " if local else "  ", f"bold {palette['id']}"),
                (commit.short, palette["id"]),
                " ",
                (commit.date, palette["muted"]),
                " ",
                commit.subject,
            )
            if commit.refs:
                label.append(f"  ({commit.refs})", style=palette["hunk"])
            options.append(Option(label, id=str(i)))
        option_list.add_options(options)
        log = self.query_one("#log-detail", RichLog)
        if self._shown:
            option_list.highlighted = 0
        else:
            log.clear()
            log.write(Text(
                "(no commits match)" if self._commits else "(no commits yet)",
                style="dim",
            ))

    def _highlighted(self) -> Optional[Commit]:
        index = self.query_one("#log-list", OptionList).highlighted
        if index is None or not (0 <= index < len(self._shown)):
            return None
        return self._shown[index]

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        commit = self._shown[int(event.option.id)]
        self.run_worker(self._show_details(commit), exclusive=True, group="log-detail")

    async def _show_details(self, commit: Commit) -> None:
        text = await to_thread(git_backend.commit_details, self._project, commit)
        log = self.query_one("#log-detail", RichLog)
        log.clear()
        log.write(details_text(text, theme_palette(self.app)))
        log.scroll_home(animate=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        commit = self._shown[int(event.option.id)]
        self.run_worker(self._show_patch(commit), exclusive=True, group="log-patch")

    async def _show_patch(self, commit: Commit) -> None:
        text = await to_thread(git_backend.commit_patch, self._project, commit)
        where = f"{self._project.path}/{commit.path}" if commit.path else self._project.path
        self.app.push_screen(
            DiffScreen(
                f"{commit.short} {commit.subject} — {where}",
                text,
                empty="(no changes to show — e.g. a merge commit)",
            )
        )

    # -- filter -------------------------------------------------------------

    def action_filter(self) -> None:
        field = self.query_one("#log-filter", Input)
        field.display = True
        field.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._fill(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#log-list", OptionList).focus()

    def action_close(self) -> None:
        """esc/q: in the filter, clear and leave it; otherwise close."""
        field = self.query_one("#log-filter", Input)
        if field.has_focus:
            field.value = ""
            field.display = False
            self._fill()
            self.query_one("#log-list", OptionList).focus()
            return
        self.dismiss(None)

    def action_copy_sha(self) -> None:
        commit = self._highlighted()
        if commit is not None:
            self.app._copy_text(commit.sha, "Commit")
