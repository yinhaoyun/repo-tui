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

The recommended way is [uv](https://docs.astral.sh/uv/)'s `uv tool install`.
It gives repo-tui its own private environment and puts a `repo-tui` command
in `~/.local/bin`, so it runs like a native app: no venv to activate, and
nothing touches the system Python. The same steps work on Ubuntu 20.04,
22.04 and 24.04.

1. Install uv (once per machine). Use Astral's installer rather than
   `sudo snap install astral-uv`: it needs no sudo and installs to
   `~/.local/bin`:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Then open a new terminal (or `source ~/.bashrc`) so `~/.local/bin` is on
   your `PATH`.

2. Clone and install:

   ```sh
   git clone https://github.com/yinhaoyun/repo-tui.git
   cd repo-tui
   uv tool install --editable .
   ```

   If `repo-tui` isn't found afterwards, run `uv tool update-shell` and open
   a new terminal.

`--editable` makes the installed command run straight from your clone, which
is what lets `repo-tui update` (below) upgrade it in place. To remove it:
`uv tool uninstall repo-tui`.

The repo is private, so `git clone` will prompt for GitHub credentials; the
simplest fix is `gh auth login` once (via GitHub CLI) or an SSH clone URL
(`git@github.com:yinhaoyun/repo-tui.git`) with your key already added to
GitHub.

### Without uv

pipx does the same job (from apt on Ubuntu 22.04 and 24.04; 20.04's apt pipx is
too old, so use uv or pip there):

```sh
sudo apt install -y pipx
pipx ensurepath                    # then open a new terminal
pipx install -e .                  # from inside the clone
```

Or use plain pip. On Ubuntu 20.04 and 22.04:

```sh
python3 -m pip install --user -e .
```

On Ubuntu 24.04 (and other Debian/Ubuntu 23.04+), pip refuses with
`error: externally-managed-environment`; add `--break-system-packages`
(safe here — it only touches your own `~/.local`, not system files):

```sh
python3 -m pip install --user --break-system-packages -e .
```

With `--user`, the command lands in `~/.local/bin`, so make sure that's on
your `PATH` (`export PATH="$HOME/.local/bin:$PATH"` in `~/.bashrc` if not).

## Use

```sh
repo-tui            # from anywhere inside a repo-init'ed tree
repo-tui /path/to/tree
```

## Upgrading

```sh
repo-tui update
```

This only works because the install is editable (`--editable` / `-e` from a
git clone): `repo-tui update` finds that clone (by walking up from the
installed package's own location, the same trick `find_repo_root` uses for
`.repo`), refuses to touch it if it has uncommitted local changes, runs
`git pull --ff-only`, and reinstalls to pick up any new dependencies (with
uv when it was installed via `uv tool install`, pip otherwise). It's
equivalent to, and no more magic than, doing this by hand:

```sh
cd path/to/your/repo-tui/clone
git pull --ff-only
uv tool install --editable --reinstall .   # only strictly needed if deps changed
```

There's no published PyPI package, so there's nothing to `pip install
--upgrade` from — the git checkout is the only source of truth.

### If `repo-tui update` stops with an error

- **"local changes present in the checkout"**: it never pulls over your own
  edits. See what they are with `git -C <clone> status`, then commit, stash
  or discard them and run `repo-tui update` again.
- **git asks for credentials, or the fetch fails**: the repo is private, so
  the pull needs the same GitHub access the clone used (`gh auth login`
  once, or an SSH key for an SSH clone URL).
- **"couldn't fast-forward"**: the clone has local commits that aren't on
  GitHub. Push or rebase them by hand in the clone.
- **"pip install failed" with `No module named pip`**: that machine was
  installed with uv before `repo-tui update` learned to reinstall through
  uv. The pull already worked and the editable install is running the new
  code; finish the reinstall once by hand, and later updates handle it
  themselves:

  ```sh
  cd path/to/your/repo-tui/clone
  uv tool install --editable --reinstall .
  ```

- **"isn't running from a git checkout"**: it was installed without a clone
  (e.g. `uv tool install git+https://...`) or the package was copied
  somewhere without its `.git`. For a `git+` uv install, upgrade with
  `uv tool upgrade repo-tui`; otherwise re-clone and install as above.

Not sure how a machine's copy was installed? `uv tool list` shows whether
uv manages it, and `which repo-tui` shows where the command lives.

## What it shows

- **Header**: manifest name/branch, project counts (total vs. changed),
  what the last sync changed, and when the tree was last refreshed. Two
  parts are clickable:
  - **"N changed"** opens a summary of why each project counts as changed
    (files changed, unpushed commits, behind upstream, on a branch, error);
    `Enter` or a click on a row jumps to that project. Also `w`.
  - **"last sync: N updated"** (shown once a sync or detach has run) opens
    the sync report: every project whose checked-out commit moved, with
    `+new`/`−dropped` commit counts. Pick one to see old → new commit, a
    diff summary, and the commits that came in (or, after a detach, the
    ones no longer on HEAD). Also `u`, here or on the sync's output
    screen.

  repo-tui records each affected project's HEAD before every sync, sync
  all and detach, and compares it afterwards, so the report reflects
  exactly that run.
- **Left pane**: one row per project. Unchanged projects are hidden by
  default (press `a` to show everything). Each row shows the branch (plus
  ahead/behind counts) and a rough `+added/-deleted` / `?untracked` stat,
  already filtered through your ignore rules.
- **Right pane**: full detail for the selected project — branch info, whether
  it's off the manifest-pinned revision, and the file list. Select a file and
  press Enter (or click) to see its full diff.
- **Bottom bar**: the always-on keys, none of which can change a checkout.
  Press `Space` to open the action panel — a box listing every key that
  does (sync, detach, branch, checkout, discard, forall), grouped into "this project" and
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
| `r` | refresh only the selected project — quick, for when you know just that one changed (also the right-click menu) |
| `R` | refresh all (re-run git status across the tree) |
| `w` | why each project counts as changed (also: click "N changed" in the header) |
| `u` | last sync report — which projects moved to which commit, and what came in (also: click "last sync" in the header) |
| `y` | copy (yank) the selected project's absolute path |
| `o` | settings — theme, highlighted hints, sync jobs, sort, auto-refresh, … (see below) |
| `q` | quit |
| `Space` then `s` | `repo sync` the selected project |
| `Space` then `S` | `repo sync` the whole tree |
| `Space` then `d` | `repo sync -d -l` — detach the selected project to its manifest revision (local only, no fetch) |
| `Space` then `b` | `repo start <branch>` — create a new branch on the selected project (prompts for a name) |
| `Space` then `c` | `repo checkout <branch>` — switch to an existing local branch (pick from a list, or type one) |
| `Space` then `x` | discard the selected project's changes so its Stat is clean: tracked changes revert to HEAD, untracked files are deleted; files hidden by your ignore rules, commits and the branch are kept (asks y/N first) |
| `Space` then `f` | `repo forall -c <command>` across the tree (prompts for the command) |
| `Esc` / `Space` (in the panel) | close the action panel without doing anything |

The keys follow two rules:

- **Look on the top layer, change behind `Space`.** Nothing on the top
  layer can change a checkout; everything that can (even the ones that ask
  first) takes `Space` and a key, so a stray keypress never syncs, switches
  a branch or runs a command.
- **Lowercase acts on the selected project, Shift on the whole tree**:
  `r`/`R` refresh, `Space s`/`Space S` sync.

`Space` rather than a tmux-style `ctrl+b` prefix: it's one key, and it
doesn't collide with tmux, which you're likely running on a build machine.
The per-project actions (refresh, sync, detach, branch, checkout, discard,
copy) are also available by right-clicking a project row.

While a `repo` command or a status refresh (at startup, `r`/`R`, and after every
`repo` command) runs, a dialog blocks all other keys and clicks. A refresh
shows how many projects have been scanned so far. For `repo sync` it shows the command line, repo's live progress (percentage,
current project, jobs running) and the output tail. Sync runs with
`-jN` parallel jobs: one per CPU by default, or set it in the config file:

