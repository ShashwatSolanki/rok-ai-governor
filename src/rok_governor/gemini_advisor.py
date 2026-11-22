"""Gemini-powered farm strategy advisor.

Gemini is the decision-maker. Local OCR and account history are supplied as
evidence/context only; this module contains no deterministic farming policy.
Gemini emits structured candidate actions; the local controller enforces a separate bounded execution policy.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from .account_store import AccountStore
from .task_store import TaskStore
from .gemini_vision import DEFAULT_MODEL
from .gemini_profiles import generate_with_profiles, load_candidate_profiles, request_profile

ADVICE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "account_summary": {"type": "STRING"},
        "current_screen": {"type": "STRING"},
        "overall_assessment": {"type": "STRING"},
        "recommended_actions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "priority": {"type": "STRING", "enum": ["urgent", "high", "normal", "low"]},
                    "action": {"type": "STRING"},
                    "category": {"type": "STRING", "enum": ["gathering", "commander", "upgrade", "research", "city", "account", "other"]},
                    "why": {"type": "STRING"},
                    "visual_evidence": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                    "uncertainties": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "manual_steps": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["priority", "action", "category", "why", "visual_evidence", "confidence", "uncertainties", "manual_steps"],
            },
        },
        "commands": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "action_type": {"type": "STRING", "enum": ["inspect_screen", "open_menu", "open_academy", "select_tab", "start_gathering", "start_research", "upgrade_building", "train_troops", "claim_reward", "manage_commander", "other"]},
                    "title": {"type": "STRING"},
                    "target": {"type": "STRING"},
                    "parameters": {"type": "OBJECT"},
                    "preconditions": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "steps": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "verification": {"type": "STRING"},
                    "rationale": {"type": "STRING"},
                    "risk": {"type": "STRING", "enum": ["low", "medium", "high", "unknown"]},
                    "confidence": {"type": "NUMBER"},
                    "input_actions": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"type": {"type": "STRING", "enum": ["click", "swipe"]}, "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}, "x2": {"type": "INTEGER"}, "y2": {"type": "INTEGER"}, "duration_ms": {"type": "INTEGER"}, "purpose": {"type": "STRING"}}, "required": ["type", "x", "y", "purpose"]}}
                },
                "required": ["action_type", "title", "target", "parameters", "preconditions", "steps", "verification", "rationale", "risk", "confidence", "input_actions"]
            }
        },
        "task_reviews": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "task_id": {"type": "INTEGER"},
                    "assessment": {"type": "STRING", "enum": ["likely_completed", "still_pending", "uncertain"]},
                    "visual_evidence": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["task_id", "assessment", "visual_evidence", "confidence"],
            },
        },
        "execution_reviews": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "execution_id": {"type": "INTEGER"},
                    "assessment": {"type": "STRING", "enum": ["verified", "failed", "uncertain"]},
                    "visual_evidence": {"type": "STRING"},
                },
                "required": ["execution_id", "assessment", "visual_evidence"],
            },
        },
        "what_to_inspect_next": {"type": "ARRAY", "items": {"type": "STRING"}},
        "do_not_assume": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["account_summary", "current_screen", "overall_assessment", "recommended_actions", "commands", "task_reviews", "execution_reviews", "what_to_inspect_next", "do_not_assume"],
}


def advise_with_gemini(
    image_path: str | Path,
    *,
    profile: str = "pc",
    database: str | Path = "data/account_history.sqlite3",
    ocr_observation: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Ask Gemini to interpret the current game screen and choose next actions."""
    profiles = load_candidate_profiles(model_override=model, default_model=DEFAULT_MODEL)
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with Image.open(path) as screenshot_image:
        screenshot_width, screenshot_height = screenshot_image.size

    account_history = AccountStore(database).latest_state()
    task_store = TaskStore(database)
    # Keep the multimodal request bounded. Long-running sessions can accumulate
    # hundreds of pending tasks; sending all of them twice bloats provider bodies
    # and causes HTTP 413 errors. TaskStore remains complete locally.
    pending_tasks = task_store.list("pending")[:20]
    recent_task_history = task_store.list("all")[:40]
    review_context = [
        {
            "task_id": task["id"],
            "action": task["action"],
            "visual_evidence_when_created": task["visual_evidence"],
            "manual_steps": task["manual_steps"],
            "last_seen": task["last_seen"],
        }
        for task in pending_tasks
    ]
    from .execution_store import ExecutionStore
    pending_execution_reviews = ExecutionStore(database).pending_verification()
    from .command_store import CommandStore
    pending_commands = CommandStore(database).list("proposed")
    pending_command_context = [
        {
            "action_type": command["action_type"],
            "title": command["title"],
            "target": command["target"],
            "seen_count": command["seen_count"],
            "last_seen": command["last_seen"],
        }
        for command in pending_commands[:20]
    ]
    context = {
        "strategic_goal": os.getenv(
            "ROKGOV_GOAL",
            "ONLY perform these tasks in this exact priority order: (1) send available armies to gather, "
            "(2) train troops, (3) upgrade buildings, (4) research, (5) collect resources from the city, "
            "(6) complete quests, (7) hunt barbarians only when visible AP remains. "
            "Do not perform any other activities. No real-money purchases.",
        ),
        "screenshot_geometry": {
            "width_pixels": screenshot_width,
            "height_pixels": screenshot_height,
            "coordinate_origin": "top-left",
            "normalized_coordinate_range": "0..1000",
        },
        "known_account_state": account_history,
        "pending_tasks_to_verify": review_context,
        "posted_actions_to_visually_verify": pending_execution_reviews,
        "existing_pending_actions": [
            {"task_id": task["id"], "category": task["category"], "action": task["action"]}
            for task in pending_tasks
        ],
        "existing_pending_commands": pending_command_context,
        "recent_task_history": [
            {"task_id": task["id"], "status": task["status"], "category": task["category"],
             "action": task["action"], "last_seen": task["last_seen"]}
            for task in recent_task_history
        ],
        "latest_local_ocr": ocr_observation or {},
        "important_rules": [
            "Treat screenshot pixels as primary evidence; OCR may be wrong.",
            "Use prior account values as historical context, not proof of current values.",
            "Do not invent commander ownership, march availability, troop counts, building levels, costs, unlocks, timers, or map targets.",
            "If a required screen or detail is not visible, ask to inspect that screen instead of guessing.",
            "Do not recommend spending gems or rare items unless the screen clearly supports a compelling reason; explain the trade-off.",
            "CITY CLICK SAFETY PREPARATION: Before the first task click while the current screen is the city, inspect the central/main building. If it is clearly identifiable, unobstructed, not already selected/open, and its tap area does not overlap any button or overlay, emit one command to tap that building and inspect its visible state before proceeding to task actions. Do not guess its coordinates or tap a button that overlaps it. If it is already selected/open, obscured, or not safely distinguishable, skip this preparation and use only clearly separated visible targets.",
            "CITY ESCAPE / CITYSCAPE MENU: If a city building-selection/list overlay is open and needs to be dismissed, click only a clearly visible empty ground area outside the building list, buildings, and buttons. The command target and click purpose must explicitly say it is empty/unoccupied ground. Never click a listed building or a control to dismiss this overlay. If no safe empty area is visible, emit no click.",
            "USER'S FIXED TASK PRIORITY (STRICT): (1) send available armies to gather; (2) train troops; (3) upgrade buildings; (4) research; (5) collect resources from the city; (6) complete quests; (7) hunt barbarians only when the screenshot visibly confirms AP remains. Choose the highest-ranked task that is currently feasible, and do not do a lower-ranked task while a higher-ranked task is available. If a task is unavailable or blocked by a visible prerequisite, proceed to the next task. Never infer AP availability; include the visible positive AP evidence in the barbarian command's preconditions.",
            "ONLY THESE SEVEN ACTIVITIES ARE AUTHORIZED, plus the two city-screen preparation steps above. Do not do alliance help, optional or daily rewards, events (including Path of Vengeance), Expedition, non-barbarian battles, commander management, or any other activity. Navigation is allowed only as needed to reach the highest-ranked currently feasible authorized task, or to exit a disallowed event screen.",
            "Explain uncertainty and cite the visual evidence for every action.",
            "Return a small prioritized queue, not generic filler. It is acceptable to return no actions.",
            "IMMEDIATE ACTION MANDATE: Whenever a target for the highest currently feasible task is visible, emit a concrete structured command with explicit normalized click coordinates in input_actions. Return authorized commands in the exact priority order above. Do not repeat an unresolved action unless fresh screenshot evidence shows the prior attempt failed and the action can safely be retried.",
            "For each concrete in-game operation that can be performed on the current screen, emit a structured command with action_type, title, target, input_actions with normalized (x, y) 0..1000, preconditions, verification, and risk 'low'.",
            "New recommendations and commands are proposals only; do not claim they have been executed. Assess previously posted actions separately through execution_reviews using only current visible evidence.",
            "For each recommended action that can be expressed as a concrete in-game operation, also emit a structured command. Commands must be declarative data, never code, shell commands, macros, or key sequences.",
            "Each command must state exact visible target, preconditions, ordered steps, a way to verify the result, rationale, risk, and confidence. If target or prerequisites are not visible, emit inspect_screen instead of guessing.",
            "CRITICAL SAFETY RULE: You are STRICTLY FORBIDDEN from clicking on or initiating ANY real-money purchases, bundle buys with real currency, cash shop offers, or payment dialogues. If an in-app purchase window appears, close or dismiss it.",
            "AUTOMATION SCOPE: Emit commands only for the seven authorized tasks or navigation strictly necessary to reach the highest-ranked currently feasible task. City resource collection means visible resource pickups from the city. Quest activity means completing/claiming a quest objective; do not claim unrelated rewards. A barbarian hunt is authorized only when the screenshot shows AP above zero; cite that visible AP in command preconditions. Spending ordinary in-game resources needed for those tasks is authorized. No real-money purchases.",
            "For each recommended action that can be expressed as a concrete in-game operation, emit a structured command with exact visible target, preconditions, ordered steps, verification criterion, rationale, and confidence.",
            "For any authorized in-game action where the target button/control is visible on the current screenshot, provide input_actions with explicit coordinates in normalized screenshot coordinates 0..1000 (0,0 top-left). You may emit either type 'click' (with x, y) or type 'swipe' (with start x, y and end x2, y2, duration_ms) to drag/slide the screen or scroll menus. For zooming: prefer the built-in game buttons (the Map/City view toggle icon at the bottom-left to zoom between city and kingdom map, and the magnifying glass Search button on the map to automatically fly to resource nodes without manual panning). Every action must have a clear purpose. Label regular in-game actions as risk 'low' so they can be dispatched immediately.",
            "CITY PANNING & DRAGGING: Rise of Kingdoms city view may show only part of the city. Pan only when needed to reach a visible building for training, upgrading, research, or a city resource pickup. For example, swipe (750, 500) -> (250, 500) to pan right; swipe (250, 500) -> (750, 500) to pan left; swipe (500, 750) -> (500, 250) to pan down. Do not explore other districts for unrelated activities.",
            "For each supplied pending task, return one task_review only when the current screenshot provides relevant evidence; otherwise use uncertain with an explanation.",
            "Use likely_completed only when the visible screen strongly supports that the specific task's intended result occurred. Disappearance of an icon alone may be ambiguous.",
            "Never treat a task as completed merely because it is old or not visible on this screen. Use still_pending only when the screenshot visibly shows the task remains undone.",
            "Task reviews are suggestions for human confirmation, not status changes. Do not invent task IDs.",
            "For each posted action in posted_actions_to_visually_verify, return exactly one execution_review using its execution_id. Mark verified only when the current screenshot clearly shows the stated result; mark failed only when the screen clearly contradicts the expected result; otherwise mark uncertain. Never infer success merely because a click was posted. Cite visible evidence and do not invent IDs.",
        ],
    }
    prompt = (
        "You are the strategic farm-account governor for Rise of Kingdoms. "
        "Study the attached current game screenshot and the supplied account context. "
        "Also compare visible evidence with pending tasks and posted automation actions, returning cautious task_reviews and execution_reviews. "
        "The model must reason about what the player should do next. On a clear city screen, "
        "inspect the central building first when it is visibly identifiable and not already open. "
        "Then respect the user's "
        "explicit priority order while using visible game state and prerequisites to choose "
        "the next feasible action. If this screen is insufficient, specify exactly what screen to "
        "inspect next. Never infer hidden game state. Return the requested JSON.\n\n"
        + json.dumps(context, ensure_ascii=False)
    )

    with path.open("rb") as image_file:
        image_bytes = image_file.read()
    def request(profile):
        return request_profile(profile, prompt, image_bytes, schema=ADVICE_SCHEMA,
                               temperature=0.2, timeout_ms=120_000)

    used_profile, response = generate_with_profiles(profiles, request)
    used_model = used_profile.model

    if not response.text:
        raise RuntimeError(f"Gemini ({used_model}) returned an empty farm-advice response.")
    try:
        advice = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini ({used_model}) returned invalid JSON for farm advice.") from exc

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": used_profile.provider,
        "model": used_model,
        "credential_profile": used_profile.name,
        "mode": "gemini_strategy",
        "source_screenshot": str(path),
        "advice": advice,
        "execution_status": "candidate_actions_require_local_policy_validation",
    }


def save_advice(result: dict[str, Any], output: str | Path = "data/latest_gemini_advice.json") -> Path:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return destination
