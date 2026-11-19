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
    context = {
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
            "Before recommending an action, compare it with existing_pending_actions and recent_task_history. Do not create a differently worded duplicate of an already-pending goal. Do not repeat completed or dismissed goals unless current screenshot evidence shows a genuinely new instance or changed need; explain why it is new.",
            "New recommendations and commands are proposals only; do not claim they have been executed. Assess previously posted actions separately through execution_reviews using only current visible evidence.",
            "For each recommended action that can be expressed as a concrete in-game operation, also emit a structured command. Commands must be declarative data, never code, shell commands, macros, or key sequences.",
            "Each command must state exact visible target, preconditions, ordered steps, a way to verify the result, rationale, risk, and confidence. If target or prerequisites are not visible, emit inspect_screen instead of guessing.",
            "Do not include commands to spend gems, buy bundles, use rare items, or make irreversible account changes. For those, explain the decision in advice only and mark that human judgment is required.",
            "For low-risk routine actions only, provide input_actions as explicit click targets in normalized screenshot coordinates 0..1000 (0,0 top-left). The screenshot_geometry gives the exact pixel width and height. Choose the visual CENTER of the intended button/control, not its edge or a nearby empty area. Estimate pixel center (px, py) first, then convert using x=round(px/width_pixels*1000) and y=round(py/height_pixels*1000). Before returning a click, verify that converting the normalized values back (x/1000*width_pixels, y/1000*height_pixels) lands on the visible control. Never guess coordinates from the window border or confuse screenshot pixels with client coordinates. If the control center or its coordinates are uncertain, omit input_actions and explain what needs inspection. Provide exactly one click per screenshot, and only when the exact target is clearly visible. Every click must have a purpose. Navigation-only actions such as opening the Academy may use action_type open_academy; this does not authorize starting research. The governor re-captures the window before dispatch and reassesses the next screen before any subsequent click. For anything uncertain or involving resource costs, omit input_actions. Never emit keyboard actions, text entry, arbitrary coordinates unrelated to a visible control, purchases, gems, rare items, upgrades, training, research starts, or irreversible actions. The local governor independently enforces an allow-list and confidence threshold before any click. A click is not proof of success; require a fresh screenshot to assess the outcome.",
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
