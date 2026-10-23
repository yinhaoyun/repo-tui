"""Data models describing the state of a repo tree and its projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class FileStatus:
    """The status of a single file inside a project's git worktree."""

    path: str
    index_status: str  # status code in the index (staged), e.g. 'M', 'A', 'D', ' '
    worktree_status: str  # status code in the worktree (unstaged), e.g. 'M', '?'
    added: int = 0
    deleted: int = 0
    ignored: bool = False
    orig_path: str = ""  # for renames/copies: the path it was renamed from

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
    remote: str = ""  # the manifest's remote for this project (git remote name)
    is_shallow: bool = False  # a shallow clone (repo init --depth / clone-depth)
    clone_depth: str = ""  # the manifest's clone-depth for this project, if any
    current_branch: str = ""  # current checked-out branch, or '' if detached
    is_detached: bool = False

    ahead: int = 0
    behind: int = 0
    local_branch_count: int = 0
    local_branches: list[str] = field(default_factory=list)

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
        # manifest revision may be a full ref, short branch name, or a sha/tag;
        # a branch name may itself contain slashes (release/1.0).
        rev = self.manifest_revision
        for prefix in ("refs/heads/", "refs/tags/"):
            if rev.startswith(prefix):
                rev = rev[len(prefix):]
                break
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
        # `repo sync` leaves untouched projects in detached HEAD, so being on
        # any local branch (even one named like the manifest revision) means
        # someone started work here with `repo start`/checkout.
        if self.current_branch and not self.is_detached:
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
class Commit:
    """One commit in a project's (or a file's) history."""

    sha: str
    short: str
    date: str  # YYYY-MM-DD
    author: str
    refs: str  # git's decorations: "HEAD -> topic, aosp/main, tag: v1"
    subject: str
    path: str = ""  # a file's history: its name in this commit (renames)


@dataclass
class HeadChange:
    """A project whose checked-out commit moved during a sync."""

    path: str
    old: str  # full sha before
    new: str  # full sha after
    added: int = 0  # commits in old..new (what the sync brought in)
    dropped: int = 0  # commits in new..old (no longer on HEAD, e.g. after detach)


@dataclass
class SyncReport:
    """What the last sync (or detach) changed, for the header and its report."""

    title: str
    finished_at: float
    returncode: Optional[int]
    scanned: int  # projects whose commit was compared
    changes: list[HeadChange] = field(default_factory=list)
    # A fetch's report (repo sync -n): nothing moved yet; each change is
    # HEAD -> the upstream commit a sync would check out.
    incoming: bool = False


@dataclass
class TreeInfo:
    """Overall info about the repo tree, independent of any single project."""

    root: Path
    manifest_name: str = ""
    manifest_branch: str = ""
    manifest_groups: str = ""
    total_projects: int = 0
