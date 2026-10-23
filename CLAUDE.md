# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

repo-tui is a Textual terminal UI for AOSP-style `repo` multi-repository trees: it runs `git status` per project in parallel, filters the results through user ignore rules, and shows the whole tree on one screen. The README is the user-facing spec for every key and behaviour, so keep it in sync with any change to keys or behaviour.

## Commands

```sh
uv run --extra dev pytest -q                                   # full suite (~90s; most of it is tests/test_app.py)
uv run --extra dev pytest tests/test_app.py -k diff -q         # a subset
uv run --extra dev pytest tests/test_git_backend.py::test_name # a single test
uv run repo-tui /path/to/repo-tree                             # run against a real `repo init`'ed tree
```

Without uv: `python3 -m pip install --user -e ".[dev]"`, then `python3 -m pytest`. There's no linter or formatter configured.

## Compatibility constraints

- **Python 3.8+** (Ubuntu 20.04). Every module starts with `from __future__ import annotations`, so `list[str]` and `X | None` are fine in annotations but not in runtime expressions such as `cast()`, type aliases, or `isinstance`. Use `compat.to_thread` instead of `asyncio.to_thread`.
- **Textual >= 0.60**. On 3.8 the newest Textual available is older than 7, so newer APIs need a fallback; see `_right_click` in `tests/test_app.py` for an example.
- **Markup escaping**: any user or git text (commit subjects, paths, command output) placed into Textual markup must go through `markup.escape`, not `rich.markup.escape`. A stray `[` crashes the app. Key hints are built with `hints.key()`/`hints.tip()`, which handle this themselves.

## Architecture

`src/repo_tui/`:

- `__main__.py`: CLI. `repo-tui update` goes to `self_update.run_update`. Otherwise it finds the tree root and runs `RepoTuiApp`, then re-execs itself if the in-app update asked for a restart.
- `repo_backend.py`: everything that touches `.repo/`. It finds the root, parses the manifest (including `<include>`, local manifests, `<extend-project>`/`<remove-project>`) into `Project`s, and wraps `repo` subcommands (sync, forall, start, checkout, abandon, init) as async functions that stream output. `sync_args`/`forall_args`/… build the argv separately so the UI can show the exact command. `repo sync` runs on a pty (`_stream_subprocess_tty`) because repo only prints its live progress line to a TTY.
- `git_backend.py`: synchronous per-project git calls (`git status --porcelain=v2`, numstat, log, blame, diff, stage/discard). It parallelises with thread pools (`collect_all`, `head_commits`, …). It mutates the `Project` objects in place.
- `models.py`: dataclasses (`Project`, `FileStatus`, `Commit`, `HeadChange`, `SyncReport`, `TreeInfo`). `Project.is_changed` defines what counts as "changed"; a detached HEAD is not changed on its own.
- `config.py`: two separate stores. The first is the ignore/config TOML, where the first file found wins with no merging: `$REPO_TUI_CONFIG`, then `<root>/.repo-tui.toml`, then `~/.config/repo-tui/config.toml`. The second is in-app settings (`SETTINGS`, edited with `o`), saved to `~/.config/repo-tui/settings.toml` (`$REPO_TUI_SETTINGS`); these override the config file. A new user-facing setting goes in `SETTINGS` and is applied in `RepoTuiApp._apply_setting`.
- `app.py`: `RepoTuiApp` holds all actions and orchestration. Key patterns:
  - Long work runs in `self.run_worker(..., exclusive=True, group=...)`. Blocking git calls go through `to_thread`.
  - `_refresh()` rescans status behind a `BusyModal` (all projects, or `only=`).
  - `_run_and_refresh()` is the single path for every `repo` command. It opens a busy dialog, optionally records HEADs before and after (`track_updates`, which produces the `SyncReport`), rescans, then shows a `ResultScreen` (with error/warning lines pulled out by `problems.py`) or a `SyncReportScreen`. The command runs as its own task so `esc esc` in the busy dialog can cancel it (`cancellable=`); the streaming helpers in `repo_backend` kill the process group on cancel.
  - `_targets()` returns the marked projects if any, else the selected one. Project actions act on targets and unmark them once they start.
- `widgets/`: screens and modals (diff/blame, log, sync report, picker, prompt, confirm, settings, help, action panel, context menu, keybar, header). Styles are in `styles.tcss`.

### Adding or changing a key/action

A key is listed in several places, and they must agree:
- `RepoTuiApp.BINDINGS` (app.py)
- `commands.py` `COMMANDS`, for the ctrl+p palette (with its "needs" condition)
- `widgets/help_modal.py`, the `?` reference
- `widgets/keybar.py`, the bottom bar (`GLOBAL_GROUPS`/`FOCUS_CONTEXTS`, rank sets how early it is dropped on narrow terminals)
- `widgets/action_panel.py` for `Space`-prefixed actions
- the README key tables

Design rules from the README: nothing on the top layer may change a checkout, so anything that does goes behind `Space` in the action panel. Lowercase keys act on the selected (or marked) project; Shift keys act on the whole tree.

## Tests

`tests/conftest.py` (autouse) points settings at `tmp_path` and stubs `self_update.check_for_update`, so tests never touch the real config or the network. `tests/test_app.py` drives the real UI headlessly with `App.run_test()`/pilot against synthetic trees: a fake `.repo/` with a manifest git repo and per-project git repos (see the `dirty_tree` fixture). `repo` itself is not installed in tests, so tests monkeypatch the `repo_backend` command functions.

## Conventions

Commit messages: a short imperative subject, often naming the key in parentheses (e.g. "Search the diff view (/) …"), then a body explaining the behaviour and why.
