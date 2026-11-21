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

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

from .account_store import AccountStore
from .task_store import TaskStore
from .gemini_vision import DEFAULT_MODEL

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
                    "input_actions": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"type": {"type": "STRING", "enum": ["click"]}, "x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}, "purpose": {"type": "STRING"}}, "required": ["type", "x", "y", "purpose"]}}
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
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env.")
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with Image.open(path) as screenshot_image:
        screenshot_width, screenshot_height = screenshot_image.size

    account_history = AccountStore(database).latest_state()
    task_store = TaskStore(database)
    pending_tasks = task_store.list("pending")
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
        for command in pending_commands[:30]
    ]
    context = {
        "strategic_goal": os.getenv(
            "ROKGOV_GOAL",
            "Run autonomously: prioritize gathering resources, grow power by upgrading buildings, "
            "researching technology, and training troops continuously. No real-money purchases.",
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
            "Prioritize practical farm-account progression and resource gathering based on evidence visible in the screenshot.",
            "Explain uncertainty and cite the visual evidence for every action.",
            "Return a small prioritized queue, not generic filler. It is acceptable to return no actions.",
            "Before proposing any command, compare its intent and target with existing_pending_commands as well as existing_pending_actions and recent_task_history. Treat differently worded actions that accomplish the same goal as duplicates. If an equivalent command is already proposed, do not emit it again; the existing proposal remains pending. Do not repeat navigation commands (such as opening the Academy or switching to Map View) unless the current screen visibly shows that navigation is still needed and the previous proposal is no longer applicable.",
            "Return at most 3 commands, ordered by priority and immediate usefulness. Prefer one next step over a long chain of speculative navigation. It is valid to return zero commands when existing proposals already cover the visible needs.",
            "New recommendations and commands are proposals only; do not claim they have been executed. Assess previously posted actions separately through execution_reviews using only current visible evidence.",
            "For each recommended action that can be expressed as a concrete in-game operation, also emit a structured command. Commands must be declarative data, never code, shell commands, macros, or key sequences.",
            "Each command must state exact visible target, preconditions, ordered steps, a way to verify the result, rationale, risk, and confidence. If target or prerequisites are not visible, emit inspect_screen instead of guessing.",
            "CRITICAL SAFETY RULE: You are STRICTLY FORBIDDEN from clicking on or initiating ANY real-money purchases, bundle buys with real currency, cash shop offers, or payment dialogues. If an in-app purchase window appears, close or dismiss it.",
            "FULL CITY GOVERNANCE AUTONOMY: The user has authorized complete autonomous control of this trial account. You ARE permitted and expected to emit structured commands with explicit click targets (input_actions) for: (1) Gathering resources from kingdom map nodes and managing gatherer marches; (2) Harvesting floating city resources and claiming free rewards/chests; (3) Starting economic and military research at the Academy; (4) Upgrading buildings (Town Hall, resource production, barracks, walls, etc.) using in-game resources; (5) Training and upgrading troops; (6) Helping alliance members (handshake icon); (7) Screen and menu navigation. Spending in-game resources (food, wood, stone, gold) to grow the city and expand power is fully authorized.",
            "For each recommended action that can be expressed as a concrete in-game operation, emit a structured command with exact visible target, preconditions, ordered steps, verification criterion, rationale, and confidence.",
            "For any authorized in-game action where the target button/control is visible on the current screenshot, provide input_actions with explicit click coordinates in normalized screenshot coordinates 0..1000 (0,0 top-left). Choose the visual CENTER of the intended button/control (e.g. Upgrade button, Research button, Train button, Gather button, Harvest bubble, Help handshake). Every click must have a clear purpose. Provide exactly one click per screenshot. Label regular in-game actions as risk 'low' so they can be dispatched immediately.",
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
        "You, Gemini, must reason about what the player should do next. Do not apply "
        "fixed reserve thresholds or hard-coded priorities. Make context-sensitive "
        "decisions from the visible game state, account progression, and stated farm "
        "objective. If this screen is insufficient, specify exactly what screen to "
        "inspect next. Never infer hidden game state. Return the requested JSON.\n\n"
        + json.dumps(context, ensure_ascii=False)
    )

    with path.open("rb") as image_file:
        image_bytes = image_file.read()
    selected_model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=120_000,
            # Let the SDK retry transient 429/5xx responses instead of failing
            # the entire advice cycle on a single overloaded Gemini request.
            retry_options=types.HttpRetryOptions(attempts=3),
        ),
    )
    response = client.models.generate_content(
        model=selected_model,
        contents=[
            prompt,
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ADVICE_SCHEMA,
            temperature=0.2,
        ),
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty farm-advice response.")
    try:
        advice = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini returned invalid JSON for farm advice.") from exc

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "Google Gemini",
        "model": selected_model,
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
