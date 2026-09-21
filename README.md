# repo-tui

A terminal UI for working with [`repo`](https://gerrit.googlesource.com/git-repo)
(AOSP-style) multi-repository trees, built to fix one specific pain point:

> After a build, some projects grow new/changed files you don't care about,
> and `repo status` buries the changes you actually made under all of it.
> Getting a full picture normally means running several different `repo`/`git`
> commands by hand.

repo-tui runs `git status` itself, per project, in parallel, filters the
result through your own ignore rules, and shows the whole tree's state in one
screen: an overview at the top, a project list on the left (unchanged
projects hidden by default), and full detail — including a diff view — for
whichever project you've selected, on the right.

## Requirements

- Linux (developed against and tested on Ubuntu 24.04; targets Ubuntu 20.04+)
- Python 3.8+
- `git` and `repo` on `PATH`
- A tree that has already been through `repo init` (repo-tui reads `.repo/`
  metadata; it doesn't run `repo init` for you)

## Install

```sh
cd repo-tui
python3 -m pip install --user -e .
# (On Debian/Ubuntu 23.04+, if pip refuses with "externally-managed-environment",
#  either use a venv, or: python3 -m pip install --user --break-system-packages -e .)
```

This installs a `repo-tui` command (via `~/.local/bin`, make sure that's on
your `PATH`).

## Use

```sh
repo-tui            # from anywhere inside a repo-init'ed tree
repo-tui /path/to/tree
```

## What it shows

- **Header**: manifest name/branch, project counts (total vs. changed), and
  when the tree was last refreshed.
- **Left pane**: one row per project. Unchanged projects are hidden by
  default (press `a` to show everything). Each row shows the branch (plus
  ahead/behind counts) and a rough `+added/-deleted` / `?untracked` stat,
  already filtered through your ignore rules.
- **Right pane**: full detail for the selected project — branch info, whether
  it's off the manifest-pinned revision, and the file list. Select a file and
  press Enter (or click) to see its full diff.
- **Bottom bar**: available keys, tmux-style — normal keys are always shown;
  press `Ctrl+B` to reveal a second layer of action keys (sync, forall,
  branch, copy path), same as tmux's leader-key convention.

A project counts as "changed" if it has any non-ignored file changes, is
ahead/behind its upstream, or is checked out on a branch other than the one
the manifest pins it to. A project sitting in detached HEAD at the manifest
revision — the normal state for an untouched project right after `repo
sync` — is *not* treated as changed on its own.

## Keys

| Key | Action |
|---|---|
| `↑`/`↓`, `j`/`k`, mouse | navigate / select |
| `tab` | switch focus between panes |
| `enter` / click (on a file row) | open that file's diff |
| `?` | help |
| `/` | filter projects by path/name |
| `a` | toggle show-all vs. hide-unchanged |
| `i` | toggle showing ignored files in the file list |
| `r` | refresh (re-run git status across the tree) |
| `q` | quit |
| `Ctrl+B` then `s` | `repo sync` the selected project |
| `Ctrl+B` then `S` | `repo sync` the whole tree |
| `Ctrl+B` then `f` | `repo forall -c <command>` across the tree |
| `Ctrl+B` then `b` | `repo start <branch>` on the selected project |
| `Ctrl+B` then `c` | copy the selected project's absolute path |

## Configuring what counts as noise

Ignore rules live in a TOML file (see `repo-tui.example.toml`), loaded from
the first of:

1. `$REPO_TUI_CONFIG`
2. `<repo-root>/.repo-tui.toml` (project-local; check it in if your team
   wants to share it)
3. `~/.config/repo-tui/config.toml`

```toml
[ignore]
patterns = ["*.pyc", "*_intermediates/*", "res/values/version.xml"]

[ignore.per_project]
"platform/frameworks/base" = ["some/generated/file.xml"]
```

Patterns are shell globs matched against each file's path relative to its own
project root, and they only affect what repo-tui *shows and counts* — they
never touch `.gitignore` or any actual git state.

## Scope / what's not here yet

This is a solid first cut, not full parity with every `repo` subcommand.
Implemented: status overview, sync (single project or whole tree), forall,
start branch, diff view. Not yet implemented: `repo upload`, `repo abandon`,
`repo prune`, topic-branch management across multiple projects, and manifest
group filtering. The codebase is structured (`repo_backend.py` /
`git_backend.py`) to make adding those straightforward.

## Development

```sh
python3 -m pip install --user -e ".[dev]"
python3 -m pytest
```

`tests/test_app.py` drives the actual Textual UI headlessly via
`App.run_test()` against synthetic git trees built in `tmp_path`.
