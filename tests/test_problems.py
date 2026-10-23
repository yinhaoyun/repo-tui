import pytest

from repo_tui import problems
from repo_tui.widgets.result_screen import summarize


@pytest.mark.parametrize(
    "line, level",
    [
        ("error: Cannot fetch platform/build", problems.ERROR),
        ("fatal: unable to access 'https://example.com/'", problems.ERROR),
        ('repo: error: "git" is not available', problems.ERROR),
        ("Failing repos:", problems.ERROR),
        ("Traceback (most recent call last):", problems.ERROR),
        ("warning: redirecting to https://example.com/", problems.WARNING),
        ("repo: warning: Python 3.8 is no longer supported", problems.WARNING),
        ("Fetching: 100% (27/27), done in 3.1s", None),
        ("abc1234 Fix the error handling in sync", None),
        ("Fix failed test", None),
    ],
)
def test_classify(line, level):
    assert problems.classify(line) == level


def test_summarize():
    assert summarize(0, 0, 0) == ("done", "information")
    assert summarize(0, 0, 2) == ("done with 2 warning(s)", "warning")
    assert summarize(0, 1, 0) == ("done with 1 error(s)", "error")
    assert summarize(1, 0, 0) == ("failed (exit 1)", "error")
    assert summarize(None, 1, 0) == ("failed to run, 1 error(s)", "error")


def test_summarize_cancelled():
    assert summarize(None, 0, 0, cancelled=True) == ("cancelled", "warning")
    assert summarize(None, 0, 1, cancelled=True) == ("cancelled, 1 warning(s)", "warning")
    assert summarize(None, 2, 0, cancelled=True) == ("cancelled, 2 error(s)", "error")
