"""`repo-tui update`: pull the latest source and reinstall in place.

This only works for an editable (`pip install -e .`) install from a git
clone, which is the install method the README documents -- there is no
published PyPI package to fall back to. It deliberately never force-pushes
over local changes: if the checkout is dirty or has diverged, it stops and
tells the user what to do instead of guessing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
        "  cd repo-tui && python3 -m pip install --user -e ."
    )


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def _pip_install_editable(root: Path) -> subprocess.CompletedProcess:
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


def run_update() -> int:
    try:
        root = _find_git_checkout_root()
    except SelfUpdateError as exc:
        print(f"repo-tui update: {exc}", file=sys.stderr)
        return 1

    print(f"repo-tui update: checkout at {root}")

    dirty = _run(root, "git", "status", "--porcelain")
    if dirty.stdout.strip():
        print(
            "repo-tui update: local changes present in the checkout -- "
            "refusing to touch it. Commit, stash, or discard them first:\n"
            f"  git -C {root} status",
            file=sys.stderr,
        )
        return 1

    before = _run(root, "git", "rev-parse", "HEAD").stdout.strip()

    fetch = _run(root, "git", "fetch", "--quiet")
    if fetch.returncode != 0:
        print(f"repo-tui update: git fetch failed:\n{fetch.stderr}", file=sys.stderr)
        return 1

    pull = _run(root, "git", "pull", "--ff-only")
    if pull.returncode != 0:
        print(
            "repo-tui update: couldn't fast-forward (local commits not on "
            f"the remote branch?). Resolve manually in {root}:\n{pull.stderr}",
            file=sys.stderr,
        )
        return 1

    after = _run(root, "git", "rev-parse", "HEAD").stdout.strip()

    if before == after:
        print("repo-tui update: already up to date.")
        return 0

    log = _run(root, "git", "log", "--oneline", f"{before}..{after}")
    print("repo-tui update: pulled new commits:")
    print(log.stdout)

    print("repo-tui update: reinstalling to pick up any dependency changes...")
    install = _pip_install_editable(root)
    if install.returncode != 0:
        print(f"repo-tui update: pip install failed:\n{install.stderr}", file=sys.stderr)
        return 1

    print(f"repo-tui update: done ({before[:8]} -> {after[:8]}).")
    return 0
