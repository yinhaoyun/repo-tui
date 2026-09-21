from repo_tui.ignore import IgnoreRules


def test_default_patterns_match_build_noise():
    rules = IgnoreRules()
    assert rules.matches("any/project", "module.pyc")
    assert rules.matches("any/project", "__pycache__/mod.cpython.pyc")
    assert not rules.matches("any/project", "real_change.c")


def test_per_project_override_is_scoped():
    rules = IgnoreRules(per_project={"platform/frameworks/base": ["res/values/version.xml"]})
    assert rules.matches("platform/frameworks/base", "res/values/version.xml")
    assert not rules.matches("platform/other", "res/values/version.xml")
