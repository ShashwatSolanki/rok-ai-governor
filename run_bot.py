#!/usr/bin/env python
"""Wrapper to start the RoK Governor with a fixed high‑level goal.

Defaults:
- title: "LDPlayer"
- input_mode: "adb"
- profile: "bluestacks"

If the governor supports a `ROKGOV_PROMPT` environment variable, you can
uncomment the `env` handling below.
"""
import argparse
import subprocess
import sys
import os

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start RoK Governor with a fixed high-level goal."
    )
    DEFAULT_GOAL = (
        "ONLY perform these tasks in this exact priority order: (1) send available armies to gather, "
        "(2) train troops, (3) upgrade buildings, (4) research, (5) collect resources from the city, "
        "(6) complete quests, (7) hunt barbarians only when visible AP remains. "
        "Do not perform any other activities. No real-money purchases."
    )
    parser.add_argument(
        "--goal",
        default=DEFAULT_GOAL,
        help="High-level instruction for the bot (default: the seven authorized tasks in strict priority order).",
    )
    parser.add_argument(
        "--title",
        default="LDPlayer",
        help="Window title substring as reported by `python -m rok_governor windows`.",
    )
    parser.add_argument(
        "--input-mode",
        default="adb",
        choices=["adb", "background", "foreground"],
        help="Input mode to use for clicks.",
    )
    parser.add_argument(
        "--profile",
        default="bluestacks",
        help="OCR profile (bluestacks or pc).",
    )
    args = parser.parse_args()
    # Show basic runtime info
    print(f"[INFO] Starting RoK Governor wrapper")
    print(f"[INFO] Goal: {args.goal}")
    print(f"[INFO] Window title: {args.title}")
    print(f"[INFO] Input mode: {args.input_mode}")
    print(f"[INFO] OCR profile: {args.profile}")

    # Pass the goal to the governor process
    env = os.environ.copy()
    env["ROKGOV_GOAL"] = args.goal

    cmd = [
        sys.executable,
        "-m",
        "rok_governor",
        "run",
        "--title",
        args.title,
        "--input-mode",
        args.input_mode,
        "--profile",
        args.profile,
    ]

    # Run the governor (blocks until stopped with Ctrl+C)
    subprocess.run(cmd, check=True, env=env)

if __name__ == "__main__":
    main()
