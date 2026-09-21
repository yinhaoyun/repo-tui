from repo_tui.config import load_config


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
