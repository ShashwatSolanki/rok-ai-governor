"""Region-based OCR utilities for user-provided Rise of Kingdoms screenshots."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import easyocr
import numpy as np
from PIL import Image

_reader: easyocr.Reader | None = None

# Normalised coordinates for the BlueStacks 5 city layout. Coordinates are
# fractions of the full screenshot so the regions scale with window size.
# These are initial layout presets, not universal coordinates for every screen.
CITY_REGIONS = {
    "player_header": (0.08, 0.05, 0.37, 0.25),
    "food": (0.61, 0.05, 0.74, 0.15),
    "wood": (0.73, 0.05, 0.85, 0.15),
    "stone": (0.85, 0.05, 0.97, 0.15),
    "timers": (0.63, 0.16, 1.00, 0.39),
    "quests": (0.00, 0.45, 0.20, 0.78),
}


def _get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        # CPU mode keeps setup predictable across machines.
        _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


def _number_token(text: str) -> str | None:
    """Return a resource-like number token without guessing its meaning."""
    match = re.search(r"\b\d+(?:[.,]\d+)?\s*[KMB]?\b", text, re.IGNORECASE)
    return match.group(0).replace(" ", "") if match else None


def _parse_header(texts: list[str]) -> dict[str, Any]:
    joined = " ".join(texts)
    result: dict[str, Any] = {}

    power = re.search(r"\b\d{1,3}(?:,\d{3})+\b", joined)
    if power:
        result["power"] = int(power.group(0).replace(",", ""))

    age = re.search(r"\b(Stone|Bronze|Iron|Dark)\s+Age\b", joined, re.IGNORECASE)
    if age:
        result["civilization_age"] = age.group(0).title()

    vip = re.search(r"\bVIP\s*(\d+)\b", joined, re.IGNORECASE)
    if vip:
        result["vip_level"] = int(vip.group(1))

    return result


def analyse_image(image_path: str | Path) -> dict[str, Any]:
    """Read selected game UI regions and return raw plus structured OCR results."""
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Screenshot not found: {path}")

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        image_array = np.asarray(rgb)

    height, width = image_array.shape[:2]
    reader = _get_reader()
    detections: list[dict[str, Any]] = []
    region_text: dict[str, list[str]] = {}

    for region_name, (x1, y1, x2, y2) in CITY_REGIONS.items():
        left = max(0, int(x1 * width))
        top = max(0, int(y1 * height))
        right = min(width, int(x2 * width))
        bottom = min(height, int(y2 * height))
        crop = image_array[top:bottom, left:right]
        if crop.size == 0:
            continue

        region_results = reader.readtext(crop)
        region_text[region_name] = []
        for bounds, detected_text, confidence in region_results:
            region_text[region_name].append(detected_text)
            detections.append({
                "text": detected_text,
                "confidence": round(float(confidence), 4),
                "region": region_name,
                "bounds": [
                    [round(float(point[0]) + left, 1), round(float(point[1]) + top, 1)]
                    for point in bounds
                ],
            })

    header = _parse_header(region_text.get("player_header", []))
    resources: dict[str, str | None] = {}
    for resource in ("food", "wood", "stone"):
        candidates = region_text.get(resource, [])
        resources[resource] = next(
            (token for text in candidates if (token := _number_token(text))),
            None,
        )

    quest_text = region_text.get("quests", [])
    timer_text = region_text.get("timers", [])
    detected_resource_count = sum(value is not None for value in resources.values())
    screen_type = (
        "city"
        if detected_resource_count >= 2 and bool(quest_text)
        else "unknown"
    )

    return {
        "source": str(path),
        "image_width": int(width),
        "image_height": int(height),
        "captured_or_analysed_at": datetime.now(timezone.utc).isoformat(),
        "engine": "EasyOCR",
        "screen": {
            "type": screen_type,
            "confidence_note": "Heuristic based on visible resource and quest regions.",
        },
        "account_state": {
            **header,
            "resources": resources,
        },
        "regions": {
            name: texts for name, texts in region_text.items()
        },
        "quest_text": quest_text,
        "timer_text": timer_text,
        "detections": detections,
        "note": (
            "OCR is approximate. Values are extracted from fixed city-layout "
            "regions and must be verified against the screenshot. A missing "
            "value means it was not confidently detected, not that it is zero."
        ),
    }


def save_observation(observation: dict[str, Any], output_dir: str | Path = "observations") -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = destination / f"ocr_{timestamp}.json"
    output_path.write_text(
        json.dumps(observation, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path
