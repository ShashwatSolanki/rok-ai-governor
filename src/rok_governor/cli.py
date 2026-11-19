"""Command-line interface for screenshot capture and OCR."""

import argparse
import json
import time
from pathlib import Path

from .capture import capture_primary_monitor
from .ocr import analyse_image, save_observation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rok-governor",
        description="Read-only screenshot capture and OCR for RoK.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture_parser = subparsers.add_parser(
        "capture", help="Capture the primary desktop monitor."
    )
    capture_parser.add_argument(
        "--output-dir", default="screenshots", help="Directory for PNG captures."
    )

    window_parser = subparsers.add_parser(
        "capture-window", help="Capture a specific Windows application window."
    )
    window_parser.add_argument(
        "--title", default="BlueStacks",
        help="Case-insensitive substring of the window title (default: BlueStacks).",
    )
    window_parser.add_argument(
        "--output-dir", default="screenshots", help="Directory for PNG captures."
    )

    subparsers.add_parser(
        "windows", help="List visible top-level Windows application windows."
    )

    analyse_parser = subparsers.add_parser(
        "analyze", help="Run local OCR on a screenshot, optionally with Gemini."
    )
    analyse_parser.add_argument("image", type=Path, help="Path to screenshot.")
    analyse_parser.add_argument(
        "--output-dir", default="observations", help="Directory for JSON results."
    )
    analyse_parser.add_argument(
        "--profile", choices=("bluestacks", "pc"), default="bluestacks",
        help="OCR layout profile (default: bluestacks). Use pc for the official PC client.",
    )
    analyse_parser.add_argument(
        "--gemini", action="store_true",
        help="Also send the screenshot to Gemini for visual interpretation (uses API quota).",
    )
    analyse_parser.add_argument(
        "--gemini-model", default=None,
        help="Optional Gemini model override (or set GEMINI_MODEL in .env).",
    )
    watch_parser = subparsers.add_parser(
        "watch", help="Periodically capture and record read-only observations of the RoK PC window."
    )
    watch_parser.add_argument(
        "--title", default="万国覚醒",
        help="Case-insensitive game window title substring (default: 万国覚醒).",
    )
    watch_parser.add_argument("--interval", type=int, default=60, help="Seconds between scans.")
    watch_parser.add_argument("--duration", type=int, default=0, help="Stop after this many seconds; 0 runs until Ctrl+C.")
    watch_parser.add_argument("--profile", choices=("bluestacks", "pc"), default="pc")
    watch_parser.add_argument("--database", default="data/account_history.sqlite3")
    watch_parser.add_argument("--screenshot-dir", default="screenshots")
    watch_parser.add_argument("--output-dir", default="observations")

    smart_parser = subparsers.add_parser(
        "monitor", help="Continuously sample the game window and run OCR only on changes."
    )
    smart_parser.add_argument("--title", default="万国覚醒")
    smart_parser.add_argument("--sample-interval", type=float, default=1.0,
                              help="Seconds between lightweight frame samples.")
    smart_parser.add_argument("--change-threshold", type=float, default=0.025,
                              help="Changed-pixel ratio required to trigger OCR.")
    smart_parser.add_argument("--stable-frames", type=int, default=2,
                              help="Consecutive changed samples required before OCR.")
    smart_parser.add_argument("--heartbeat", type=float, default=30.0,
                              help="Run OCR at least this often, even without visual changes.")
    smart_parser.add_argument("--duration", type=int, default=0,
                              help="Stop after this many seconds; 0 runs until Ctrl+C.")
    smart_parser.add_argument("--profile", choices=("bluestacks", "pc"), default="pc")
    smart_parser.add_argument("--database", default="data/account_history.sqlite3")
    smart_parser.add_argument("--screenshot-dir", default="screenshots")
    smart_parser.add_argument("--output-dir", default="observations")
    smart_parser.add_argument("--gemini", action="store_true",
                              help="Ask Gemini for strategy recommendations periodically.")
    smart_parser.add_argument("--gemini-interval", type=float, default=180.0,
                              help="Minimum seconds between Gemini strategy requests.")
    smart_parser.add_argument("--gemini-model", default=None)

    advise_parser = subparsers.add_parser(
        "advise", help="Capture the current game screen and ask Gemini what to do next."
    )
    advise_parser.add_argument("--title", default="万国覚醒")
    advise_parser.add_argument("--profile", choices=("bluestacks", "pc"), default="pc")
    advise_parser.add_argument("--database", default="data/account_history.sqlite3")
    advise_parser.add_argument("--screenshot-dir", default="screenshots")
    advise_parser.add_argument("--output", default="data/latest_gemini_advice.json")
    advise_parser.add_argument("--model", default=None)
    advise_parser.add_argument(
        "--fast", action="store_true",
        help="Skip local EasyOCR and let Gemini interpret the screenshot directly.",
    )

    plan_parser = subparsers.add_parser(
        "plan", help="Generate a prioritized farm plan from the latest account state."
    )
    plan_parser.add_argument("--database", default="data/account_history.sqlite3")
    plan_parser.add_argument("--output", default="data/latest_farm_plan.json")

    status_parser = subparsers.add_parser(
        "status", help="Show the latest known account fields from the local history database."
    )
    status_parser.add_argument("--database", default="data/account_history.sqlite3")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "capture":
        output = capture_primary_monitor(args.output_dir)
        print(f"Screenshot saved: {output}")
        return 0

    if args.command == "windows":
        from .window_capture import find_windows

        windows = find_windows()
        if not windows:
            print("No visible titled windows found.")
            return 0
        for item in windows:
            state = "minimised" if item["minimized"] else "not minimised"
            print(f'{item["title"]} | {state} | hwnd={item["hwnd"]}')
        return 0

    if args.command == "capture-window":
        from .window_capture import capture_window

        output = capture_window(args.title, args.output_dir)
        print(f"Window screenshot saved: {output}")
        return 0

    if args.command == "advise":
        from .window_capture import capture_window
        from .gemini_advisor import advise_with_gemini, save_advice

        print("Capturing current RoK window...", flush=True)
        screenshot = capture_window(args.title, args.screenshot_dir)
        local_observation = None
        if args.fast:
            print("Fast mode: skipping local EasyOCR; Gemini will inspect the screenshot directly.", flush=True)
        else:
            try:
                print("Running local OCR as supporting evidence...", flush=True)
                local_observation = analyse_image(screenshot, profile=args.profile)
            except Exception as exc:
                print(f"Local OCR unavailable ({type(exc).__name__}: {exc}); asking Gemini to inspect pixels directly.", flush=True)
        print("Asking Gemini to assess the account and recommend next actions...", flush=True)
        try:
            result = advise_with_gemini(
                screenshot,
                profile=args.profile,
                database=args.database,
                ocr_observation=local_observation,
                model=args.model,
            )
        except Exception as exc:
            print(
                f"Gemini advice unavailable ({type(exc).__name__}: {exc}). "
                "The screenshot was captured and local OCR completed. "
                "Try the command again in a minute; transient Gemini 429/5xx "
                "errors are retried automatically.",
                flush=True,
            )
            return 1
        output = save_advice(result, args.output)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\\nGemini advice saved: {output}")
        return 0

    if args.command == "plan":
        from .decision_engine import build_farm_plan, save_farm_plan

        plan = build_farm_plan(database=args.database)
        output = save_farm_plan(plan, args.output)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        print(f"\\nFarm plan saved: {output}")
        return 0

    if args.command == "status":
        from .account_store import AccountStore

        store = AccountStore(args.database)
        print(f"Observations recorded: {store.observation_count()}")
        state = store.latest_state()
        if not state:
            print("No account observations yet. Run 'python -m rok_governor watch'.")
            return 0
        for item in state:
            print(f"{item['key']}: {json.dumps(item['value'], ensure_ascii=False)}")
        return 0

    if args.command == "watch":
        from .account_store import AccountStore
        from .window_capture import capture_window

        if args.interval < 5:
            raise ValueError("--interval must be at least 5 seconds.")
        store = AccountStore(args.database)
        started = time.monotonic()
        print(
            f"Watching RoK window containing {args.title!r} every {args.interval}s. "
            "Read-only mode; press Ctrl+C to stop.",
            flush=True,
        )
        try:
            while True:
                scan_started = time.monotonic()
                screenshot = capture_window(args.title, args.screenshot_dir)
                observation = analyse_image(screenshot, profile=args.profile)
                observation["source"] = str(screenshot)
                observation_id = store.record(observation)
                output = save_observation(observation, args.output_dir)
                print(
                    f"Recorded observation #{observation_id}: "
                    f"screen={observation.get('screen', {}).get('type', 'unknown')} "
                    f"review_required={observation.get('review_required')} "
                    f"json={output}",
                    flush=True,
                )
                if args.duration and time.monotonic() - started >= args.duration:
                    break
                elapsed = time.monotonic() - scan_started
                time.sleep(max(0, args.interval - elapsed))
        except KeyboardInterrupt:
            print("\\nStopped watching.")
        return 0

    if args.command == "monitor":
        from .smart_monitor import run_smart_monitor

        run_smart_monitor(
            title=args.title,
            sample_interval=args.sample_interval,
            change_threshold=args.change_threshold,
            stable_frames=args.stable_frames,
            heartbeat=args.heartbeat,
            duration=args.duration,
            profile=args.profile,
            database=args.database,
            screenshot_dir=args.screenshot_dir,
            output_dir=args.output_dir,
            gemini_enabled=args.gemini,
            gemini_interval=args.gemini_interval,
            gemini_model=args.gemini_model,
        )
        return 0

    if args.command == "analyze":
        print(f"[1/3] Loading screenshot: {args.image}", flush=True)
        if not args.image.is_file():
            raise FileNotFoundError(f"Screenshot not found: {args.image}")

        print("[2/3] Running local EasyOCR (first run can take a while)...", flush=True)
        started = time.perf_counter()
        observation = analyse_image(args.image, profile=args.profile)
        print(f"      OCR finished in {time.perf_counter() - started:.1f}s.", flush=True)

        gemini_failed = False
        if args.gemini:
            from .gemini_vision import analyse_with_gemini

            print("[3/3] Sending screenshot to Gemini (45-second request timeout)...", flush=True)
            started = time.perf_counter()
            try:
                observation["gemini_vision"] = analyse_with_gemini(
                    args.image, ocr_observation=observation, model=args.gemini_model
                )
                print(
                    f"      Gemini finished in {time.perf_counter() - started:.1f}s.",
                    flush=True,
                )
            except Exception as exc:
                gemini_failed = True
                observation["gemini_vision"] = {
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "note": "Local OCR succeeded; Gemini analysis failed. Check network, API key, quota and model availability.",
                }
                print(
                    f"      Gemini failed after {time.perf_counter() - started:.1f}s: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

        output = save_observation(observation, args.output_dir)
        print(json.dumps(observation, indent=2, ensure_ascii=False))
        print(f"\nJSON observation saved: {output}")
        if gemini_failed:
            print("Local OCR results were saved; Gemini results are unavailable.")
            return 1
        return 0

    return 2
