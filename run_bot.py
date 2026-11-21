#!/usr/bin/env python
"""Wrapper to start the RoK Governor with a fixed high‑level goal.

Defaults:
- title: "Rise Of Kingdoms Bot Deve..."
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
        description="Start RoK Governor with a fixed high‑level goal."
    )
    parser.add_argument(
        "--goal",
        required=True,
        help="High‑level instruction for the bot (e.g., 'Run autonomously, prioritize gathering, upgrade buildings, and research when possible').",
    )
    parser.add_argument(
        "--title",
        default="Rise Of Kingdoms Bot Deve...",
        help="Exact window title as reported by `python -m rok_governor windows`.",
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

    # Example of passing a prompt via env var (uncomment if governor reads it)
    # env = os.environ.copy()
    # env["ROKGOV_PROMPT"] = args.goal

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
    subprocess.run(cmd, check=True)  # , env=env

if __name__ == "__main__":
    main()
