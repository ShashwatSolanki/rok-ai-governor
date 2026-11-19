"""Optional Gemini-powered visual interpretation of RoK screenshots.

This module is advisory only: it analyses pixels and returns structured
observations. It never interacts with the game client.
"""

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

# Keep the model configurable: availability and service load can change.
DEFAULT_MODEL = "gemini-3.6-flash"
GEMINI_TIMEOUT_MS = 45_000

COMMANDER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING", "nullable": True},
        "rarity": {"type": "STRING", "nullable": True},
        "level": {"type": "INTEGER", "nullable": True},
        "star_level": {"type": "NUMBER", "nullable": True},
        "skills_visible": {"type": "BOOLEAN"},
        "skill_details": {"type": "ARRAY", "items": {"type": "STRING"}},
        "specialties": {"type": "ARRAY", "items": {"type": "STRING"}},
        "evidence": {"type": "STRING"},
        "uncertain_fields": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "name", "rarity", "level", "star_level", "skills_visible",
        "skill_details", "specialties", "evidence", "uncertain_fields",
    ],
}

VISION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "screen_type": {
            "type": "STRING",
            "enum": [
                "city", "world_map", "commander_list", "commander_detail",
                "building_detail", "research", "hospital", "inventory",
                "mail", "event", "alliance", "other", "unknown",
            ],
        },
        "screen_description": {"type": "STRING"},
        "player_power": {"type": "INTEGER", "nullable": True},
        "civilization_age": {"type": "STRING", "nullable": True},
        "vip_level": {"type": "INTEGER", "nullable": True},
        "resources": {
            "type": "OBJECT",
            "properties": {
                name: {
                    "type": "OBJECT",
                    "properties": {
                        "raw": {"type": "STRING", "nullable": True},
                        "value": {"type": "INTEGER", "nullable": True},
                        "evidence": {"type": "STRING"},
                        "confidence": {"type": "NUMBER"},
                    },
                    "required": ["raw", "value", "evidence", "confidence"],
                }
                for name in ("food", "wood", "stone", "gold", "gems")
            },
            "required": ["food", "wood", "stone", "gold", "gems"],
        },
        "selected_commander": {
            "type": "OBJECT",
            "nullable": True,
            "properties": COMMANDER_SCHEMA["properties"],
            "required": COMMANDER_SCHEMA["required"],
        },
        "visible_commanders": {
            "type": "ARRAY",
            "items": COMMANDER_SCHEMA,
        },
        "visible_buildings": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visible_research": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visible_quests": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visible_activities": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visible_timers": {"type": "ARRAY", "items": {"type": "STRING"}},
        "new_unlocks": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertain_fields": {"type": "ARRAY", "items": {"type": "STRING"}},
        "notes": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "screen_type", "screen_description", "player_power",
        "civilization_age", "vip_level", "resources", "selected_commander",
        "visible_commanders", "visible_buildings", "visible_research",
        "visible_quests", "visible_activities", "visible_timers",
        "new_unlocks", "uncertain_fields", "notes",
    ],
}


def analyse_with_gemini(
    image_path: str | Path,
    ocr_observation: dict[str, Any] | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Interpret a screenshot with Gemini and return schema-constrained JSON."""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to a local .env file; "
            "see .env.example. Gemini is optional, so omit --gemini for local OCR only."
        )

    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Screenshot not found: {path}")

    prompt = (
        "You are a visual state observer for Rise of Kingdoms. Inspect the entire "
        "game screenshot and classify the currently open screen. This may be the "
        "city, world map, commander roster, commander detail, building, research, "
        "hospital, inventory, mail, event, or alliance screen. Do not assume it is "
        "the city screen.\n\n"
        "When commanders are visible, identify each commander only when the portrait "
        "or visible name supports the identification. Extract visible rarity, level, "
        "star level, specialties and skill text. For a selected commander, populate "
        "selected_commander. For a roster, populate visible_commanders. Never infer "
        "a commander identity from a level number or role icon alone.\n\n"
        "Read resources by matching each number to its adjacent resource icon/label, "
        "not by horizontal position alone. Convert K/M/B quantities to integers only "
        "when the suffix and number are legible. Read visible building/research names, "
        "quests, activities and timers where possible. Do not claim an unlock is new "
        "unless the screenshot explicitly indicates it; otherwise leave new_unlocks "
        "empty.\n\n"
        "Return only information visually supported by the screenshot. Use null for "
        "unreadable scalar fields, empty arrays for absent lists, and describe "
        "ambiguity in uncertain_fields. Do not invent hidden account data, upgrade "
        "levels, commander skills, or game state. The result is an observation, not "
        "a strategy decision."
    )
    if ocr_observation:
        prompt += (
            "\n\nLocal OCR is a secondary hint only. Verify it against the image; "
            "it may contain region-label mistakes or incorrect resource associations:\n"
            + json.dumps(
                {
                    "profile": ocr_observation.get("profile"),
                    "screen": ocr_observation.get("screen", {}),
                    "account_state": ocr_observation.get("account_state", {}),
                    "regions": ocr_observation.get("regions", {}),
                    "quest_text": ocr_observation.get("quest_text", []),
                    "timer_text": ocr_observation.get("timer_text", []),
                },
                ensure_ascii=False,
            )
        )

    with path.open("rb") as image_file:
        image_bytes = image_file.read()

    selected_model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=GEMINI_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=1),
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
            response_schema=VISION_SCHEMA,
            temperature=0.1,
        ),
    )

    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")
    try:
        result = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini returned invalid JSON despite structured output.") from exc

    return {
        "provider": "Google Gemini",
        "model": selected_model,
        "analysis": result,
        "note": (
            "Gemini output is a visual interpretation, not verified account data. "
            "It cannot establish that an item is newly unlocked without a previous "
            "observation. Compare uncertain fields with the screenshot and history."
        ),
    }
