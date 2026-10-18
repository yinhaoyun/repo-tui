"""Loading of repo-tui's own config file (ignore patterns, preferences).

Config is looked up, in order, at:
  - $REPO_TUI_CONFIG if set
  - <repo-root>/.repo-tui.toml   (project-local, checked in if the team wants)
  - ~/.config/repo-tui/config.toml

Later files do not merge with earlier ones; the first one found wins, so a
project-local file fully overrides the user's global defaults. This keeps
behavior predictable for a tool whose whole point is "what counts as noise".

The settings listed in SETTINGS can also be changed in the in-app settings
screen (`o`). Those choices are saved separately, to
~/.config/repo-tui/settings.toml ($REPO_TUI_SETTINGS overrides the path),
and take precedence over the config file, so changing one in the UI never
rewrites a hand-edited (or team-shared) config.
"""

from __future__ import annotations

import json
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


SORT_CHOICES = (
    ("changed", "changed first"),
    ("changes", "most changes first"),
    ("branch", "by branch"),
    ("path", "by path"),
)
RESULT_SCREEN_CHOICES = (
    ("always", "always"),
    ("problems", "only on errors/warnings"),
)
UPDATE_CHECK_CHOICES = tuple(
    (hours, "every hour" if hours == 1 else f"every {hours} h") for hours in (1, 3, 6, 12, 24)
)
AUTO_REFRESH_CHOICES = tuple(
    (minutes, f"every {minutes} min" if minutes else "off")
    for minutes in (0, 1, 2, 5, 10, 15, 30, 60)
)


@dataclass
class Config:
    show_all_default: bool = False
    highlight_hints: bool = True
    theme: str = "textual-dark"
    max_workers: int = 16
    sync_jobs: int = field(default_factory=default_sync_jobs)
    confirm_sync_all: bool = False
    show_sync_report: bool = True
    sort: str = "changed"
    result_screen: str = "always"
    auto_refresh_minutes: int = 0
    check_updates: bool = True
    update_check_hours: int = 6
    ignore: IgnoreRules = field(default_factory=IgnoreRules)
    custom_themes: dict = field(default_factory=dict)  # raw [themes.<name>] tables
    source: Path | None = None


@dataclass(frozen=True)
class Setting:
    """One user-changeable setting: a Config attribute (also its key in
    settings.toml), where it lives in the config file, and how the settings
    screen shows it."""

    name: str
    tab: str
    label: str
    help: str
    kind: str  # "bool", "int", "choice" (one of `choices`) or "theme"
    section: str  # config file [section] it can also be set in
    file_key: str
    choices: tuple = ()  # ((value, label), ...) for kind "choice"
    minimum: int = 1
    maximum: int = 64

    def coerce(self, value: Any) -> Any:
        """`value` as this setting's type, or None if it isn't valid."""
        if self.kind == "bool":
            return value if isinstance(value, bool) else None
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            return min(max(value, self.minimum), self.maximum)
        if self.kind == "choice":
            return value if value in [v for v, _ in self.choices] else None
        return value if isinstance(value, str) and value else None  # theme


