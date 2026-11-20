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
    max_elements: int = 500,
    max_depth: int = 8,
) -> dict[str, Any]:
    """Return a bounded snapshot of UIA elements for one uniquely matched window."""
    if not isinstance(title_filter, str) or not title_filter.strip():
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
        desktop = Desktop(backend="uia")
        candidates = [
            window for window in desktop.windows()
            if title_filter.casefold() in (window.window_text() or "").casefold()
        ]
    except Exception as exc:
        raise UIADiagnosticError(
            f"Windows UI Automation could not enumerate desktop windows: {type(exc).__name__}: {exc}"
        ) from exc

    if len(candidates) != 1:
        raise UIADiagnosticError(
            f"Expected exactly one UIA window matching {title_filter!r}; found {len(candidates)}."
        )

    root = candidates[0]
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
        root_handle, root_name, root_class = 0, "", ""

    return {
        "window_title": root_name,
        "window_class": root_class,
        "hwnd": root_handle,
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
