"""Themes repo-tui adds on top of Textual's built-in ones, and the user's
own, defined in the config file:

    [themes.my-theme]
    primary = "#ff8800"          # required
    dark = true                  # false for a light theme
    background = "#101418"       # the rest are optional; Textual derives
    foreground = "#e0e0e0"       # whatever is left out from primary
    # secondary, accent, warning, error, success, surface, panel, boost
    # luminosity_spread = 0.15, text_alpha = 0.95
    # [themes.my-theme.variables]  any Textual design variable, e.g.
    # "footer-key-foreground" = "#ff8800"

Light themes need a fairly bright `warning`: key hints are drawn as black
text on it."""

from __future__ import annotations

from typing import Any

from textual.color import Color, ColorParseError
from textual.theme import Theme


def _theme(name: str, dark: bool = True, **colors: str) -> Theme:
    return Theme(name=name, dark=dark, **colors)


EXTRA_THEMES = [
    _theme(
        "github-dark",
        primary="#58a6ff", secondary="#bc8cff", accent="#f78166",
        warning="#d29922", error="#f85149", success="#3fb950",
        foreground="#c9d1d9", background="#0d1117", surface="#161b22", panel="#21262d",
    ),
    _theme(
        "github-light", dark=False,
        primary="#0969da", secondary="#8250df", accent="#bc4c00",
        warning="#d4a72c", error="#cf222e", success="#1a7f37",
        foreground="#1f2328", background="#ffffff", surface="#f6f8fa", panel="#eaeef2",
    ),
    _theme(
        "everforest-dark",
        primary="#7fbbb3", secondary="#d699b6", accent="#e69875",
        warning="#dbbc7f", error="#e67e80", success="#a7c080",
        foreground="#d3c6aa", background="#2d353b", surface="#343f44", panel="#3d484d",
    ),
    _theme(
        "everforest-light", dark=False,
        primary="#3a94c5", secondary="#df69ba", accent="#f57d26",
        warning="#dfa000", error="#f85552", success="#8da101",
        foreground="#5c6a72", background="#fdf6e3", surface="#f4f0d9", panel="#efebd4",
    ),
    _theme(
        "kanagawa",
        primary="#7e9cd8", secondary="#957fb8", accent="#ffa066",
        warning="#e6c384", error="#e82424", success="#98bb6c",
        foreground="#dcd7ba", background="#1f1f28", surface="#2a2a37", panel="#363646",
    ),
    _theme(
        "ayu-dark",
        primary="#59c2ff", secondary="#d2a6ff", accent="#ff8f40",
        warning="#e6b450", error="#d95757", success="#aad94c",
        foreground="#bfbdb6", background="#0b0e14", surface="#11151c", panel="#1a1f29",
    ),
    _theme(
        "ayu-mirage",
        primary="#73d0ff", secondary="#dfbfff", accent="#ffad66",
        warning="#ffcc66", error="#f28779", success="#d5ff80",
        foreground="#cccac2", background="#1f2430", surface="#242936", panel="#2d3343",
    ),
    _theme(
        "ayu-light", dark=False,
        primary="#399ee6", secondary="#a37acc", accent="#fa8d3e",
        warning="#f2ae49", error="#e65050", success="#86b300",
        foreground="#5c6166", background="#fcfcfc", surface="#f3f4f5", panel="#e7eaed",
    ),
    _theme(
        "night-owl",
        primary="#82aaff", secondary="#c792ea", accent="#f78c6c",
        warning="#ffcb8b", error="#ef5350", success="#addb67",
        foreground="#d6deeb", background="#011627", surface="#0b2942", panel="#1d3b53",
    ),
    _theme(
        "material-palenight",
        primary="#82aaff", secondary="#c792ea", accent="#f78c6c",
        warning="#ffcb6b", error="#ff5370", success="#c3e88d",
        foreground="#a6accd", background="#292d3e", surface="#32374d", panel="#3a3f58",
    ),
    _theme(
        "oceanic-next",
        primary="#6699cc", secondary="#c594c5", accent="#f99157",
        warning="#fac863", error="#ec5f67", success="#99c794",
        foreground="#d8dee9", background="#1b2b34", surface="#343d46", panel="#4f5b66",
    ),
    _theme(
        "cobalt2",
        primary="#ffc600", secondary="#0088ff", accent="#ff9d00",
        warning="#ffc600", error="#ff628c", success="#3ad900",
        foreground="#ffffff", background="#193549", surface="#1f4662", panel="#234e6d",
    ),
    _theme(
        "horizon",
        primary="#e95678", secondary="#b877db", accent="#fab795",
        warning="#fac29a", error="#f43e5c", success="#29d398",
        foreground="#d5d8da", background="#1c1e26", surface="#232530", panel="#2e303e",
    ),
    _theme(
        "synthwave-84",
        primary="#ff7edb", secondary="#36f9f6", accent="#fede5d",
        warning="#fede5d", error="#fe4450", success="#72f1b8",
        foreground="#ffffff", background="#262335", surface="#2a2139", panel="#34294f",
    ),
    _theme(
        "zenburn",
        primary="#8cd0d3", secondary="#dc8cc3", accent="#dfaf8f",
        warning="#f0dfaf", error="#cc9393", success="#7f9f7f",
        foreground="#dcdccc", background="#3f3f3f", surface="#4f4f4f", panel="#5f5f5f",
    ),
    _theme(
        "high-contrast-dark",
        primary="#00b7ff", secondary="#ff00ff", accent="#ffff00",
        warning="#ffd700", error="#ff3333", success="#00ff66",
        foreground="#ffffff", background="#000000", surface="#0a0a0a", panel="#1a1a1a",
    ),
    _theme(
        "high-contrast-light", dark=False,
        primary="#0050c8", secondary="#8000c0", accent="#c04000",
        warning="#ffb300", error="#c00000", success="#007a00",
        foreground="#000000", background="#ffffff", surface="#f2f2f2", panel="#e0e0e0",
    ),
    _theme(
        "matrix",
        primary="#00ff41", secondary="#008f11", accent="#39ff14",
        warning="#ccff00", error="#ff3333", success="#00ff41",
        foreground="#00ff41", background="#000000", surface="#031a03", panel="#0a2a0a",
    ),
    _theme(
        "amber-crt",
        primary="#ffb000", secondary="#ff8c00", accent="#ffd060",
        warning="#ffd060", error="#ff5030", success="#c0d000",
        foreground="#ffb000", background="#1a1000", surface="#241800", panel="#2e2000",
    ),
]

