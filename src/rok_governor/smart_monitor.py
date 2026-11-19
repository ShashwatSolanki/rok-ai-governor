"""Change-aware game-window monitoring with Gemini strategy and bounded input."""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
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


def _write_runtime_status(
    database: str | Path,
    *,
    state: str,
    title: str,
    started_at: str | None = None,
    last_capture_at: str | None = None,
    last_ocr_at: str | None = None,
    last_gemini_at: str | None = None,
    last_error: str | None = None,
    consecutive_failures: int = 0,
    observation_count: int = 0,
    automation_enabled: bool = False,
) -> Path:
    """Atomically publish a small health snapshot beside the local database."""
    path = Path(database).with_name("runtime_status.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "mode": "background_automation" if automation_enabled else "observe_only",
        "window_title": title,
        "started_at": started_at,
        "last_capture_at": last_capture_at,
        "last_ocr_at": last_ocr_at,
        "last_gemini_at": last_gemini_at,
        "last_error": last_error,
        "consecutive_failures": consecutive_failures,
        "observations_this_session": observation_count,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def _prune_screenshots(directory: str | Path, keep: int = 200) -> None:
    """Bound diagnostic screenshot storage while preserving the newest captures."""
    files = sorted(
        Path(directory).glob("window_*.png"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old_file in files[keep:]:
        old_file.unlink(missing_ok=True)


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
    automation_enabled: bool = True,
) -> int:
    """Observe the game continuously; OCR is supporting evidence, Gemini is independent.

    Gemini runs on its own schedule against the freshest captured frame, even
    when local OCR fails. Diagnostic screenshots are retained only when used
    for a saved observation or Gemini assessment.
    """
    if sample_interval < 0.25:
        raise ValueError("--sample-interval must be at least 0.25 seconds.")
    if not 0.0 < change_threshold <= 1.0:
        raise ValueError("--change-threshold must be between 0 and 1.")
    if stable_frames < 1:
        raise ValueError("--stable-frames must be at least 1.")
    if heartbeat < sample_interval:
        raise ValueError("--heartbeat must be >= --sample-interval.")
    if gemini_interval <= 0:
        raise ValueError("--gemini-interval must be positive.")

    store = AccountStore(database)
    started = time.monotonic()
    last_ocr = 0.0
    previous_signature: np.ndarray | None = None
    changed_streak = 0
    observation_number = 0
    session_started_at = datetime.now(timezone.utc).isoformat()
    last_capture_at: str | None = None
    last_ocr_at: str | None = None
    last_gemini_at: str | None = None
    runtime_error: str | None = None
    latest_observation: dict[str, Any] | None = None
    next_gemini_at = 0.0
    gemini_failures = 0
    gemini_retry_delay = 15.0
    gemini_max_retry_delay = 900.0

    print(
        f"Smart monitoring '{title}' every {sample_interval:g}s; "
        f"OCR on change (>{change_threshold:.1%}) or every {heartbeat:g}s. "
        + ("Gemini strategy enabled; " if gemini_enabled else "")
        + ("Background input enabled; " if automation_enabled and gemini_enabled else "Observe-only; ")
        + "Ctrl+C to stop.",
        flush=True,
    )

    _write_runtime_status(database, state="starting", title=title, started_at=session_started_at, automation_enabled=automation_enabled and gemini_enabled)
    try:
        while True:
            loop_started = time.monotonic()
            screenshot = capture_window(title, screenshot_dir)
            last_capture_at = datetime.now(timezone.utc).isoformat()
            _write_runtime_status(database, state="running", title=title, started_at=session_started_at, last_capture_at=last_capture_at, last_ocr_at=last_ocr_at, last_gemini_at=last_gemini_at, last_error=runtime_error, observation_count=observation_number, automation_enabled=automation_enabled and gemini_enabled)
            keep_screenshot = False
            try:
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
                changed = (
                    changed_streak >= stable_frames
                    and (last_ocr == 0.0 or now - last_ocr >= 10.0)
                )
                heartbeat_due = last_ocr == 0.0 or now - last_ocr >= heartbeat
                should_analyse = changed or heartbeat_due

                if should_analyse:
                    try:
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
                        latest_observation = observation
                        last_ocr_at = datetime.now(timezone.utc).isoformat()
                        runtime_error = None
                        keep_screenshot = True
                        print(
                            f"Observation #{observation_id}: "
                            f"screen={observation.get('screen', {}).get('type', 'unknown')} "
                            f"trigger={observation['monitor']['trigger']} "
                            f"change={ratio:.1%} review={observation.get('review_required')} "
                            f"json={output}",
                            flush=True,
                        )
                    except Exception as exc:
                        runtime_error = f"Local OCR {type(exc).__name__}: {exc}"
                        print(
                            f"Local OCR unavailable ({type(exc).__name__}: {exc}); "
                            "Gemini will continue using screenshot pixels.",
                            flush=True,
                        )
                    finally:
                        last_ocr = time.monotonic()
                        changed_streak = 0

                # Gemini's schedule is independent of OCR success and OCR heartbeat.
                if gemini_enabled and time.monotonic() >= next_gemini_at:
                    try:
                        from .gemini_advisor import advise_with_gemini, save_advice

                        from .execution_store import ExecutionStore
                        pending_verification_before_cycle = bool(
                            ExecutionStore(database).pending_verification()
                        )
                        print("Requesting Gemini strategy assessment...", flush=True)
                        advice = advise_with_gemini(
                            screenshot,
                            profile=profile,
                            database=database,
                            ocr_observation=latest_observation,
                            model=gemini_model,
                        )
                        advice_path = save_advice(advice)
                        from .task_store import TaskStore

                        task_store = TaskStore(database)
                        synced = task_store.ingest(advice)
                        reviews_saved = task_store.record_reviews(advice)
                        from .command_store import CommandStore

                        commands_saved = CommandStore(database).ingest(advice)

                        # Execute at most one idempotent, low-risk click command per
                        # Gemini cycle when unattended input is enabled. All other proposals remain queued.
                        from .execution_store import ExecutionStore
                        from .input_controller import execute_safe_actions, WindowInputError

                        execution_store = ExecutionStore(database)
                        execution_reviews_saved = execution_store.record_reviews(advice)
                        candidate_commands = (
                            (advice.get("advice") or {}).get("commands") or []
                        ) if automation_enabled and not pending_verification_before_cycle else []
                        if automation_enabled and pending_verification_before_cycle:
                            print(
                                "No new input this cycle: Gemini is assessing the previous click first.",
                                flush=True,
                            )
                        for proposed in candidate_commands:
                            if not proposed.get("input_actions"):
                                continue
                            if not proposed.get("preconditions") or not proposed.get("verification"):
                                continue

                            # Gemini may take several seconds to answer. Re-capture
                            # immediately before dispatch and reject stale coordinates
                            # if the game screen changed materially in the meantime.
                            fresh_frame = None
                            try:
                                fresh_frame = capture_window(title, screenshot_dir)
                                freshness_change = _change_ratio(
                                    signature, _frame_signature(fresh_frame)
                                )
                                if freshness_change > 0.02:
                                    print(
                                        f"Automation skipped '{proposed.get('title')}': "
                                        f"screen changed {freshness_change:.1%} while Gemini was thinking.",
                                        flush=True,
                                    )
                                    continue
                            except Exception as preflight_exc:
                                print(
                                    f"Automation preflight failed; no input sent: "
                                    f"{type(preflight_exc).__name__}: {preflight_exc}",
                                    flush=True,
                                )
                                break
                            finally:
                                if fresh_frame is not None:
                                    fresh_frame.unlink(missing_ok=True)

                            execution_command = dict(proposed)
                            execution_command["screen_fingerprint"] = hashlib.sha256(
                                signature.tobytes()
                            ).hexdigest()
                            if not execution_store.claim(execution_command):
                                continue
                            try:
                                sent = execute_safe_actions(title, proposed)
                                execution_store.finish(
                                    execution_command, "posted",
                                    {"input_results": sent, "verification_criteria": proposed.get("verification"), "verification_status": "awaiting_post_action_capture"},
                                )
                                time.sleep(1.2)
                                try:
                                    verification_frame = capture_window(title, screenshot_dir)
                                    execution_store.finish(
                                        execution_command, "posted",
                                        {"input_results": sent, "verification_criteria": proposed.get("verification"),
                                         "post_action_screenshot": str(verification_frame),
                                         "verification_status": "awaiting_next_visual_assessment"},
                                    )
                                    keep_screenshot = True
                                    print(
                                        f"One background click posted for '{proposed.get('title')}'; "
                                        f"post-action frame saved to {verification_frame}. "
                                        "Game acceptance is not yet verified.",
                                        flush=True,
                                    )
                                except Exception as capture_exc:
                                    execution_store.finish(
                                        execution_command, "posted",
                                        {"input_results": sent, "verification_status": "capture_failed",
                                         "capture_error": str(capture_exc)},
                                    )
                                    print(
                                        f"Background input was posted for '{proposed.get('title')}', "
                                        f"but post-action capture failed: {capture_exc}",
                                        flush=True,
                                    )
                                break
                            except WindowInputError as exc:
                                execution_store.finish(execution_command, "blocked", {"reason": str(exc)})
                                print(f"Automation safely skipped '{proposed.get('title')}': {exc}", flush=True)
                            except Exception as exc:
                                execution_store.finish(
                                    execution_command, "error",
                                    {"error_type": type(exc).__name__, "reason": str(exc)},
                                )
                                print(
                                    f"Automation failed for '{proposed.get('title')}': "
                                    f"{type(exc).__name__}: {exc}",
                                    flush=True,
                                )
                                break

                        keep_screenshot = True
                        print(
                            f"Gemini recommendations saved: {advice_path}; "
                            f"task queue synced ({synced} recommendation(s)); "
                            f"visual reviews saved ({reviews_saved}); "
                            f"structured commands queued ({commands_saved}); "
                            f"previous actions visually reviewed ({execution_reviews_saved}).",
                            flush=True,
                        )
                        gemini_failures = 0
                        last_gemini_at = datetime.now(timezone.utc).isoformat()
                        runtime_error = None
                        next_gemini_at = time.monotonic() + gemini_interval
                    except Exception as exc:
                        gemini_failures += 1
                        delay = min(
                            gemini_max_retry_delay,
                            gemini_retry_delay * (2 ** min(gemini_failures - 1, 6)),
                        )
                        next_gemini_at = time.monotonic() + delay
                        runtime_error = f"Gemini {type(exc).__name__}: {exc}"
                        _write_runtime_status(database, state="degraded", title=title, started_at=session_started_at, last_capture_at=last_capture_at, last_ocr_at=last_ocr_at, last_gemini_at=last_gemini_at, last_error=runtime_error, consecutive_failures=gemini_failures, observation_count=observation_number, automation_enabled=automation_enabled and gemini_enabled)
                        print(
                            f"Gemini advisor unavailable ({type(exc).__name__}: {exc}); "
                            f"retrying in {delay:g}s (failures: {gemini_failures}).",
                            flush=True,
                        )
            finally:
                if not keep_screenshot:
                    screenshot.unlink(missing_ok=True)
            if keep_screenshot:
                _prune_screenshots(screenshot_dir)
            _write_runtime_status(database, state="running" if runtime_error is None else "degraded", title=title, started_at=session_started_at, last_capture_at=last_capture_at, last_ocr_at=last_ocr_at, last_gemini_at=last_gemini_at, last_error=runtime_error, consecutive_failures=gemini_failures, observation_count=observation_number, automation_enabled=automation_enabled and gemini_enabled)

            if duration and time.monotonic() - started >= duration:
                break

            elapsed = time.monotonic() - loop_started
            time.sleep(max(0.0, sample_interval - elapsed))
    except KeyboardInterrupt:
        print("\nSmart monitor stopped.", flush=True)

    _write_runtime_status(database, state="stopped", title=title, started_at=session_started_at, last_capture_at=last_capture_at, last_ocr_at=last_ocr_at, last_gemini_at=last_gemini_at, last_error=runtime_error, consecutive_failures=gemini_failures, observation_count=observation_number, automation_enabled=automation_enabled and gemini_enabled)
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
    automation_enabled: bool = True,
) -> int:
    """Run the Gemini governor and recover from transient failures.

    The supervisor restarts a failed monitoring cycle with exponential backoff.
    A finite duration applies to the whole run,
    not to each restart.
    """
    if retry_delay <= 0 or max_retry_delay < retry_delay:
        raise ValueError("Retry delays must be positive and max_retry_delay >= retry_delay.")
    from dotenv import load_dotenv

    load_dotenv()
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is missing. Add it to .env before starting the Gemini governor.")
    started = time.monotonic()
    failures = 0
    print(
        "RoK Governor supervisor started. Gemini strategy is enabled; "
        + ("bounded background input is enabled. " if automation_enabled else "observe-only mode is enabled. ")
        + "Press Ctrl+C to stop.",
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
                automation_enabled=automation_enabled,
            )
            return 0
        except KeyboardInterrupt:
            previous_health_path = Path(database).with_name("runtime_status.json")
            try:
                previous_health = json.loads(previous_health_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_health = {}
            _write_runtime_status(
                database, state="stopped", title=title,
                started_at=previous_health.get("started_at"),
                last_capture_at=previous_health.get("last_capture_at"),
                last_ocr_at=previous_health.get("last_ocr_at"),
                last_gemini_at=previous_health.get("last_gemini_at"),
                last_error=previous_health.get("last_error"),
                consecutive_failures=failures,
                observation_count=previous_health.get("observations_this_session", 0),
                automation_enabled=automation_enabled,
            )
            print("\\nGovernor stopped by user.", flush=True)
            return 0
        except Exception as exc:
            failures += 1
            delay = min(max_retry_delay, retry_delay * (2 ** min(failures - 1, 10)))
            previous_health_path = Path(database).with_name("runtime_status.json")
            try:
                previous_health = json.loads(previous_health_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous_health = {}
            _write_runtime_status(
                database, state="restarting", title=title,
                started_at=previous_health.get("started_at"),
                last_capture_at=previous_health.get("last_capture_at"),
                last_ocr_at=previous_health.get("last_ocr_at"),
                last_gemini_at=previous_health.get("last_gemini_at"),
                last_error=f"Monitor {type(exc).__name__}: {exc}",
                consecutive_failures=failures,
                observation_count=previous_health.get("observations_this_session", 0),
                automation_enabled=automation_enabled,
            )
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
