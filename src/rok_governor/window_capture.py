""""Read-only capture of a specific Windows application window.

PrintWindow asks the target application to render into an off-screen bitmap.
Some hardware-accelerated applications (including some emulator/game builds)
do not support this reliably. In that case this module fails loudly rather than
silently capturing the wrong part of the desktop.

The process opts into per-monitor DPI awareness before querying window geometry.
Without this, Windows can virtualize coordinates and dimensions (for example,
1920x1080 at 125% scaling may appear as 1536x864).
"""

import ctypes
from datetime import datetime
from pathlib import Path


def _enable_dpi_awareness() -> str:
    """Enable physical-pixel window geometry where Windows supports it."""
    user32 = ctypes.windll.user32

    # Windows 10+: per-monitor v2 is the most accurate mode. This must be set
    # before the process creates/queries any windows.
    try:
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass

    # Windows 8.1 fallback.
    try:
        shcore = ctypes.windll.shcore
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        if set_awareness(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
            return "per-monitor"
    except (AttributeError, OSError):
        pass

    # Older Windows fallback. It is system-DPI-aware rather than per-monitor.
    try:
        set_aware = user32.SetProcessDPIAware
        set_aware.restype = ctypes.c_bool
        if set_aware():
            return "system"
    except (AttributeError, OSError):
        pass

    # A failure can simply mean another component or application manifest
    # already established the process DPI mode.
    return "unchanged"


DPI_AWARENESS = _enable_dpi_awareness()

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
    """Capture a matching window using Win32 PrintWindow, without desktop input."""
    candidates = find_windows(title_filter)
    if not candidates:
        raise RuntimeError(
            f"No visible window title containing {title_filter!r} was found. "
            "Run 'python -m rok_governor windows' to list window titles."
        )

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
        # PrintWindow is exported by user32.dll; pywin32 does not expose it
        # as win32gui.PrintWindow in all builds.
        print_window = ctypes.windll.user32.PrintWindow
        print_window.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        print_window.restype = ctypes.c_bool
        result = print_window(
            ctypes.c_void_p(hwnd),
            ctypes.c_void_p(memory_dc.GetSafeHdc()),
            ctypes.c_uint(0x00000002),  # PW_RENDERFULLCONTENT
        )
        if not result:
            raise RuntimeError(
                "Windows could not render this window off-screen. "
                "Try leaving the game window visible behind your other apps."
            )

        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        # GetBitmapBits is already top-down for this capture path; flipping it
        # vertically would turn the game image upside down.
        image = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]), bits,
            "raw", "BGRX", 0, 1
        )

        extrema = image.convert("L").getextrema()
        if extrema[1] <= 3:
            raise RuntimeError(
                "The window returned a blank/black frame. This game renderer "
                "may not support minimised/off-screen capture. Keep the game "
                "visible behind other windows and try again."
            )

        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_path = destination / f"window_{timestamp}.png"
        image.save(output_path, format="PNG")
        return output_path
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)
