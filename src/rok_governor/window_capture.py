"""Read-only capture of a specific Windows application window.

PrintWindow asks the target application to render into an off-screen bitmap.
Some hardware-accelerated applications (including some emulator/game builds)
do not support this reliably. In that case this module fails loudly rather than
silently capturing the wrong part of the desktop.
"""

from datetime import datetime
from pathlib import Path

from PIL import Image

try:
    import win32gui
    import win32ui
except ImportError as exc:  # pragma: no cover - platform-specific dependency
    raise RuntimeError(
        "Window capture requires pywin32. Install dependencies with "
        "'python -m pip install -r requirements.txt'."
    ) from exc


def find_windows(title_filter: str = "") -> list[dict]:
    """Return visible top-level windows whose titles contain title_filter."""
    matches: list[dict] = []
    needle = title_filter.casefold()

    def visit(hwnd: int, _extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title or (needle and needle not in title.casefold()):
            return
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except win32gui.error:
            return
        if right <= left or bottom <= top:
            return
        matches.append({
            "hwnd": hwnd,
            "title": title,
            "rect": (left, top, right, bottom),
            "minimized": bool(win32gui.IsIconic(hwnd)),
        })

    win32gui.EnumWindows(visit, None)
    return matches


def capture_window(
    title_filter: str = "BlueStacks",
    output_dir: str | Path = "screenshots",
) -> Path:
    """Capture a matching window using PrintWindow, without desktop input."""
    candidates = find_windows(title_filter)
    if not candidates:
        raise RuntimeError(
            f"No visible window title containing {title_filter!r} was found. "
            "Run 'python -m rok_governor windows' to list window titles."
        )

    # Prefer the largest matching window, since emulators can expose helper windows.
    target = max(
        candidates,
        key=lambda item: (item["rect"][2] - item["rect"][0])
        * (item["rect"][3] - item["rect"][1]),
    )
    hwnd = target["hwnd"]
    left, top, right, bottom = target["rect"]
    width, height = right - left, bottom - top

    window_dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    memory_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    memory_dc.SelectObject(bitmap)

    try:
        # PW_RENDERFULLCONTENT asks DWM/the app to render the full window.
        result = win32gui.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 0x00000002)
        if result != 1:
            raise RuntimeError(
                "Windows could not render this window off-screen. "
                "Try leaving BlueStacks visible behind your other apps."
            )

        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits,
            "raw", "BGRX", 0, 1
        ).transpose(Image.Transpose.FLIP_TOP_BOTTOM)

        # Reject obvious blank/black captures; these are common with GPU surfaces.
        extrema = image.convert("L").getextrema()
        if extrema == (0, 0) or (extrema[1] <= 3 and extrema[0] == 0):
            raise RuntimeError(
                "The window returned a blank/black frame. This game renderer "
                "may not support minimised/off-screen capture. Keep BlueStacks "
                "visible behind other windows and try again."
            )

        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = destination / f"window_{timestamp}.png"
        image.save(output_path, format="PNG")
        return output_path
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)
