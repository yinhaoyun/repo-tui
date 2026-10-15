import pytest

from repo_tui import hints, self_update


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep tests off the real ~/.config/repo-tui/settings.toml, and reset
    the module-level hint style each test may have toggled."""
    monkeypatch.setenv("REPO_TUI_SETTINGS", str(tmp_path / "settings.toml"))
    # The app checks GitHub for a newer repo-tui at startup; keep tests offline.
    monkeypatch.setattr(self_update, "check_for_update", lambda **kw: self_update.UpdateCheck())
    yield
    hints.set_highlight(True)
