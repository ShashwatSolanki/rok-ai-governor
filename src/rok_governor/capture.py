"""Read-only desktop screenshot capture."""

from datetime import datetime
from pathlib import Path

import mss
from PIL import Image


def capture_primary_monitor(output_dir: str | Path = "screenshots") -> Path:
    """Capture the primary monitor to a timestamped PNG file.

    This function only captures pixels; it does not interact with the game.
    """
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = destination / f"desktop_{timestamp}.png"

    with mss.mss() as screen:
        monitor = screen.monitors[1]
        shot = screen.grab(monitor)
        image = Image.frombytes("RGB", shot.size, shot.rgb)
        image.save(output_path, format="PNG")

    return output_path
