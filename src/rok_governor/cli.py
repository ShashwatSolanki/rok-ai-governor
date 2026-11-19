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

    analyse_parser = subparsers.add_parser(
        "analyze", help="Run OCR on an existing screenshot."
    )
    analyse_parser.add_argument("image", type=Path, help="Path to screenshot.")
    analyse_parser.add_argument(
        "--output-dir", default="observations", help="Directory for JSON results."
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.command == "capture":
        output = capture_primary_monitor(args.output_dir)
        print(f"Screenshot saved: {output}")
        return 0

    if args.command == "analyze":
        observation = analyse_image(args.image)
        output = save_observation(observation, args.output_dir)
        print(json.dumps(observation, indent=2, ensure_ascii=False))
        print(f"\nJSON observation saved: {output}")
        return 0

    return 2
