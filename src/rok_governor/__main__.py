"""Module entry point with a focused native UI Automation diagnostic."""
import argparse
import json
import sys


def _run_uia_diagnostic(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rok_governor uia-diagnose",
        description="Inventory accessible controls in a Windows game window without sending input.",
    )
    parser.add_argument("--title", default="万国覚醒")
    parser.add_argument("--max-elements", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=8)
    args = parser.parse_args(argv)

    from .uia_diagnostics import inspect_uia_tree, UIADiagnosticError

    try:
        result = inspect_uia_tree(
            args.title,
            max_elements=args.max_elements,
            max_depth=args.max_depth,
        )
    except UIADiagnosticError as exc:
        print(f"UI Automation diagnostic unavailable: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("No focus change, pointer movement, or game input was performed.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "uia-diagnose":
        raise SystemExit(_run_uia_diagnostic(sys.argv[2:]))

    from .cli import main

    raise SystemExit(main())
