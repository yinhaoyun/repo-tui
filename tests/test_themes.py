from textual.theme import BUILTIN_THEMES

from repo_tui import themes


def test_extra_themes_have_unique_new_names():
    names = [t.name for t in themes.EXTRA_THEMES]
    assert len(names) == len(set(names)) >= 15
    assert not set(names) & set(BUILTIN_THEMES)


def test_extra_themes_build_their_colour_systems():
    for theme in themes.EXTRA_THEMES:
        variables = theme.to_color_system().generate()
        assert variables["primary"] and variables["text-warning"], theme.name


def test_custom_theme_from_config():
    found, errors = themes.custom_themes(
        {"mine": {"primary": "#ff8800", "dark": False, "background": "white",
                  "variables": {"footer-key-foreground": "#ff8800"}}}
    )
    assert errors == []
    assert [(t.name, t.primary, t.dark) for t in found] == [("mine", "#ff8800", False)]


def test_bad_custom_themes_are_skipped_with_a_reason_each():
    found, errors = themes.custom_themes(
        {
            "ok": {"primary": "red"},
            "bad-colour": {"primary": "#ff88zz"},
            "no-primary": {"background": "#000000"},
            "typo": {"primary": "red", "backgroud": "#000000", "dark": "yes"},
            "not-a-table": "red",
        }
    )
    assert [t.name for t in found] == ["ok"]
    text = "\n".join(errors)
    assert "[themes.bad-colour] primary: '#ff88zz' isn't a colour" in text
    assert "[themes.no-primary]: 'primary' is required" in text
    assert "unknown key 'backgroud'" in text
    assert "[themes.typo] dark: expected true or false" in text
    assert "[themes.not-a-table]: must be a table" in text
