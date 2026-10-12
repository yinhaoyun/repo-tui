import pytest

from repo_tui import hints


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep tests off the real ~/.config/repo-tui/settings.toml, and reset
    the module-level hint style each test may have toggled."""
    monkeypatch.setenv("REPO_TUI_SETTINGS", str(tmp_path / "settings.toml"))
    yield
    hints.set_highlight(True)
