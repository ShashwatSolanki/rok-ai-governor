"""Window-targeted background mouse input for the RoK governor.

Only one bounded left-click per fresh visual assessment is supported. Inputs
are posted to the selected window and do not move the physical mouse or
activate the game window. Some game clients ignore background messages.
"""
from __future__ import annotations

import math
import time
from typing import Any


class WindowInputError(RuntimeError):
    """A safe, window-scoped input could not be delivered."""


def post_click(
    title_filter: str,
    *,
    x_normalized: int,
    y_normalized: int,
    screenshot_size: tuple[int, int] | None = None,
    hold_seconds: float = 0.08,
) -> dict[str, Any]:
    """Post one left-click to normalized coordinates in a unique game window.

    Coordinates use a 0..1000 range over the captured window rectangle. This
    method never foregrounds the target and never uses desktop-wide input.
    """
    if type(x_normalized) is not int or type(y_normalized) is not int:
        raise WindowInputError("Click coordinates must be integer normalized values.")
    if not 0 <= x_normalized <= 1000 or not 0 <= y_normalized <= 1000:
        raise WindowInputError("Click rejected: normalized coordinates must be within 0..1000.")
    if screenshot_size is not None:
        if (not isinstance(screenshot_size, tuple) or len(screenshot_size) != 2
                or any(type(value) is not int or value <= 0 for value in screenshot_size)):
            raise WindowInputError(f"Invalid screenshot geometry: {screenshot_size!r}")
    if not math.isfinite(hold_seconds) or not 0.04 <= hold_seconds <= 0.2:
        raise WindowInputError("Click hold duration is outside the permitted range.")

    try:
        import win32con
        import win32gui
    except ImportError as exc:  # pragma: no cover - Windows-only runtime
        raise WindowInputError("Background input requires pywin32 on Windows.") from exc

    from .window_capture import find_windows

    candidates = find_windows(title_filter)
    if len(candidates) != 1:
        raise WindowInputError(
            f"Expected exactly one matching game window; found {len(candidates)}. "
            "Use a more specific --title."
        )
    target = candidates[0]
    if target["minimized"]:
        raise WindowInputError("Target game window is minimised; refusing to send input.")

    hwnd = target["hwnd"]
    if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        raise WindowInputError("Target game window is no longer available.")

    left, top, right, bottom = target["rect"]
    window_width, window_height = right - left, bottom - top
    if screenshot_size is None:
        screenshot_width, screenshot_height = window_width, window_height
    else:
        screenshot_width, screenshot_height = screenshot_size

    # Gemini coordinates are normalized against the actual screenshot pixels.
    # Scale them into the current Win32 window rectangle before converting to
    # client coordinates; this accounts for DPI virtualization and any size
    # discrepancy between PrintWindow's bitmap and GetWindowRect.
    screenshot_x = round(x_normalized * screenshot_width / 1000)
    screenshot_y = round(y_normalized * screenshot_height / 1000)
    screenshot_x = min(screenshot_width - 1, screenshot_x)
    screenshot_y = min(screenshot_height - 1, screenshot_y)
    window_x = round(screenshot_x * window_width / screenshot_width)
    window_y = round(screenshot_y * window_height / screenshot_height)
    screen_x = left + window_x
    screen_y = top + window_y
    client_x, client_y = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
    client_rect = win32gui.GetClientRect(hwnd)
    client_width, client_height = client_rect[2] - client_rect[0], client_rect[3] - client_rect[1]
    geometry = (
        f"target={target['title']!r}, normalized=({x_normalized},{y_normalized}), "
        f"screenshot={screenshot_width}x{screenshot_height}, "
        f"window_rect={(left, top, right, bottom)}, "
        f"client_rect={client_rect}, mapped_client=({client_x},{client_y})"
    )
    # Validate against the real client area rather than an arbitrary normalized
    # inset: legitimate controls can sit close to the top or side of the game.
    if not (2 <= client_x < client_width - 2 and 2 <= client_y < client_height - 2):
        raise WindowInputError(f"Click rejected: mapped point is outside the safe client area; {geometry}")

    lparam = ((client_y & 0xFFFF) << 16) | (client_x & 0xFFFF)
    down_posted = False
    try:
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        down_posted = True
        time.sleep(hold_seconds)
    except Exception as exc:
        raise WindowInputError(f"Windows rejected the background click: {exc}") from exc
    finally:
        if down_posted:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
            except Exception as exc:
                raise WindowInputError(f"Could not release the background mouse button: {exc}") from exc

    return {
        "window_title": target["title"],
        "hwnd": hwnd,
        "x_normalized": x_normalized,
        "y_normalized": y_normalized,
        "client_x": client_x,
        "client_y": client_y,
        "screenshot_pixel": [screenshot_x, screenshot_y],
        "screenshot_size": [screenshot_width, screenshot_height],
        "window_rect": [left, top, right, bottom],
        "client_rect": list(client_rect),
        "dpi_awareness": getattr(__import__("rok_governor.window_capture", fromlist=["DPI_AWARENESS"]), "DPI_AWARENESS", "unknown"),
        "sent_at": time.time(),
        "delivery": "posted_to_window_not_foregrounded",
    }


def execute_safe_actions(
    title_filter: str,
    command: dict[str, Any],
    *,
    minimum_confidence: float = 0.88,
) -> list[dict[str, Any]]:
    """Execute one allow-listed low-risk click from a fresh Gemini assessment."""
    allowed_command_types = {"inspect_screen", "open_menu", "open_academy", "select_tab", "start_gathering"}
    if command.get("action_type") not in allowed_command_types:
        raise WindowInputError("Command type is not enabled for unattended execution.")
    if str(command.get("risk", "unknown")).strip().casefold() != "low":
        raise WindowInputError("Only Gemini-labelled low-risk commands may run unattended.")

    confidence = command.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not minimum_confidence <= confidence <= 1.0
    ):
        raise WindowInputError("Command confidence must be between the threshold and 1.0.")
    if not isinstance(command.get("title"), str) or not command["title"].strip():
        raise WindowInputError("Command title is missing.")
    if not isinstance(command.get("target"), str) or not command["target"].strip():
        raise WindowInputError("Command target is missing.")
    preconditions = command.get("preconditions")
    if (
        not isinstance(preconditions, list)
        or not preconditions
        or any(not isinstance(item, str) or not item.strip() for item in preconditions)
    ):
        raise WindowInputError("Command has no valid explicit preconditions.")
    if not isinstance(command.get("verification"), str) or not command["verification"].strip():
        raise WindowInputError("Command has no verification criterion.")

    actions = command.get("input_actions")
    if not isinstance(actions, list) or len(actions) != 1:
        raise WindowInputError("Exactly one click is allowed per visual assessment.")
    action = actions[0]
    if not isinstance(action, dict) or action.get("type") != "click":
        raise WindowInputError("Only explicit left-click actions are supported.")
    purpose = action.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 160:
        raise WindowInputError("Every click needs a concise visible-target purpose.")

    result = post_click(
        title_filter,
        x_normalized=action.get("x"),
        y_normalized=action.get("y"),
        screenshot_size=command.get("screenshot_size"),
    )
    result["purpose"] = purpose.strip()
    return [result]
