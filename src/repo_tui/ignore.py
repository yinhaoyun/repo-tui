"""Ignore-pattern matching for files that are "noise" after a build.

This is the fix for the core pain point: after a build, generated/changed
files clutter `repo status`. Patterns here are matched against a file's path
*relative to its project root* using shell-glob semantics (fnmatch), and are
applied on top of (not instead of) git's own status/gitignore handling, so
users can hide tracked-but-noisy files (e.g. regenerated version stamps)
that .gitignore cannot suppress.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field


# A conservative set of patterns for common AOSP build byproducts that
# sometimes show up as tracked-file modifications or as untracked files
# inside a project's own worktree (not the top-level out/ directory, which
# is not part of any git project to begin with).
DEFAULT_IGNORE_PATTERNS: list[str] = [
    "*.pyc",
    "__pycache__/*",
    "*.o",
    "*.a",
    "*.so",
    "*.class",
    "*.swp",
    "*~",
    ".DS_Store",
]


@dataclass
class IgnoreRules:
    patterns: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    # per-project-name overrides, e.g. {"platform/frameworks/base": ["res/values/version.xml"]}
    per_project: dict[str, list[str]] = field(default_factory=dict)

    def matches(self, project_name: str, rel_path: str) -> bool:
        patterns = list(self.patterns)
        patterns.extend(self.per_project.get(project_name, ()))
        return any(fnmatch.fnmatch(rel_path, pat) for pat in patterns)
