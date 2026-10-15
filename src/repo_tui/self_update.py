"""`repo-tui update`: pull the latest source and reinstall in place.

This only works for an editable install (`uv tool install --editable .` or
`pip install -e .`) from a git clone, which is the install method the README documents -- there is no
published PyPI package to fall back to. It deliberately never force-pushes
over local changes: if the checkout is dirty or has diverged, it stops and
tells the user what to do instead of guessing.

The app also uses this module: `check_for_update` (at startup, in the
background) to tell the user a newer version is on GitHub, and `update`
behind the `U` key. Those run git non-interactively, since a credential or
ssh passphrase prompt would land on the terminal the TUI is drawing on.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import repo_tui


class SelfUpdateError(RuntimeError):
    pass


def _find_git_checkout_root() -> Path:
    """Walk up from the installed package looking for this project's own
    git checkout (identified by a pyproject.toml naming this package)."""
    here = Path(repo_tui.__file__).resolve().parent
    for candidate in (here, *here.parents):
        pyproject = candidate / "pyproject.toml"
        if (candidate / ".git").exists() and pyproject.is_file():
            if 'name = "repo-tui"' in pyproject.read_text(errors="ignore"):
                return candidate
    raise SelfUpdateError(
        "repo-tui isn't running from a git checkout it can update in place "
        "(no pyproject.toml for repo-tui found above "
        f"{here}). Re-clone from GitHub and reinstall instead:\n"
        "  git clone https://github.com/yinhaoyun/repo-tui.git\n"
        "  cd repo-tui && uv tool install --editable ."
    )


def _run(cwd: Path, *args: str, batch: bool = False) -> subprocess.CompletedProcess:
    """Run a command, capturing its output. `batch` (inside the TUI) makes
    git fail instead of prompting: no stdin, no controlling terminal, no
    credential prompt, and ssh in BatchMode."""
    if not batch:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", f"{args[0]} {args[1]} timed out after 60s")


def _first_error(result: subprocess.CompletedProcess) -> str:
    """The line of git's stderr that says what went wrong."""
    lines = [line.strip() for line in result.stderr.splitlines() if line.strip()]
    reasons = [line for line in lines if line.startswith(("fatal:", "error:"))]
    return " ".join(reasons or lines[-1:]) or f"exit {result.returncode}"


@dataclass
class UpdateCheck:
    """Whether a newer repo-tui is on GitHub, compared with this checkout."""

    root: Optional[Path] = None
    behind: int = 0  # commits on GitHub this checkout doesn't have
    ahead: int = 0  # local commits GitHub doesn't have
    commits: list[str] = field(default_factory=list)  # the new ones, oneline
    dirty: bool = False
    error: str = ""  # why it couldn't check
    not_a_checkout: bool = False  # not an editable git install: can't self-update

    @property
    def available(self) -> bool:
        return self.behind > 0

    @property
    def blocker(self) -> str:
        """Why updating can't go ahead, or ""."""
        if self.error:
            return self.error
        if self.dirty:
            return (
                f"local changes present in the checkout ({self.root}). "
                "Commit, stash or discard them first."
            )
        if self.ahead and self.behind:
            return (
                f"the checkout ({self.root}) has {self.ahead} local commit(s) that "
                "aren't on GitHub, so it can't fast-forward. Push or rebase them by hand."
            )
        return ""


def check_for_update(fetch: bool = True) -> UpdateCheck:
    """Fetch (non-interactively) and compare HEAD with its upstream."""
    try:
        root = _find_git_checkout_root()
    except SelfUpdateError as exc:
        return UpdateCheck(error=str(exc), not_a_checkout=True)
    check = UpdateCheck(root=root)
    if fetch:
        result = _run(root, "git", "fetch", "--quiet", batch=True)
        if result.returncode != 0:
            check.error = f"git fetch failed: {_first_error(result)}"
            return check
    upstream = _run(root, "git", "rev-parse", "--abbrev-ref", "@{u}", batch=True)
    if upstream.returncode != 0:
        check.error = f"the checkout's branch has no upstream to update from ({root})"
        return check
    counts = _run(
        root, "git", "rev-list", "--left-right", "--count", "HEAD...@{u}", batch=True
    ).stdout.split()
    if len(counts) == 2 and all(c.isdigit() for c in counts):
        check.ahead, check.behind = int(counts[0]), int(counts[1])
    if check.behind:
        log = _run(root, "git", "log", "--oneline", "-n", "20", "HEAD..@{u}", batch=True)
        check.commits = log.stdout.splitlines()
    check.dirty = bool(_run(root, "git", "status", "--porcelain", batch=True).stdout.strip())
    return check


def _is_uv_tool_env() -> bool:
    """`uv tool install` envs carry a uv-receipt.toml and have no pip."""
    return (Path(sys.prefix) / "uv-receipt.toml").is_file()


def _pip_install_editable(root: Path) -> subprocess.CompletedProcess:
    if _is_uv_tool_env():
        uv = shutil.which("uv") or "uv"
        return subprocess.run(
            [uv, "tool", "install", "--quiet", "--editable", "--reinstall", str(root)],
            capture_output=True,
            text=True,
        )
    install = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(root)],
        capture_output=True,
        text=True,
    )
    if install.returncode != 0 and "externally-managed-environment" in install.stderr:
        install = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--user",
                "--break-system-packages",
                "-e",
                str(root),
            ],
            capture_output=True,
            text=True,
        )
    return install


Say = Callable[[str, bool], None]  # (message, is_error)


def update(say: Say, batch: bool = False) -> int:
    """Pull the latest repo-tui and reinstall it, reporting through `say`.
    Returns 0 on success (including "already up to date"), 1 otherwise."""
    try:
        root = _find_git_checkout_root()
    except SelfUpdateError as exc:
        say(str(exc), True)
        return 1

    say(f"checkout at {root}", False)

    dirty = _run(root, "git", "status", "--porcelain", batch=batch)
    if dirty.stdout.strip():
        say(
            "local changes present in the checkout -- "
            "refusing to touch it. Commit, stash, or discard them first:\n"
            f"  git -C {root} status",
            True,
        )
        return 1

    before = _run(root, "git", "rev-parse", "HEAD").stdout.strip()

    fetch = _run(root, "git", "fetch", "--quiet", batch=batch)
    if fetch.returncode != 0:
        say(f"git fetch failed:\n{fetch.stderr}", True)
        return 1

    pull = _run(root, "git", "pull", "--ff-only", batch=batch)
    if pull.returncode != 0:
        say(
            "couldn't fast-forward (local commits not on "
            f"the remote branch?). Resolve manually in {root}:\n{pull.stderr}",
            True,
        )
        return 1

    after = _run(root, "git", "rev-parse", "HEAD").stdout.strip()

    if before == after:
        say("already up to date.", False)
        return 0

    log = _run(root, "git", "log", "--oneline", f"{before}..{after}")
    say("pulled new commits:\n" + log.stdout.rstrip(), False)

    say("reinstalling to pick up any dependency changes...", False)
    install = _pip_install_editable(root)
    if install.returncode != 0:
        say(f"reinstall failed:\n{install.stderr}", True)
        return 1

    say(f"done ({before[:8]} -> {after[:8]}).", False)
    return 0


def run_update() -> int:
    """`repo-tui update` on the command line."""

    def say(message: str, is_error: bool) -> None:
        print(f"repo-tui update: {message}", file=sys.stderr if is_error else sys.stdout)

    return update(say)
