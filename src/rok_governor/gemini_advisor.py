"""Gemini-powered farm strategy advisor.

Gemini is the decision-maker. Local OCR and account history are supplied as
evidence/context only; this module contains no deterministic farming policy.
Recommendations remain advisory and must be checked in the game.
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

from .account_store import AccountStore
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
        "what_to_inspect_next": {"type": "ARRAY", "items": {"type": "STRING"}},
        "do_not_assume": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["account_summary", "current_screen", "overall_assessment", "recommended_actions", "what_to_inspect_next", "do_not_assume"],
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

    account_history = AccountStore(database).latest_state()
    context = {
        "known_account_state": account_history,
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
            "Recommendations are advisory; never claim an action has been executed.",
        ],
    }
    prompt = (
        "You are the strategic farm-account governor for Rise of Kingdoms. "
        "Study the attached current game screenshot and the supplied account context. "
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
        "mode": "gemini_advisory",
        "source_screenshot": str(path),
        "advice": advice,
        "execution_status": "not_executed_manual_review_required",
    }


def save_advice(result: dict[str, Any], output: str | Path = "data/latest_gemini_advice.json") -> Path:
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return destination