SETTINGS = (
    Setting(
        "highlight_hints", "Display", "Highlight key hints & tips",
        "Show keys as high-contrast keycaps and usage tips in bold colour, "
        "everywhere: the bottom bar, help, action panel and dialogs.",
        "bool", "display", "highlight_hints",
    ),
    Setting(
        "theme", "Display", "Theme",
        "Colour theme for the whole app, applied as soon as you change it. "
        "←/→ steps through them; enter opens a filterable list. Add your own "
        "under [themes.<name>] in the config file.",
        "theme", "display", "theme",
    ),
    Setting(
        "sync_jobs", "Sync", "Sync jobs (-jN)",
        "Parallel jobs for repo sync, sync all and detach. Lower it if your "
        "git server throttles you. Default: one per CPU.",
        "int", "sync", "jobs", maximum=128,
    ),
    Setting(
        "confirm_sync_all", "Sync", "Confirm before sync all",
        "Ask y/N before space S syncs the whole tree.",
        "bool", "sync", "confirm_all",
    ),
    Setting(
        "show_sync_report", "Sync", "Open sync report after sync",
        "After space s / space S, open the list of projects that moved to a "
        "new commit (when any did and the sync had no errors/warnings). "
        "Detach and manifest switches never open it; u always does.",
        "bool", "sync", "show_report",
    ),
    Setting(
        "max_workers", "Behaviour", "Status scan workers",
        "How many projects get git status in parallel during a refresh. "
        "Raise on fast SSDs with many cores; lower on network storage.",
        "int", "performance", "max_workers", maximum=128,
    ),
    Setting(
        "sort", "Behaviour", "Sort project list",
        "Order of the project list. Changed first keeps changed projects at "
        "the top, then sorts by path.",
        "choice", "display", "sort", choices=SORT_CHOICES,
    ),
    Setting(
        "result_screen", "Behaviour", "Show command output",
        "After a repo command: always open its output screen, or only when "
        "it failed or printed errors/warnings (a short toast otherwise).",
        "choice", "behaviour", "result_screen", choices=RESULT_SCREEN_CHOICES,
    ),
    Setting(
        "auto_refresh_minutes", "Behaviour", "Auto-refresh status",
        "Rescan git status in the background every N minutes, without the "
        "blocking dialog. Skipped while a dialog or command is open.",
        "choice", "behaviour", "auto_refresh_minutes", choices=AUTO_REFRESH_CHOICES,
    ),
    Setting(
        "check_updates", "Behaviour", "Check for repo-tui updates",
        "Check GitHub in the background for a newer repo-tui, at startup and "
        "then every so often (below), and say so in the header. U updates it "
        "either way.",
        "bool", "behaviour", "check_updates",
    ),
    Setting(
        "update_check_hours", "Behaviour", "Update check interval",
        "How often to re-check GitHub for a newer repo-tui while it runs. A "
        "popup only appears when there's something newer than last time.",
        "choice", "behaviour", "update_check_hours", choices=UPDATE_CHECK_CHOICES,
    ),
)
SETTINGS_BY_NAME = {setting.name: setting for setting in SETTINGS}


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
    saved = load_settings()
    for setting in SETTINGS:
        value = setting.coerce(saved.get(setting.name))
        if value is not None:
            setattr(config, setting.name, value)
    return config


def settings_path() -> Path:
    env_path = os.environ.get("REPO_TUI_SETTINGS")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".config" / "repo-tui" / "settings.toml"


_HISTORY_LIMIT = 50


def history_path(name: str) -> Path:
    """Where a prompt's history lives, next to settings.toml."""
    return settings_path().parent / f"{name}_history"


def load_history(name: str) -> list[str]:
    """A prompt's earlier entries, oldest first ([] if there are none)."""
    try:
        return [line for line in history_path(name).read_text().splitlines() if line]
    except OSError:
        return []


def add_history(name: str, entry: str) -> None:
    """Remember `entry` as the newest, once, keeping the last _HISTORY_LIMIT.
    History is a convenience: failing to save it is silently ignored."""
    entries = [e for e in load_history(name) if e != entry] + [entry]
    path = history_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(entries[-_HISTORY_LIMIT:]) + "\n")
    except OSError:
        pass


def load_settings() -> dict[str, Any]:
    """Settings saved from the in-app menu; {} if none (or unreadable)."""
    try:
        with settings_path().open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _toml_value(value: bool | int | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def save_setting(name: str, value: bool | int | str) -> Path:
    """Persist one menu setting, keeping the others already in the file."""
    settings = {
        k: v for k, v in load_settings().items() if isinstance(v, (bool, int, str))
    }
    settings[name] = value
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k} = {_toml_value(v)}\n" for k, v in sorted(settings.items()))
    path.write_text("# Written by repo-tui's settings screen (o).\n" + body)
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

    config = Config(
        show_all_default=bool(display.get("show_all_default", False)),
        ignore=IgnoreRules(patterns=list(patterns), per_project=dict(per_project)),
        custom_themes=data.get("themes", {}),
        source=path,
    )
    for setting in SETTINGS:
        value = setting.coerce(data.get(setting.section, {}).get(setting.file_key))
        if value is not None:
            setattr(config, setting.name, value)
    return config
