"""A one-column vertical divider that can be dragged with the mouse to resize
the pane on its left (the project list) against the pane on its right."""

from __future__ import annotations

from textual import events
from textual.widget import Widget

MIN_PANE_WIDTH = 20  # neither pane can be dragged narrower than this


class Splitter(Widget):
    def __init__(self, target_id: str, **kwargs) -> None:
        """`target_id`: id of the sibling widget whose width the drag sets."""
        super().__init__(**kwargs)
        self._target_id = target_id
        self._dragging = False

    def render(self) -> str:
        return "\n".join("│" * self.size.width for _ in range(self.size.height))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        self._dragging = True
        self.add_class("-dragging")
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging or self.parent is None:
            return
        container = self.parent.region
        width = event.screen_x - container.x
        max_width = container.width - self.size.width - MIN_PANE_WIDTH
        width = max(MIN_PANE_WIDTH, min(width, max_width))
        self.parent.query_one(f"#{self._target_id}").styles.width = width
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self.remove_class("-dragging")
        self.release_mouse()
        event.stop()
