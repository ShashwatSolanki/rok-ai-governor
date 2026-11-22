"""Deterministic allow-list and priority order for unattended game actions."""
from __future__ import annotations

import re
from typing import Any

TASK_ORDER = (
    "gathering",
    "training",
    "building_upgrade",
    "research",
    "city_resources",
    "quests",
    "barbarian_hunt",
)

_EVENT_TERMS = re.compile(r"\b(event|expedition|battlefield|arena|path of vengeance)\b", re.I)
_EVENT_SCREEN = re.compile(
    r"\b(expedition|event\s+(?:center|screen|menu|interface|page|tab)|"
    r"(?:center|screen|menu|interface|page|tab)\s+(?:of\s+)?events)\b", re.I
)
_EXIT_TERMS = re.compile(r"\b(back|close|exit|home|return|dismiss)\b", re.I)
_RESOURCE_TERMS = re.compile(r"\b(resource|resources|bubble|harvest|collect|food|wood|stone|gold)\b", re.I)
_QUEST_TERMS = re.compile(r"\b(quest|quests|mission|missions|objective|objectives)\b", re.I)
_BARBARIAN_TERMS = re.compile(r"\b(barbarian|barbarians)\b", re.I)
_CITY_MENU = re.compile(r"\b(?:city\s*escape|cityscape|building selection)\b.{0,35}\b(?:menu|screen|overlay|list|cards|selection)\b", re.I)
_EMPTY_GROUND = re.compile(r"\b(empty|blank|unoccupied|clear)\b.{0,35}\b(ground|area|space|part|spot)\b|\b(ground|area|space|part|spot)\b.{0,35}\b(empty|blank|unoccupied|clear)\b", re.I)
_CENTER_BUILDING = re.compile(r"\b(city center|city centre|central building|main building|building in the middle|middle building)\b", re.I)
_CITY_SCREEN = re.compile(r"\b(city|cityscape|city view)\b", re.I)
_AP_AVAILABLE = re.compile(
    r"\b(?:AP|action points?)\b\s*(?:(?:is|:|=)\s*)?[1-9]\d*(?:\s*/\s*[1-9]\d*)?|"
    r"\b(?:AP|action points?)\b\s+(?:available|remaining|left|positive|sufficient)\b(?!\s*[:=]?\s*0\b)|"
    r"\b(?:available|remaining|left|positive|sufficient)\s+(?:AP|action points?)\b(?!\s*[:=]?\s*0\b)",
    re.I,
)


def _text(command: dict[str, Any]) -> str:
    return " ".join(
        str(command.get(key) or "")
        for key in ("title", "target", "action_type")
    )


def action_priority(command: dict[str, Any], advice: dict[str, Any] | None = None) -> int | None:
    """Return task rank (0 is highest); None means outside the user's task list.

    Navigation is allowed only when its stated destination clearly serves one of
    the seven tasks, or when it exits an event screen. Barbarian hunts require
    AP availability to be stated in the command's visible-screen preconditions.
    """
    action_type = str(command.get("action_type") or "").casefold()
    text = _text(command)
    low = text.casefold()
    advice = advice or {}
    screen_text = " ".join(
        str(advice.get(key) or "")
        for key in ("current_screen", "screen_description")
    )
    menu_open = bool(_CITY_MENU.search(screen_text) or _CITY_MENU.search(text))
    if menu_open:
        target_text = " ".join((text, str(command.get("target") or "")))
        is_dismiss = bool(_EXIT_TERMS.search(text)) and action_type in {
            "open_menu", "inspect_screen", "select_tab", "other"
        }
        if is_dismiss and _EMPTY_GROUND.search(target_text):
            return -2
        # While this overlay/list is open, only dismiss it via empty ground.
        return None

    in_city = bool(_CITY_SCREEN.search(screen_text))
    if in_city and _CENTER_BUILDING.search(text) and action_type in {
        "open_menu", "inspect_screen", "other"
    }:
        return -1

    if _EVENT_TERMS.search(text):
        if _EXIT_TERMS.search(text) and action_type in {
            "open_menu", "inspect_screen", "select_tab", "other"
        } and not re.search(r"\b(start|fight|attack|claim|complete|enter|battle)\b", text, re.I):
            return 0
        return None

    if _BARBARIAN_TERMS.search(text):
        preconditions = command.get("preconditions") or []
        if isinstance(preconditions, str):
            preconditions = [preconditions]
        evidence = " ".join(str(item) for item in preconditions)
        if not _AP_AVAILABLE.search(evidence):
            return None
        return 6

    if action_type == "start_gathering" or re.search(r"\b(gather|gathering|march|resource node)\b", low):
        return 0
    if action_type == "train_troops" or re.search(r"\b(train|training|troops|barracks)\b", low):
        return 1
    if action_type == "upgrade_building" or re.search(r"\b(upgrade|building|construction)\b", low):
        return 2
    if action_type in {"start_research", "open_academy"} or re.search(r"\b(research|academy|technology|tech)\b", low):
        return 3
    if _QUEST_TERMS.search(text):
        return 5
    if action_type == "claim_reward" and _RESOURCE_TERMS.search(text):
        return 4
    if action_type in {"open_menu", "select_tab", "inspect_screen", "other"}:
        if _EXIT_TERMS.search(text):
            return 0
        # A navigation command must identify which authorized task it supports.
        if re.search(r"\b(map|march|gather|resource node)\b", low):
            return 0
        if re.search(r"\b(barracks|train|troop)\b", low):
            return 1
        if re.search(r"\b(building|upgrade|construction)\b", low):
            return 2
        if re.search(r"\b(academy|research|technology|tech)\b", low):
            return 3
        if re.search(r"\b(city resource|resource bubble|harvest|collect resource)\b", low):
            return 4
        if _QUEST_TERMS.search(text):
            return 5
    return None


def order_task_commands(
    commands: list[dict[str, Any]], advice: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Drop unapproved activities and stably sort eligible commands by priority."""
    indexed = [(action_priority(command, advice), i, command) for i, command in enumerate(commands)]
    return [command for rank, _, command in sorted(
        (entry for entry in indexed if entry[0] is not None),
        key=lambda entry: (entry[0], entry[1]),
    )]


def event_action_rejection(command: dict[str, Any], advice: dict[str, Any]) -> str | None:
    """Reject event controls except a clearly identified exit action."""
    text = _text(command)
    screen = str(advice.get("current_screen") or "")
    action_type = str(command.get("action_type") or "").casefold()
    if not (_EVENT_TERMS.search(text) or _EVENT_SCREEN.search(screen)):
        return None
    safe_exit = bool(_EXIT_TERMS.search(text)) and action_type in {
        "open_menu", "inspect_screen", "select_tab", "other"
    }
    if safe_exit and not re.search(r"\b(start|fight|attack|claim|complete|enter|battle)\b", text, re.I):
        return None
    return "Events and event actions are outside the seven authorized priorities."
