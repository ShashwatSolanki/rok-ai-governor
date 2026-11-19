"""Command-line interface for screenshot capture and OCR."""

import argparse
import json
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
        "--gemini", action="store_true",
        help="Also send the screenshot to Gemini for visual interpretation (uses API quota).",
    )
    analyse_parser.add_argument(
        "--gemini-model", default=None,
        help="Optional Gemini model override (or set GEMINI_MODEL in .env).",
    )
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
        observation = analyse_image(args.image)
        if args.gemini:
            from .gemini_vision import analyse_with_gemini

            observation["gemini_vision"] = analyse_with_gemini(
                args.image, ocr_observation=observation, model=args.gemini_model
            )
        output = save_observation(observation, args.output_dir)
        print(json.dumps(observation, indent=2, ensure_ascii=False))
        print(f"\nJSON observation saved: {output}")
        return 0

    return 2
