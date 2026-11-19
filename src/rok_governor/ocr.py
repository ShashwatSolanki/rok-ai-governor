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
BLUESTACKS_REGIONS = {
    "player_header": (0.08, 0.05, 0.37, 0.25),
    "food": (0.61, 0.05, 0.74, 0.15),
    "wood": (0.73, 0.05, 0.85, 0.15),
    "stone": (0.85, 0.05, 0.97, 0.15),
    "timers": (0.63, 0.16, 1.00, 0.39),
    "quests": (0.00, 0.45, 0.20, 0.78),
}

# Initial normalized regions calibrated from the supplied official PC client capture.
PC_REGIONS = {
    "player_header": (0.00, 0.00, 0.24, 0.19),
    # The native 1920x1080 client places all five resource counters in one
    # horizontal strip. These bounds isolate each icon/value pair.
    "food": (0.565, 0.00, 0.72, 0.075),
    "wood": (0.72, 0.00, 0.80, 0.075),
    "stone": (0.80, 0.00, 0.87, 0.075),
    "gold": (0.87, 0.00, 0.945, 0.075),
    "gems": (0.945, 0.00, 0.995, 0.075),
    "timers": (0.82, 0.08, 1.00, 0.42),
    "quests": (0.00, 0.20, 0.16, 0.72),
}

OCR_PROFILES = {"bluestacks": BLUESTACKS_REGIONS, "pc": PC_REGIONS}

REVIEW_CONFIDENCE = 0.75


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


def _to_quantity(token: str) -> int | None:
    """Convert abbreviated game quantities to integers (e.g. 956.1K)."""
    match = re.fullmatch(r"(\d+(?:[.,]\d+)?)([KMB]?)", token, re.IGNORECASE)
    if not match:
        return None
    number_text = match.group(1)
    if "," in number_text and "." in number_text:
        # Conventional thousands separators plus decimal point: 1,234.5
        number_text = number_text.replace(",", "")
    elif re.fullmatch(r"\\d{1,3}(,\\d{3})+", number_text):
        # Grouped integer: 3,425 must mean three thousand four hundred twenty-five.
        number_text = number_text.replace(",", "")
    else:
        # Decimal comma, if present: 956,1K.
        number_text = number_text.replace(",", ".")
    number = float(number_text)
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(round(number * multiplier[match.group(2).upper()]))


def _parse_header(detections: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for detection in detections:
        text = detection["text"]
        confidence = detection["confidence"]

        power = re.search(r"\b\d{1,3}(?:,\d{3})+\b", text)
        if power:
            result["power"] = {
                "value": int(power.group(0).replace(",", "")),
                "confidence": confidence,
                "needs_review": confidence < REVIEW_CONFIDENCE,
            }

        age = re.search(
            r"\b(Stone|Bronze|Iron|Dark)\s+Age\b", text, re.IGNORECASE
        )
        if age:
            result["civilization_age"] = {
                "value": age.group(0).title(),
                "confidence": confidence,
                "needs_review": confidence < REVIEW_CONFIDENCE,
            }

        vip = re.search(r"\bVIP\s*(\d+)\b", text, re.IGNORECASE)
        if vip:
            result["vip_level"] = {
                "value": int(vip.group(1)),
                "confidence": confidence,
                "needs_review": confidence < REVIEW_CONFIDENCE,
            }
    return result


def analyse_image(image_path: str | Path, profile: str = "bluestacks") -> dict[str, Any]:
    """Read selected game UI regions and return structured OCR results."""
    if profile not in OCR_PROFILES:
        raise ValueError(f"Unknown OCR profile {profile!r}; choose from {', '.join(OCR_PROFILES)}")
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

    for region_name, (x1, y1, x2, y2) in OCR_PROFILES[profile].items():
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

    header_detections = [
        item for item in detections if item["region"] == "player_header"
    ]
    header = _parse_header(header_detections)

    resources: dict[str, dict[str, Any] | None] = {}
    for resource in ("food", "wood", "stone", "gold", "gems"):
        candidates = [
            item for item in detections if item["region"] == resource
        ]
        selected = next(
            (
                (item, token)
                for item in candidates
                if (token := _number_token(item["text"])) is not None
            ),
            None,
        )
        if selected is None:
            resources[resource] = None
            continue

        item, token = selected
        resources[resource] = {
            "raw": token,
            "value": _to_quantity(token),
            "confidence": item["confidence"],
            "needs_review": item["confidence"] < REVIEW_CONFIDENCE,
        }

    quest_text = region_text.get("quests", [])
    timer_text = region_text.get("timers", [])
    detected_resource_count = sum(value is not None for value in resources.values())
    screen_type = (
        "city"
        if (detected_resource_count >= 2 and bool(quest_text))
        or (profile == "pc" and "power" in header and detected_resource_count >= 1)
        else "unknown"
    )

    return {
        "source": str(path),
        "image_width": int(width),
        "image_height": int(height),
        "captured_or_analysed_at": datetime.now(timezone.utc).isoformat(),
        "engine": "EasyOCR",
        "profile": profile,
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
        "review_required": (
            any(item is None for item in resources.values())
            or any(
                item.get("needs_review", False)
                for item in [
                    *header.values(),
                    *(value for value in resources.values() if value is not None),
                ]
            )
        ),
        "note": (
            "OCR is approximate. Abbreviated resource quantities are converted "
            "to integers while retaining their raw text and confidence. "
            "Low-confidence values are flagged for review. A missing value "
            "means it was not detected, not that it is zero."
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