_COLOR_KEYS = (
    "primary", "secondary", "accent", "warning", "error", "success",
    "foreground", "background", "surface", "panel", "boost",
)
_FLOAT_KEYS = ("luminosity_spread", "text_alpha")


def _color(value: Any, where: str, errors: list[str]) -> str | None:
    if not isinstance(value, str):
        errors.append(f"{where}: expected a colour string, got {value!r}")
        return None
    try:
        Color.parse(value)
    except ColorParseError:
        errors.append(f"{where}: {value!r} isn't a colour (try \"#ff8800\" or \"rgb(255,136,0)\")")
        return None
    return value


def custom_themes(table: Any) -> tuple[list[Theme], list[str]]:
    """Themes from the config file's [themes.<name>] tables, plus a message
    for each problem found. A theme with a problem is skipped, not guessed."""
    if not table:
        return [], []
    if not isinstance(table, dict):
        return [], ["[themes] must be a table of [themes.<name>] tables"]
    themes, errors = [], []
    for name, spec in table.items():
        where = f"[themes.{name}]"
        if not isinstance(spec, dict):
            errors.append(f"{where}: must be a table")
            continue
        problems: list[str] = []
        kwargs: dict[str, Any] = {}
        for k, value in spec.items():
            if k in _COLOR_KEYS:
                kwargs[k] = _color(value, f"{where} {k}", problems)
            elif k == "dark":
                if isinstance(value, bool):
                    kwargs["dark"] = value
                else:
                    problems.append(f"{where} dark: expected true or false")
            elif k in _FLOAT_KEYS:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    kwargs[k] = float(value)
                else:
                    problems.append(f"{where} {k}: expected a number")
            elif k == "variables" and isinstance(value, dict):
                kwargs["variables"] = {str(v): str(c) for v, c in value.items()}
            else:
                problems.append(f"{where}: unknown key {k!r}")
        if "primary" not in spec:
            problems.append(f"{where}: 'primary' is required")
        if problems:
            errors += problems
            continue
        themes.append(Theme(name=str(name), **kwargs))
    return themes, errors
