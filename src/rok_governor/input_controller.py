"""Window-targeted background mouse input for the RoK governor.

Only one bounded left-click per fresh visual assessment is supported.
Background mode posts window messages without focus. Foreground mode is an
explicit opt-in that focuses the game and moves the physical mouse because
some game clients ignore background messages.
"""
from __future__ import annotations

import ctypes
import math
import time
from ctypes import wintypes
from typing import Any


class WindowInputError(RuntimeError):
    """A safe, window-scoped input could not be delivered."""


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", _InputUnion)]


def _send_mouse_input(flags: int, position: tuple[int, int] | None = None) -> bool:
    """Send one mouse event using the Win32 SendInput API."""
    x, y = position or (0, 0)
    event = _Input(
        type=0,  # INPUT_MOUSE
        mi=_MouseInput(x, y, 0, flags, 0, 0),
    )
    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    send_input.restype = wintypes.UINT
    return send_input(1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1


def post_click(
    title_filter: str,
    *,
    x_normalized: int,
    y_normalized: int,
    screenshot_size: tuple[int, int] | None = None,
    input_mode: str = "background",
    hold_seconds: float = 0.08,
) -> dict[str, Any]:
    """Send one left-click to normalized coordinates in a unique game window.

    Coordinates use a 0..1000 range over the captured window rectangle.
    Foreground input must be explicitly selected by the caller.
    """
    if type(x_normalized) is not int or type(y_normalized) is not int:
        raise WindowInputError("Click coordinates must be integer normalized values.")
    if not 0 <= x_normalized <= 1000 or not 0 <= y_normalized <= 1000:
        raise WindowInputError("Click rejected: normalized coordinates must be within 0..1000.")
    if screenshot_size is not None:
        if (not isinstance(screenshot_size, tuple) or len(screenshot_size) != 2
                or any(type(value) is not int or value <= 0 for value in screenshot_size)):
            raise WindowInputError(f"Invalid screenshot geometry: {screenshot_size!r}")
    if input_mode not in {"background", "foreground"}:
        raise WindowInputError("Input mode must be 'background' or 'foreground'.")
    if not math.isfinite(hold_seconds) or not 0.04 <= hold_seconds <= 0.2:
        raise WindowInputError("Click hold duration is outside the permitted range.")

    try:
        import win32api
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

    if input_mode == "background":
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
        delivery = "posted_to_window_not_foregrounded"
    else:
        virtual_left = win32api.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        virtual_top = win32api.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        virtual_width = win32api.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        virtual_height = win32api.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if not (
            virtual_left <= screen_x < virtual_left + virtual_width
            and virtual_top <= screen_y < virtual_top + virtual_height
        ):
            raise WindowInputError(
                f"Calibrated click point ({screen_x}, {screen_y}) is outside the virtual desktop."
            )
        absolute_x = round((screen_x - virtual_left) * 65535 / max(1, virtual_width - 1))
        absolute_y = round((screen_y - virtual_top) * 65535 / max(1, virtual_height - 1))
        move_flags = (
            win32con.MOUSEEVENTF_MOVE
            | win32con.MOUSEEVENTF_ABSOLUTE
            | win32con.MOUSEEVENTF_VIRTUALDESK
        )
        button_down = False
        try:
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.1)
            if win32gui.GetForegroundWindow() != hwnd:
                raise WindowInputError(
                    "Could not focus the game window; foreground input was not sent."
                )
            if not _send_mouse_input(move_flags, (absolute_x, absolute_y)):
                raise WindowInputError(
                    "Windows rejected the calibrated foreground pointer movement. "
                    "The click was not sent."
                )
            actual_cursor = tuple(win32api.GetCursorPos())
            if actual_cursor != (screen_x, screen_y):
                raise WindowInputError(
                    "Windows did not move the pointer to the calibrated click target; "
                    f"expected {(screen_x, screen_y)}, got {actual_cursor}. Click withheld."
                )
            button_down = True
            if not _send_mouse_input(win32con.MOUSEEVENTF_LEFTDOWN):
                raise WindowInputError("Windows rejected the foreground mouse-down event.")
            time.sleep(hold_seconds)
        except WindowInputError:
            raise
        except Exception as exc:
            raise WindowInputError(f"Windows rejected the foreground click: {exc}") from exc
        finally:
            if button_down:
                try:
                    if not _send_mouse_input(win32con.MOUSEEVENTF_LEFTUP):
                        raise WindowInputError("Windows rejected the foreground mouse-up event.")
                except Exception as exc:
                    raise WindowInputError(f"Could not release the foreground mouse button: {exc}") from exc
        delivery = "sent_to_foreground_window"

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
        "input_mode": input_mode,
        "delivery": delivery,
    }


