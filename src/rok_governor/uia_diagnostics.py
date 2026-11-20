"""Read-only UI Automation inspection for the native Windows game client.

This module inventories accessible UIA elements. It deliberately does not
invoke controls, set focus, move the pointer, or send keyboard/mouse input.
"""
from __future__ import annotations

from typing import Any


class UIADiagnosticError(RuntimeError):
    """The requested window could not be inspected through UI Automation."""


def inspect_uia_tree(
    title_filter: str = "万国覚醒",
    *,
    hwnd: int | None = None,
    max_elements: int = 500,
    max_depth: int = 8,
) -> dict[str, Any]:
    """Return a bounded snapshot of UIA elements for one uniquely matched window."""
    if hwnd is not None and (type(hwnd) is not int or hwnd <= 0):
        raise UIADiagnosticError("hwnd must be a positive integer when provided.")
    if hwnd is None and (not isinstance(title_filter, str) or not title_filter.strip()):
        raise UIADiagnosticError("A non-empty window title filter is required.")
    if type(max_elements) is not int or not 1 <= max_elements <= 2000:
        raise UIADiagnosticError("max_elements must be an integer between 1 and 2000.")
    if type(max_depth) is not int or not 1 <= max_depth <= 20:
        raise UIADiagnosticError("max_depth must be an integer between 1 and 20.")

    try:
        from pywinauto import Desktop
    except ImportError as exc:  # pragma: no cover - Windows optional dependency
        raise UIADiagnosticError(
            "UI Automation diagnostics require pywinauto. "
            "Install project dependencies with: python -m pip install -r requirements.txt"
        ) from exc

    try:
        # Match through Win32 first: UIA's window_text() can lose non-Latin
        # characters (for example, returning "????" for a Japanese title).
        win32_desktop = Desktop(backend="win32")
        if hwnd is not None:
            native_window = win32_desktop.window(handle=hwnd).wrapper_object()
            candidates = [native_window]
        else:
            candidates = [
                window for window in win32_desktop.windows()
                if title_filter.casefold() in (window.window_text() or "").casefold()
            ]
    except Exception as exc:
        raise UIADiagnosticError(
            f"Windows could not enumerate native windows: {type(exc).__name__}: {exc}"
        ) from exc

    if len(candidates) != 1:
        raise UIADiagnosticError(
            f"Expected exactly one native window matching {title_filter!r}; found {len(candidates)}."
        )

    native_window = candidates[0]
    native_handle = int(native_window.handle)
    if hwnd is not None and native_handle != hwnd:
        raise UIADiagnosticError(
            f"Win32 resolved HWND {native_handle}; expected requested HWND {hwnd}."
        )
    native_title = native_window.window_text() or ""

    try:
        # Reacquire the same HWND through UIA instead of matching its possibly
        # corrupted UIA name. This preserves identity without relying on title
        # encoding in the UIA provider.
        uia_desktop = Desktop(backend="uia")
        root = uia_desktop.window(handle=native_handle).wrapper_object()
    except Exception as exc:
        raise UIADiagnosticError(
            f"UI Automation could not attach to native window HWND {native_handle}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    elements: list[dict[str, Any]] = []
    truncated = False

    def visit(wrapper: Any, depth: int) -> None:
        nonlocal truncated
        if len(elements) >= max_elements:
            truncated = True
            return
        try:
            info = wrapper.element_info
            rect = info.rectangle
            elements.append({
                "depth": depth,
                "name": (info.name or "")[:240],
                "control_type": info.control_type or "",
                "automation_id": (info.automation_id or "")[:160],
                "class_name": (info.class_name or "")[:160],
                "handle": int(info.handle or 0),
                "rectangle": [rect.left, rect.top, rect.right, rect.bottom],
                "enabled": bool(info.enabled),
                "offscreen": bool(info.is_offscreen),
            })
        except Exception as exc:
            elements.append({
                "depth": depth,
                "inspection_error": f"{type(exc).__name__}: {exc}",
            })
            return

        if depth >= max_depth or len(elements) >= max_elements:
            if depth < max_depth and len(elements) >= max_elements:
                truncated = True
            return
        try:
            children = wrapper.children()
        except Exception:
            return
        for child in children:
            if len(elements) >= max_elements:
                truncated = True
                break
            visit(child, depth + 1)

    visit(root, 0)
    try:
        root_info = root.element_info
        root_handle = int(root_info.handle or 0)
        root_name = root.window_text()
        root_class = root_info.class_name or ""
    except Exception:
        root_handle, root_name, root_class = native_handle, "", ""

    if root_handle and root_handle != native_handle:
        raise UIADiagnosticError(
            f"UI Automation attached to unexpected HWND {root_handle}; expected {native_handle}."
        )

    return {
        "window_title": native_title,
        "requested_hwnd": hwnd,
        "uia_name": root_name,
        "window_class": root_class,
        "hwnd": native_handle,
        "uia_hwnd": root_handle,
        "backend": "uia",
        "element_count": len(elements),
        "truncated": truncated,
        "elements": elements,
        "input_sent": False,
        "focus_changed": False,
        "note": (
            "Read-only UI Automation inventory. A sparse tree is common for "
            "canvas-rendered Unity games and does not imply that visual controls are absent."
        ),
    }
