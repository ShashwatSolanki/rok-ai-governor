"""Change-aware window monitoring with selective OCR.

This first prototype samples the game window, compares downscaled frames, and
runs OCR only after a meaningful visual change or a periodic heartbeat.
It does not send input to the game.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .account_store import AccountStore
from .ocr import analyse_image, save_observation
from .window_capture import capture_window


def _frame_signature(image_path: str | Path) -> np.ndarray:
    """Create a small, blurred grayscale signature for cheap comparisons."""
    with Image.open(image_path) as image:
        rgb = image.convert("RGB").resize((320, 180), Image.Resampling.BILINEAR)
    frame = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
    return cv2.GaussianBlur(frame, (5, 5), 0)


def _change_ratio(previous: np.ndarray, current: np.ndarray) -> float:
    """Fraction of pixels whose brightness changed beyond a small tolerance."""
    difference = cv2.absdiff(previous, current)
    _, changed = cv2.threshold(difference, 18, 255, cv2.THRESH_BINARY)
    return float(np.count_nonzero(changed)) / float(changed.size)


def run_smart_monitor(
    *,
    title: str = "万国覚醒",
    sample_interval: float = 1.0,
    change_threshold: float = 0.025,
    stable_frames: int = 2,
    heartbeat: float = 30.0,
    duration: int = 0,
    profile: str = "pc",
    database: str = "data/account_history.sqlite3",
    screenshot_dir: str = "screenshots",
    output_dir: str = "observations",
    gemini_enabled: bool = False,
    gemini_interval: float = 180.0,
    gemini_model: str | None = None,
) -> int:
    """Watch a window continuously, invoking OCR selectively.

    A changed frame is retained as a diagnostic screenshot. Unchanged samples
    are deleted immediately. OCR also runs on a heartbeat so small HUD updates
    are eventually picked up even if frame comparison considers them noise.
    """
    if sample_interval < 0.25:
        raise ValueError("--sample-interval must be at least 0.25 seconds.")
    if not 0.0 < change_threshold <= 1.0:
        raise ValueError("--change-threshold must be between 0 and 1.")
    if stable_frames < 1:
        raise ValueError("--stable-frames must be at least 1.")
    if heartbeat < sample_interval:
        raise ValueError("--heartbeat must be >= --sample-interval.")

    store = AccountStore(database)
    started = time.monotonic()
    last_ocr = 0.0
    previous_signature: np.ndarray | None = None
    changed_streak = 0
    observation_number = 0
    last_gemini = 0.0

    print(
        f"Smart monitoring '{title}' every {sample_interval:g}s; "
        f"OCR on change (>{change_threshold:.1%}) or every {heartbeat:g}s. "
        + ("Gemini strategy enabled; " if gemini_enabled else "")
        + "Read-only; Ctrl+C to stop.",
        flush=True,
    )

    try:
        while True:
            loop_started = time.monotonic()
            screenshot = capture_window(title, screenshot_dir)
            signature = _frame_signature(screenshot)

            ratio = (
                _change_ratio(previous_signature, signature)
                if previous_signature is not None else 1.0
            )
            previous_signature = signature

            if ratio >= change_threshold:
                changed_streak += 1
            else:
                changed_streak = 0

            now = time.monotonic()
            # Debounce animated UI effects so a pulsing icon or moving background
            # cannot force OCR on every pair of samples.
            changed = (
                changed_streak >= stable_frames
                and (last_ocr == 0.0 or now - last_ocr >= 10.0)
            )
            heartbeat_due = last_ocr == 0.0 or now - last_ocr >= heartbeat
            should_analyse = changed or heartbeat_due

            if should_analyse:
                observation = analyse_image(screenshot, profile=profile)
                observation["source"] = str(screenshot)
                observation["monitor"] = {
                    "mode": "smart",
                    "change_ratio": round(ratio, 5),
                    "trigger": "visual_change" if changed else "heartbeat",
                }
                observation_id = store.record(observation)
                output = save_observation(observation, output_dir)
                observation_number += 1
                print(
                    f"Observation #{observation_id}: "
                    f"screen={observation.get('screen', {}).get('type', 'unknown')} "
                    f"trigger={observation['monitor']['trigger']} "
                    f"change={ratio:.1%} review={observation.get('review_required')} "
                    f"json={output}",
                    flush=True,
                )
                last_ocr = time.monotonic()
                changed_streak = 0

                if gemini_enabled and (
                    last_gemini == 0.0 or last_ocr - last_gemini >= gemini_interval
                ):
                    try:
                        from .gemini_advisor import advise_with_gemini, save_advice

                        print("Requesting Gemini strategy assessment...", flush=True)
                        advice = advise_with_gemini(
                            screenshot,
                            profile=profile,
                            database=database,
                            ocr_observation=observation,
                            model=gemini_model,
                        )
                        advice_path = save_advice(advice)
                        from .task_store import TaskStore

                        synced = TaskStore(database).ingest(advice)
                        reviews_saved = TaskStore(database).record_reviews(advice)
                        print(
                            f"Gemini recommendations saved: {advice_path}; "
                            f"task queue synced ({synced} recommendation(s)); "
                            f"visual reviews saved ({reviews_saved}).",
                            flush=True,
                        )
                    except Exception as exc:
                        print(
                            f"Gemini advisor unavailable: {type(exc).__name__}: {exc}",
                            flush=True,
                        )
                    finally:
                        last_gemini = time.monotonic()
            else:
                screenshot.unlink(missing_ok=True)

            if duration and time.monotonic() - started >= duration:
                break

            elapsed = time.monotonic() - loop_started
            time.sleep(max(0.0, sample_interval - elapsed))
    except KeyboardInterrupt:
        print("\nSmart monitor stopped.", flush=True)

    print(
        f"Stopped. New OCR observations: {observation_number}; "
        f"total history: {store.observation_count()}.",
        flush=True,
    )
    return observation_number



def run_resilient_monitor(
    *,
    title: str = "万国覚醒",
    sample_interval: float = 2.0,
    change_threshold: float = 0.025,
    stable_frames: int = 2,
    heartbeat: float = 30.0,
    duration: int = 0,
    profile: str = "pc",
    database: str = "data/account_history.sqlite3",
    screenshot_dir: str = "screenshots",
    output_dir: str = "observations",
    gemini_interval: float = 180.0,
    gemini_model: str | None = None,
    retry_delay: float = 5.0,
    max_retry_delay: float = 60.0,
) -> int:
    """Run Gemini-powered, read-only monitoring and recover from transient failures.

    The supervisor restarts a failed monitoring cycle with exponential backoff.
    It never sends input to the game. A finite duration applies to the whole run,
    not to each restart.
    """
    if retry_delay <= 0 or max_retry_delay < retry_delay:
        raise ValueError("Retry delays must be positive and max_retry_delay >= retry_delay.")
    started = time.monotonic()
    failures = 0
    print(
        "RoK Governor supervisor started. Gemini strategy is enabled; "
        "game interaction is disabled. Press Ctrl+C to stop.",
        flush=True,
    )
    while True:
        remaining = max(0, duration - int(time.monotonic() - started)) if duration else 0
        if duration and remaining <= 0:
            print("Configured run duration reached.", flush=True)
            return 0
        try:
            run_smart_monitor(
                title=title,
                sample_interval=sample_interval,
                change_threshold=change_threshold,
                stable_frames=stable_frames,
                heartbeat=heartbeat,
                duration=remaining,
                profile=profile,
                database=database,
                screenshot_dir=screenshot_dir,
                output_dir=output_dir,
                gemini_enabled=True,
                gemini_interval=gemini_interval,
                gemini_model=gemini_model,
            )
            return 0
        except KeyboardInterrupt:
            print("\\nGovernor stopped by user.", flush=True)
            return 0
        except Exception as exc:
            failures += 1
            delay = min(max_retry_delay, retry_delay * (2 ** min(failures - 1, 10)))
            print(
                f"Monitor cycle failed ({type(exc).__name__}: {exc}). "
                f"Restarting in {delay:g}s (consecutive failures: {failures}).",
                flush=True,
            )
            if duration:
                elapsed = time.monotonic() - started
                if elapsed + delay >= duration:
                    print("Run duration expires before the next retry.", flush=True)
                    return 1
            time.sleep(delay)
