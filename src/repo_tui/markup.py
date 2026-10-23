"""Escaping text for Textual content markup."""

from __future__ import annotations


def escape(text: str) -> str:
    """Make `text` safe to drop into Textual markup.

    rich.markup.escape only escapes a `[` that looks like the start of a tag,
    but Textual's parser lets any stray `[` swallow the next real tag: a commit
    subject like "jump between its files (]/[)" followed by "[dim]...[/]"
    raises MarkupError ("auto closing tag ('[/]') has nothing to close") and
    takes the app down. So escape every `[`.
    """
    return text.replace("[", "\\[")
