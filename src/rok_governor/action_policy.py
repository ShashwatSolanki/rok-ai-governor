"""Local allow/deny rules for event-related game actions."""
from __future__ import annotations

import re
from typing import Any

_EVENT_TERMS = re.compile(r"\b(event|expedition|battle|battlefield|arena)\b", re.IGNORECASE)
_EVENT_SCREEN = re.compile(
    r"\b(expedition|event\s+(?:center|screen|menu|interface|page|tab)|"
    r"(?:center|screen|menu|interface|page|tab)\s+(?:of\s+)?events)\b",
    re.IGNORECASE,
)
_PATH_OF_VENGEANCE = re.compile(r"\bpath\s+of\s+vengeance\b", re.IGNORECASE)
_EXIT_TERMS = re.compile(r"\b(back|close|exit|home|city|map|return)\b", re.IGNORECASE)


def event_action_rejection(command: dict[str, Any], advice: dict[str, Any]) -> str | None:
    """Reject event/battle actions except Path of Vengeance and safe exits."""
    action_type = str(command.get("action_type") or "").casefold()
    title_target = " ".join(
        str(command.get(key) or "") for key in ("title", "target", "action_type")
    )
    if _PATH_OF_VENGEANCE.search(title_target):
        return None

    screen = str(advice.get("current_screen") or "")
    mentions_event = bool(_EVENT_TERMS.search(title_target))
    on_event_screen = bool(_EVENT_SCREEN.search(screen))
    if not mentions_event and not on_event_screen:
        return None

    safe_exit = bool(_EXIT_TERMS.search(title_target)) and action_type in {
        "open_menu", "inspect_screen", "select_tab", "other"
    }
    if safe_exit and not re.search(r"\b(start|fight|attack|claim|complete|enter|battle)\b", title_target, re.IGNORECASE):
        return None
    return "Only Path of Vengeance is allowed; Expedition and other event actions are disabled."
