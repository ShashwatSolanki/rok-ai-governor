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

DEFAULT_MODEL = "gemini-3.8-flash"

VISION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "screen_type": {
            "type": "STRING",
            "enum": ["city", "world_map", "commander", "building", "research", "other", "unknown"],
        },
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
                    },
                    "required": ["raw", "value", "evidence"],
                }
                for name in ("food", "wood", "stone", "gold")
            },
            "required": ["food", "wood", "stone", "gold"],
        },
        "visible_quests": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visible_activities": {"type": "ARRAY", "items": {"type": "STRING"}},
        "uncertain_fields": {"type": "ARRAY", "items": {"type": "STRING"}},
        "notes": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [
        "screen_type", "player_power", "civilization_age", "vip_level",
        "resources", "visible_quests", "visible_activities",
        "uncertain_fields", "notes",
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
            "see .env.example. Gemini is optional, so you can omit --gemini "
            "to use local OCR only."
        )

    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Screenshot not found: {path}")

    prompt = (
        "Analyse this Rise of Kingdoms game screenshot. Return only observations "
        "that are visibly supported by the image. Identify the screen type, "
        "player power, civilization age, VIP level, visible food/wood/stone/gold "
        "amounts, quests, and visible activities or timers. Convert abbreviated "
        "resource amounts such as 956.1K or 1.2M to integers when legible. "
        "If a value is obscured, cropped, or ambiguous, return null and list "
        "the field in uncertain_fields. Do not infer hidden account data, "
        "do not guess missing values, and do not treat emulator window chrome "
        "as game information. Resource labels must be matched to their icons, "
        "not merely to horizontal position."
    )
    if ocr_observation:
        prompt += (
            "\n\nLocal OCR output is provided as a secondary hint. Verify it "
            "against the image; it may contain mistakes:\n"
            + json.dumps(
                {
                    "account_state": ocr_observation.get("account_state", {}),
                    "quest_text": ocr_observation.get("quest_text", []),
                    "timer_text": ocr_observation.get("timer_text", []),
                },
                ensure_ascii=False,
            )
        )

    mime_type = "image/png"
    with path.open("rb") as image_file:
        image_bytes = image_file.read()

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
        contents=[
            prompt,
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
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
        "model": model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
        "analysis": result,
        "note": (
            "Gemini output is a visual interpretation, not verified account data. "
            "Compare it with local OCR and the original screenshot before relying on it."
        ),
    }