```toml
[sync]
jobs = 4
```

## Settings (`o`)

Press `o` for the settings screen. Settings are grouped into tabs: switch
with `tab`/`shift+tab` (or click a tab), pick a row with `↑`/`↓`, change it
with `←`/`→`, `enter` or a click (`enter` on a number lets you type it), and
close with `esc`. Changes apply immediately and are saved to
`~/.config/repo-tui/settings.toml` (`$REPO_TUI_SETTINGS` overrides the path).

| Tab | Setting | Values | Config file key |
|---|---|---|---|
| Display | Highlight key hints & tips — keys as high-contrast keycaps, tips in bold colour, everywhere | on (default) / off | `[display] highlight_hints` |
| Display | Theme | any Textual theme (`textual-dark` default, `nord`, `gruvbox`, `dracula`, …) | `[display] theme` |
| Sync | Sync jobs (`-jN`) for sync, sync all and detach | 1–128 (default: one per CPU) | `[sync] jobs` |
| Sync | Confirm before sync all — ask y/N before `Space S` | on / off (default) | `[sync] confirm_all` |
| Behaviour | Status scan workers — parallel `git status` during a refresh | 1–128 (default 16) | `[performance] max_workers` |
| Behaviour | Sort project list | changed first (default), most changes first, by branch, by path | `[display] sort` (`changed`/`changes`/`branch`/`path`) |
| Behaviour | Show command output — after a repo command, always open its output, or only when it failed or printed errors/warnings (a toast otherwise) | always (default) / only on errors/warnings | `[behaviour] result_screen` (`always`/`problems`) |
| Behaviour | Auto-refresh status — rescan in the background, no blocking dialog; skipped while a dialog or command is open | off (default), every 1/2/5/10/15/30/60 min | `[behaviour] auto_refresh_minutes` |

A value saved from the settings screen wins over the config file, so the
config file (below) sets your defaults and the screen overrides them per
user without rewriting it. Invalid values in either file are ignored.

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
