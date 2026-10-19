"""Markup for key hints and usage tips, kept in one place so every screen
renders them the same way and one setting switches the style.

Highlighted (the default): keys are high-contrast keycaps (black on the
theme's warning colour) and tips are bold in the theme's warning text
colour, so both stand out in dark and light themes. Plain: keys are just
bold and tips are dim, the look repo-tui had before.

Widgets build their markup when they're composed, so a change takes effect
on the next screen opened; the always-visible KeyBar re-renders at once."""

from __future__ import annotations

_highlight = True


def set_highlight(on: bool) -> None:
    global _highlight
    _highlight = on


def highlight_enabled() -> bool:
    return _highlight


def _keycap(name: str) -> str:
    # rich's escape() leaves a lone "[" alone, which then breaks the markup
    return name.replace("[", "\\[")


def key(name: str) -> str:
    """A key or key combo, e.g. key("space") or key("esc")."""
    if _highlight:
        return f"[b #000000 on $warning]{_keycap(name)}[/]"
    return f"[b]{_keycap(name)}[/]"


def tip(markup: str) -> str:
    """A hint about what the user can do next. `markup` may contain key()s."""
    if _highlight:
        return f"[b $text-warning]{markup}[/]"
    return f"[dim]{markup}[/]"


def close_tip(*keys: str) -> str:
    """"(esc/q to close)" with each key highlighted."""
    return tip("(" + "/".join(key(k) for k in keys) + " to close)")
