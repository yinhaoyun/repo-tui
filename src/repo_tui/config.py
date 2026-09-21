"""Loading of repo-tui's own config file (ignore patterns, preferences).

Config is looked up, in order, at:
  - $REPO_TUI_CONFIG if set
  - <repo-root>/.repo-tui.toml   (project-local, checked in if the team wants)
  - ~/.config/repo-tui/config.toml

Later files do not merge with earlier ones; the first one found wins, so a
project-local file fully overrides the user's global defaults. This keeps
behavior predictable for a tool whose whole point is "what counts as noise".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - exercised on Python <3.11
    import tomli as tomllib  # type: ignore[no-redef]

from .ignore import DEFAULT_IGNORE_PATTERNS, IgnoreRules


@dataclass
class Config:
    show_all_default: bool = False
    max_workers: int = 16
    ignore: IgnoreRules = field(default_factory=IgnoreRules)
    source: Path | None = None


def _candidate_paths(repo_root: Path) -> list[Path]:
    candidates = []
    env_path = os.environ.get("REPO_TUI_CONFIG")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(repo_root / ".repo-tui.toml")
    candidates.append(Path.home() / ".config" / "repo-tui" / "config.toml")
    return candidates


def load_config(repo_root: Path) -> Config:
    for path in _candidate_paths(repo_root):
        if path.is_file():
            return _parse(path)
    return Config()


def _parse(path: Path) -> Config:
    with path.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)

    display = data.get("display", {})
    ignore_section = data.get("ignore", {})

    patterns = ignore_section.get("patterns")
    if patterns is None:
        patterns = list(DEFAULT_IGNORE_PATTERNS)
    per_project = ignore_section.get("per_project", {})

    return Config(
        show_all_default=bool(display.get("show_all_default", False)),
        max_workers=int(data.get("performance", {}).get("max_workers", 16)),
        ignore=IgnoreRules(patterns=list(patterns), per_project=dict(per_project)),
        source=path,
    )
