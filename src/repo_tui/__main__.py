"""CLI entry point for repo-tui."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .repo_backend import RepoTreeError, find_repo_root


def run() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        from .self_update import run_update

        sys.exit(run_update())

    parser = argparse.ArgumentParser(prog="repo-tui", description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="path inside the repo tree to start from (default: current directory)",
    )
    parser.epilog = "Run `repo-tui update` to pull and reinstall the latest version."
    args = parser.parse_args()

    try:
        repo_root = find_repo_root(Path(args.path))
    except RepoTreeError as exc:
        print(f"repo-tui: {exc}", file=sys.stderr)
        sys.exit(1)

    # Imported lazily so `--help` and error paths above don't pay for the
    # (larger) textual import.
    from .app import RepoTuiApp

    RepoTuiApp(repo_root).run()


if __name__ == "__main__":
    run()
