"""OCR utilities for user-provided game screenshots."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import easyocr
import numpy as np
from PIL import Image

_reader: easyocr.Reader | None = None


def _get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        # CPU mode keeps setup predictable across machines.
        _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


def analyse_image(image_path: str | Path) -> dict[str, Any]:
    """Read visible text from an image and return structured OCR results."""
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Screenshot not found: {path}")

    with Image.open(path) as image:
        rgb = image.convert("RGB")
        image_array = np.asarray(rgb)

    results = _get_reader().readtext(image_array)

    detections = []
    for bounds, text, confidence in results:
        detections.append(
            {
                "text": text,
                "confidence": round(float(confidence), 4),
                "bounds": [
                    [round(float(point[0]), 1), round(float(point[1]), 1)]
                    for point in bounds
                ],
            }
        )

    return {
        "source": str(path),
        "image_width": int(image_array.shape[1]),
        "image_height": int(image_array.shape[0]),
        "captured_or_analysed_at": datetime.now(timezone.utc).isoformat(),
        "engine": "EasyOCR",
        "detections": detections,
        "note": "OCR is approximate. Verify values against the screenshot.",
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
