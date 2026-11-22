import io
import subprocess
import time

from PIL import Image


_ADB_DISCONNECTED_MARKERS = (
    "device offline",
    "device not found",
    "no devices/emulators found",
)


def _run_adb(command: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Run an ADB command, reconnect offline emulator transports once, retry."""
    try:
        return subprocess.run(command, capture_output=True, check=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or b"").decode(errors="replace").casefold()
        if not any(marker in details for marker in _ADB_DISCONNECTED_MARKERS):
            raise

        try:
            subprocess.run(
                ["adb", "reconnect", "offline"],
                capture_output=True,
                check=False,
                timeout=5,
            )
            time.sleep(1)
            return subprocess.run(command, capture_output=True, check=True, timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as retry_exc:
            retry_details = getattr(retry_exc, "stderr", b"") or b""
            if isinstance(retry_details, bytes):
                retry_details = retry_details.decode(errors="replace")
            raise RuntimeError(
                "ADB emulator is disconnected; one reconnect attempt failed. "
                "Confirm LDPlayer is running and `adb devices -l` shows its device as online. "
                f"Details: {str(retry_details).strip() or retry_exc}"
            ) from retry_exc


def adb_tap(x: int, y: int) -> None:
    """Perform an ADB tap at the given absolute screen coordinates.

    Args:
        x: Horizontal pixel coordinate on the screen.
        y: Vertical pixel coordinate on the screen.
    """
    # Use the `adb` executable that should be on PATH.
    # The command is equivalent to: adb shell input tap <x> <y>
    _run_adb(["adb", "shell", "input", "tap", str(x), str(y)], timeout=15)


def adb_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> None:
    """Perform an ADB swipe/drag gesture between two coordinates.

    Args:
        x1, y1: Start pixel coordinates on the Android screen.
        x2, y2: End pixel coordinates on the Android screen.
        duration_ms: Duration of the swipe motion in milliseconds.
    """
    _run_adb(
        ["adb", "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
        timeout=20,
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
        result = _run_adb(["adb", "exec-out", "screencap", "-p"], timeout=10)
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
