import os

from repo_tui.config import default_sync_jobs, load_config, save_setting, settings_path


def test_load_config_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.source is None
    assert cfg.show_all_default is False
    assert "*.pyc" in cfg.ignore.patterns


def test_load_config_reads_project_local_toml(tmp_path):
    (tmp_path / ".repo-tui.toml").write_text(
        """
[ignore]
patterns = ["*.generated"]

[display]
show_all_default = true

[performance]
max_workers = 4
"""
    )
    cfg = load_config(tmp_path)
    assert cfg.source == tmp_path / ".repo-tui.toml"
    assert cfg.ignore.patterns == ["*.generated"]
    assert cfg.show_all_default is True
    assert cfg.max_workers == 4


def test_sync_jobs_defaults_to_cpu_count_and_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    assert load_config(tmp_path).sync_jobs == default_sync_jobs() == 32
    (tmp_path / ".repo-tui.toml").write_text("[sync]\njobs = 3\n")
    assert load_config(tmp_path).sync_jobs == 3


def test_highlight_hints_defaults_on_and_reads_config(tmp_path):
    assert load_config(tmp_path).highlight_hints is True
    (tmp_path / ".repo-tui.toml").write_text("[display]\nhighlight_hints = false\n")
    assert load_config(tmp_path).highlight_hints is False


def test_menu_setting_is_saved_and_overrides_config_file(tmp_path):
    (tmp_path / ".repo-tui.toml").write_text("[display]\nhighlight_hints = true\n")
    path = save_setting("highlight_hints", False)
    assert path == settings_path()
    assert load_config(tmp_path).highlight_hints is False
    save_setting("highlight_hints", True)
    assert load_config(tmp_path).highlight_hints is True
    assert (tmp_path / ".repo-tui.toml").read_text() == "[display]\nhighlight_hints = true\n"


def test_new_settings_read_from_config_file_and_bad_values_are_ignored(tmp_path):
    (tmp_path / ".repo-tui.toml").write_text(
        """
[display]
theme = "nord"
sort = "branch"

[sync]
confirm_all = true
jobs = 0

[behaviour]
result_screen = "sometimes"
auto_refresh_minutes = 5
"""
    )
    cfg = load_config(tmp_path)
    assert cfg.theme == "nord"
    assert cfg.sort == "branch"
    assert cfg.confirm_sync_all is True
    assert cfg.sync_jobs == 1  # clamped to the minimum
    assert cfg.result_screen == "always"  # invalid choice -> default
    assert cfg.auto_refresh_minutes == 5


def test_saved_settings_of_every_type_round_trip(tmp_path):
    save_setting("theme", "gruvbox")
    save_setting("sync_jobs", 6)
    save_setting("confirm_sync_all", True)
    cfg = load_config(tmp_path)
    assert (cfg.theme, cfg.sync_jobs, cfg.confirm_sync_all) == ("gruvbox", 6, True)


def test_prompt_history_keeps_newest_once_and_a_limit(tmp_path, monkeypatch):
    from repo_tui import config as config_mod

    monkeypatch.setenv("REPO_TUI_SETTINGS", str(tmp_path / "cfg" / "settings.toml"))
    assert config_mod.load_history("forall") == []
    for entry in ("git status", "git log -1", "git status"):
        config_mod.add_history("forall", entry)
    assert config_mod.load_history("forall") == ["git log -1", "git status"]
    for i in range(60):
        config_mod.add_history("forall", f"cmd {i}")
    history = config_mod.load_history("forall")
    assert len(history) == 50 and history[-1] == "cmd 59"
    assert (tmp_path / "cfg" / "forall_history").is_file()


def test_prompt_history_failing_to_save_is_ignored(tmp_path, monkeypatch):
    from repo_tui import config as config_mod

    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("REPO_TUI_SETTINGS", str(blocker / "settings.toml"))
    config_mod.add_history("forall", "git status")  # parent is a file: no crash
    assert config_mod.load_history("forall") == []
