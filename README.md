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
git clone https://github.com/yinhaoyun/repo-tui.git
cd repo-tui
python3 -m pip install --user -e .
```

The repo is private, so `git clone` will prompt for GitHub credentials; the
simplest fix is `gh auth login` once (via GitHub CLI) or an SSH clone URL
(`git@github.com:yinhaoyun/repo-tui.git`) with your key already added to
GitHub.

If pip refuses with `error: externally-managed-environment` (Debian/Ubuntu
23.04+, including 24.04), either use a venv:

```sh
sudo apt install -y python3-venv   # if not already present
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

or install straight to your user site-packages, bypassing the guard (safe
here — it only touches your own `~/.local`, not system files):

```sh
python3 -m pip install --user --break-system-packages -e .
```

Either way, this installs a `repo-tui` command. With the venv approach it
only exists while the venv is active (`. .venv/bin/activate` each session,
or symlink `.venv/bin/repo-tui` somewhere on your `PATH`); with `--user` it
lands in `~/.local/bin`, so make sure that's on your `PATH`
(`export PATH="$HOME/.local/bin:$PATH"` in `~/.bashrc` if not).

## Use

```sh
repo-tui            # from anywhere inside a repo-init'ed tree
repo-tui /path/to/tree
```

## Upgrading

```sh
repo-tui update
```

This only works because the install is editable (`pip install -e .` from a
git clone): `repo-tui update` finds that clone (by walking up from the
installed package's own location, the same trick `find_repo_root` uses for
`.repo`), refuses to touch it if it has uncommitted local changes, runs
`git pull --ff-only`, and reinstalls to pick up any new dependencies. It's
equivalent to, and no more magic than, doing this by hand:

```sh
cd path/to/your/repo-tui/clone
git pull --ff-only
python3 -m pip install --user -e .   # only strictly needed if deps changed
```

There's no published PyPI package, so there's nothing to `pip install
--upgrade` from — the git checkout is the only source of truth. If
`repo-tui update` can't find a git checkout (e.g. you copied the installed
package elsewhere without its `.git`), it'll tell you to re-clone instead.

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
- **Bottom bar**: the always-on keys. Press `Space` to open the action
  panel — a box listing every action key, grouped into "this project" and
  "whole tree", with the target project in its title. It stays open until
  you press an action key (runs it, then closes), or `Esc`/`Space` to exit.
  Right-clicking a project row opens the per-project actions as a menu.

A project counts as "changed" if it has any non-ignored file changes, is
ahead/behind its upstream, or is checked out on any local branch (e.g. one
made with `repo start`) — even a branch named like the manifest revision. A
project sitting in detached HEAD — the normal state for an untouched project
right after `repo sync` — is *not* treated as changed on its own.

## Keys

| Key | Action |
|---|---|
| `↑`/`↓`, `j`/`k`, mouse | navigate / select |
| `tab` | switch focus between panes |
| `enter` / click (on a file row) | open that file's diff |
| right-click (on a project row) | open the project context menu |
| drag the `│` divider | resize the project list vs. the detail pane |
| `?` | help |
| `/` | filter projects by path/name |
| `a` | toggle show-all vs. hide-unchanged |
| `i` | toggle showing ignored files in the file list |
| `r` | refresh (re-run git status across the tree) |
| `q` | quit |
| `b` | `repo start <branch>` — create a new branch on the selected project (prompts for a name) |
| `B` | `repo checkout <branch>` — switch to an existing local branch (pick from a list, or type one) |
| `c` | copy the selected project's absolute path |
| `f` | `repo forall -c <command>` across the tree (prompts for the command) |
| `Space` then `s` | `repo sync` the selected project |
| `Space` then `S` | `repo sync` the whole tree |
| `Space` then `d` | `repo sync -d -l` — detach the selected project to its manifest revision (local only, no fetch) |
| `Space` then `x` | discard the selected project's changes so its Stat is clean: tracked changes revert to HEAD, untracked files are deleted; files hidden by your ignore rules, commits and the branch are kept (asks y/N first) |
| `Space` then `b`/`B`/`c`/`f` | same as the direct keys |
| `Esc` / `Space` (in the panel) | close the action panel without doing anything |

Keys that ask before doing anything (`b`, `B`, `c`, `f`) work directly.
Keys that run straight away and change the checkout (`s`, `S`, `d`, `x`) are only
available from the `Space` action panel, so a stray keypress can't trigger
a sync. The per-project actions (`s`, `d`, `x`, `b`/`B`, `c`) are also available
by right-clicking a project row.

While a `repo` command or a status refresh (at startup, `r`, and after every
`repo` command) runs, a dialog blocks all other keys and clicks. A refresh
shows how many projects have been scanned so far. For `repo sync` it shows the command line, repo's live progress (percentage,
current project, jobs running) and the output tail. Sync runs with
`-jN` parallel jobs: one per CPU by default, or set it in the config file:

```toml
[sync]
jobs = 4
```

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
