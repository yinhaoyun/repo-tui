"""Loading of repo-tui's own config file (ignore patterns, preferences).

Config is looked up, in order, at:
  - $REPO_TUI_CONFIG if set
  - <repo-root>/.repo-tui.toml   (project-local, checked in if the team wants)
  - ~/.config/repo-tui/config.toml

Later files do not merge with earlier ones; the first one found wins, so a
project-local file fully overrides the user's global defaults. This keeps
behavior predictable for a tool whose whole point is "what counts as noise".

Choices made in the in-app settings menu (`o`) are saved separately, to
~/.config/repo-tui/settings.toml ($REPO_TUI_SETTINGS overrides the path),
and take precedence over the config file, so toggling one in the UI never
rewrites a hand-edited (or team-shared) config.
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


def default_sync_jobs() -> int:
    """Parallel jobs for `repo sync -j`: one per CPU."""
    return os.cpu_count() or 4


@dataclass
class Config:
    show_all_default: bool = False
    highlight_hints: bool = True
    max_workers: int = 16
    sync_jobs: int = field(default_factory=default_sync_jobs)
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
    config = Config()
    for path in _candidate_paths(repo_root):
        if path.is_file():
            config = _parse(path)
            break
    settings = load_settings()
    if isinstance(settings.get("highlight_hints"), bool):
        config.highlight_hints = settings["highlight_hints"]
    return config


def settings_path() -> Path:
    env_path = os.environ.get("REPO_TUI_SETTINGS")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".config" / "repo-tui" / "settings.toml"


def load_settings() -> dict[str, Any]:
    """Settings saved from the in-app menu; {} if none (or unreadable)."""
    try:
        with settings_path().open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def save_setting(name: str, value: bool) -> Path:
    """Persist one boolean menu setting, keeping any others in the file."""
    settings = {k: v for k, v in load_settings().items() if isinstance(v, bool)}
    settings[name] = value
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k} = {'true' if v else 'false'}\n" for k, v in sorted(settings.items()))
    path.write_text("# Written by repo-tui's settings menu (o).\n" + body)
    return path


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
        highlight_hints=bool(display.get("highlight_hints", True)),
        max_workers=int(data.get("performance", {}).get("max_workers", 16)),
        sync_jobs=int(data.get("sync", {}).get("jobs", default_sync_jobs())),
        ignore=IgnoreRules(patterns=list(patterns), per_project=dict(per_project)),
        source=path,
    )
