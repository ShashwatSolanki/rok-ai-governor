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