def execute_safe_actions(
    title_filter: str,
    command: dict[str, Any],
    *,
    input_mode: str = "background",
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
        input_mode=input_mode,
    )
    result["purpose"] = purpose.strip()
    return [result]


def diagnose_input_geometry(
    title_filter: str = "万国覚醒",
    *,
    x_normalized: int = 500,
    y_normalized: int = 500,
    screenshot_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Inspect input geometry without focusing a window or sending any input."""
    if type(x_normalized) is not int or type(y_normalized) is not int:
        raise WindowInputError("Diagnostic coordinates must be integers.")
    if not 0 <= x_normalized <= 1000 or not 0 <= y_normalized <= 1000:
        raise WindowInputError("Diagnostic coordinates must be within 0..1000.")
    if screenshot_size is not None and (
        not isinstance(screenshot_size, tuple)
        or len(screenshot_size) != 2
        or any(type(value) is not int or value <= 0 for value in screenshot_size)
    ):
        raise WindowInputError(f"Invalid screenshot geometry: {screenshot_size!r}")

    try:
        import win32api
        import win32gui
        from .window_capture import find_child_windows, find_windows
    except ImportError as exc:  # pragma: no cover - Windows-only runtime
        raise WindowInputError("Input diagnostics require pywin32 on Windows.") from exc

    candidates = find_windows(title_filter)
    if len(candidates) != 1:
        raise WindowInputError(
            f"Expected exactly one matching game window; found {len(candidates)}. "
            "Use a more specific --title."
        )
    target = candidates[0]
    hwnd = target["hwnd"]
    left, top, right, bottom = target["rect"]
    window_width, window_height = right - left, bottom - top
    screenshot_width, screenshot_height = screenshot_size or (window_width, window_height)
    screenshot_x = min(screenshot_width - 1, round(x_normalized * screenshot_width / 1000))
    screenshot_y = min(screenshot_height - 1, round(y_normalized * screenshot_height / 1000))
    window_x = round(screenshot_x * window_width / screenshot_width)
    window_y = round(screenshot_y * window_height / screenshot_height)
    screen_x, screen_y = left + window_x, top + window_y
    client_x, client_y = win32gui.ScreenToClient(hwnd, (screen_x, screen_y))
    client_rect = win32gui.GetClientRect(hwnd)
    virtual = {
        "left": win32api.GetSystemMetrics(76),
        "top": win32api.GetSystemMetrics(77),
        "width": win32api.GetSystemMetrics(78),
        "height": win32api.GetSystemMetrics(79),
    }
    return {
        "window_title": target["title"],
        "hwnd": hwnd,
        "minimized": target["minimized"],
        "window_rect": list(target["rect"]),
        "window_size": [window_width, window_height],
        "client_rect": list(client_rect),
        "client_size": [client_rect[2] - client_rect[0], client_rect[3] - client_rect[1]],
        "screenshot_size": [screenshot_width, screenshot_height],
        "normalized_target": [x_normalized, y_normalized],
        "screenshot_pixel": [screenshot_x, screenshot_y],
        "screen_target": [screen_x, screen_y],
        "client_target": [client_x, client_y],
        "target_inside_client": (
            0 <= client_x < client_rect[2] - client_rect[0]
            and 0 <= client_y < client_rect[3] - client_rect[1]
        ),
        "virtual_desktop": virtual,
        "cursor_position": list(win32api.GetCursorPos()),
        "foreground_hwnd": win32gui.GetForegroundWindow(),
        "child_windows": find_child_windows(hwnd),
        "input_sent": False,
        "focus_changed": False,
    }
