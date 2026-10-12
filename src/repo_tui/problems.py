"""Spot error and warning lines in a repo/git command's output, so the UI
can call them out instead of leaving them buried in a long log.

Matches the prefixes repo and git actually print ("error: ...", "fatal:
...", "warning: ...", repo's "Failing repos:" summary, a Python traceback
from repo itself) rather than any line containing "error", so ordinary
output such as a commit subject mentioning a failed test isn't flagged."""

from __future__ import annotations

import re
from typing import Optional

ERROR = "error"
WARNING = "warning"

_ERROR_RE = re.compile(
    r"^\s*(?:repo:\s*)?(?:error|fatal)\s*:"
    r"|^Failing repos\b"
    r"|^Traceback \(most recent call last\)",
    re.IGNORECASE,
)
_WARNING_RE = re.compile(r"^\s*(?:repo:\s*)?warn(?:ing)?\s*:", re.IGNORECASE)


def classify(line: str) -> Optional[str]:
    """ERROR, WARNING, or None for an ordinary output line."""
    if _ERROR_RE.search(line):
        return ERROR
    if _WARNING_RE.search(line):
        return WARNING
    return None


def style_for(level: Optional[str]) -> str:
    return {ERROR: "bold red", WARNING: "yellow"}.get(level or "", "")
