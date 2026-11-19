"""Window-targeted background mouse input for the RoK governor.

Only bounded left-click actions are supported. Inputs are posted to the selected
window and do not move the user's physical mouse or activate the game window.
Some GPU/game clients ignore background window messages; callers must treat
that as an unsupported input path rather than falling back to desktop clicks.
"""
from __future__ import annotations

import time
from typing import Any


class WindowInputError(RuntimeError):
    """A safe, window-scoped input could not be delivered."""


def post_click(
    title_filter: str,
    *,
    x_normalized: int,
    y_normalized: int,
    hold_seconds: float = 0.08,
) -> dict[str, Any]:
    """Post one left-click to normalized coordinates in a unique game window.

    Coordinates use a 0..1000 range over the full captured window rectangle.
    This method never foregrounds the target and never uses desktop-wide input.
    """
    if not 30 <= x_normalized <= 970 or not 30 <= y_normalized <= 970:
        raise WindowInputError("Click rejected: coordinates too close to window edges.")
    if not 0.04 <= hold_seconds <= 0.2:
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
    left, top, right, bottom = target["rect"]
    screen_x = left + round((right - left) * x_normalized / 1000)
    screen_y = top + round((bottom - top) * y_normalized / 1000)
    client_x, client_y = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
    client_rect = win32gui.GetClientRect(hwnd)
    if not (0 <= client_x < client_rect[2] and 0 <= client_y < client_rect[3]):
        raise WindowInputError("Mapped click falls outside the game client area.")

    lparam = ((client_y & 0xFFFF) << 16) | (client_x & 0xFFFF)
    try:
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        time.sleep(hold_seconds)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
    except Exception as exc:
        raise WindowInputError(f"Windows rejected the background click: {exc}") from exc

    return {
        "window_title": target["title"],
        "hwnd": hwnd,
        "x_normalized": x_normalized,
        "y_normalized": y_normalized,
        "client_x": client_x,
        "client_y": client_y,
        "sent_at": time.time(),
        "delivery": "posted_to_window_not_foregrounded",
    }


def execute_safe_actions(
    title_filter: str,
    command: dict[str, Any],
    *,
    minimum_confidence: float = 0.88,
) -> list[dict[str, Any]]:
    """Execute a small, allow-listed set of low-risk clicks from Gemini output."""
    allowed_command_types = {"inspect_screen", "open_menu", "select_tab", "start_gathering"}
    if command.get("action_type") not in allowed_command_types:
        raise WindowInputError("Command type is not enabled for unattended execution.")
    if str(command.get("risk", "unknown")).casefold() != "low":
        raise WindowInputError("Only Gemini-labelled low-risk commands may run unattended.")
    confidence = command.get("confidence")
    if not isinstance(confidence, (int, float)) or confidence < minimum_confidence:
        raise WindowInputError("Command confidence is below the unattended execution threshold.")

    actions = command.get("input_actions")
    if not isinstance(actions, list) or not actions or len(actions) > 3:
        raise WindowInputError("Command must contain 1 to 3 explicit click actions.")
    results = []
    for action in actions:
        if not isinstance(action, dict) or action.get("type") != "click":
            raise WindowInputError("Only explicit click actions are supported.")
        x = action.get("x")
        y = action.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            raise WindowInputError("Click coordinates must be integer normalized values.")
        results.append(post_click(title_filter, x_normalized=x, y_normalized=y))
        time.sleep(0.35)
    return results
