"""Data models describing the state of a repo tree and its projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileStatus:
    """The status of a single file inside a project's git worktree."""

    path: str
    index_status: str  # status code in the index (staged), e.g. 'M', 'A', 'D', ' '
    worktree_status: str  # status code in the worktree (unstaged), e.g. 'M', '?'
    added: int = 0
    deleted: int = 0
    ignored: bool = False

    @property
    def is_untracked(self) -> bool:
        return self.index_status == "?" and self.worktree_status == "?"

    @property
    def is_staged(self) -> bool:
        return self.index_status not in (" ", "?")

    @property
    def code(self) -> str:
        """Two-character status code, git-status style, e.g. ' M', 'A ', '??'."""
        return f"{self.index_status}{self.worktree_status}"


@dataclass
class Project:
    """The aggregated status of one project (one git checkout) in the tree."""

    path: str  # path relative to the repo root
    name: str  # project name/remote path from the manifest
    abs_path: Path

    manifest_revision: str = ""  # branch/revision the manifest pins this project to
    current_branch: str = ""  # current checked-out branch, or '' if detached
    is_detached: bool = False

    ahead: int = 0
    behind: int = 0
    local_branch_count: int = 0

    added: int = 0
    deleted: int = 0
    untracked_count: int = 0

    files: list[FileStatus] = field(default_factory=list)

    error: str = ""  # populated if status collection failed for this project

    @property
    def has_visible_changes(self) -> bool:
        return any(not f.ignored for f in self.files)

    @property
    def off_manifest_branch(self) -> bool:
        if not self.manifest_revision:
            return False
        # manifest revision may be a full ref, short branch name, or a sha/tag.
        rev = self.manifest_revision.rsplit("/", 1)[-1]
        return bool(self.current_branch) and self.current_branch != rev

    @property
    def is_changed(self) -> bool:
        """Whether this project deserves the user's attention."""
        if self.error:
            return True
        if self.has_visible_changes:
            return True
        if self.ahead or self.behind:
            return True
        if self.off_manifest_branch:
            return True
        return False

    @property
    def stat_summary(self) -> str:
        parts = []
        if self.added or self.deleted:
            parts.append(f"+{self.added}/-{self.deleted}")
        if self.untracked_count:
            parts.append(f"?{self.untracked_count}")
        return " ".join(parts) if parts else "clean"

    @property
    def branch_summary(self) -> str:
        if self.is_detached:
            return "(detached)"
        label = self.current_branch or "?"
        if self.ahead:
            label += f" ↑{self.ahead}"
        if self.behind:
            label += f" ↓{self.behind}"
        return label


@dataclass
class TreeInfo:
    """Overall info about the repo tree, independent of any single project."""

    root: Path
    manifest_name: str = ""
    manifest_branch: str = ""
    manifest_groups: str = ""
    total_projects: int = 0
