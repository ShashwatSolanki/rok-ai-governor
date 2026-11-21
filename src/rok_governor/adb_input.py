import io
import subprocess

from PIL import Image


def adb_tap(x: int, y: int) -> None:
    """Perform an ADB tap at the given absolute screen coordinates.

    Args:
        x: Horizontal pixel coordinate on the screen.
        y: Vertical pixel coordinate on the screen.
    """
    # Use the `adb` executable that should be on PATH.
    # The command is equivalent to: adb shell input tap <x> <y>
    subprocess.run(["adb", "shell", "input", "tap", str(x), str(y)], check=True)


def adb_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> None:
    """Perform an ADB swipe/drag gesture between two coordinates.

    Args:
        x1, y1: Start pixel coordinates on the Android screen.
        x2, y2: End pixel coordinates on the Android screen.
        duration_ms: Duration of the swipe motion in milliseconds.
    """
    subprocess.run(
        ["adb", "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        check=True,
    )


def adb_screencap() -> Image.Image:
    """Capture the Android emulator screen via ADB and return a PIL Image.

    This captures the *actual* game display inside the emulator, regardless of
    which Windows application is in the foreground.  It is the most reliable
    capture method when ``--input-mode adb`` is used.

    Raises:
        RuntimeError: If the ADB ``screencap`` command fails.
    """
    try:
        result = subprocess.run(
            ["adb", "exec-out", "screencap", "-p"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ADB screencap failed (exit {exc.returncode}): {exc.stderr.decode(errors='replace')}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "The 'adb' executable was not found on PATH. "
            "Install the Android SDK platform-tools and add them to PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ADB screencap timed out after 10 seconds.") from exc

    if not result.stdout:
        raise RuntimeError("ADB screencap returned empty output.")

    return Image.open(io.BytesIO(result.stdout)).convert("RGB")
