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
    parser.add_argument(
        "--hwnd", type=lambda value: int(value, 0), default=None,
        help="Attach by native window handle (decimal or 0x-prefixed hex), bypassing title matching.",
    )
    parser.add_argument("--max-elements", type=int, default=500)
    parser.add_argument("--max-depth", type=int, default=8)
    args = parser.parse_args(argv)

    from .uia_diagnostics import inspect_uia_tree, UIADiagnosticError

    try:
        result = inspect_uia_tree(
            args.title,
            hwnd=args.hwnd,
            max_elements=args.max_elements,
            max_depth=args.max_depth,
        )
    except UIADiagnosticError as exc:
        print(f"UI Automation diagnostic unavailable: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("No focus change, pointer movement, or game input was performed.")
    return 0



def _run_native_process_diagnostic(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rok_governor native-process-diagnose",
        description="Inspect native game process metadata and loaded module names (read-only).",
    )
    parser.add_argument(
        "--hwnd", type=lambda value: int(value, 0), required=True,
        help="Game window handle in decimal or 0x-prefixed hexadecimal.",
    )
    args = parser.parse_args(argv)

    from .native_process_diagnostics import (
        inspect_native_process, NativeProcessDiagnosticError,
    )

    try:
        result = inspect_native_process(args.hwnd)
    except NativeProcessDiagnosticError as exc:
        print(f"Native process diagnostic unavailable: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("Read-only inspection: no process memory reads/writes, code injection, focus changes, or game input.")
    return 0

def _run_native_import_diagnostic(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rok_governor native-import-diagnose",
        description="Inspect on-disk PE import tables for the native game and Unity binaries (read-only).",
    )
    parser.add_argument(
        "--hwnd", type=lambda value: int(value, 0), required=True,
        help="Game window handle in decimal or 0x-prefixed hexadecimal.",
    )
    args = parser.parse_args(argv)

    from .native_process_diagnostics import (
        inspect_native_process, NativeProcessDiagnosticError,
    )
    from .native_import_diagnostics import (
        inspect_game_imports, NativeImportDiagnosticError,
    )

    try:
        process = inspect_native_process(args.hwnd)
        executable_path = process.get("executable_path")
        if not executable_path:
            raise NativeImportDiagnosticError(
                "Windows did not provide the game executable path."
            )
        result = {
            "hwnd": args.hwnd,
            "process_id": process["process_id"],
            "executable_path": executable_path,
            **inspect_game_imports(executable_path),
        }
    except (NativeProcessDiagnosticError, NativeImportDiagnosticError) as exc:
        print(f"Native import diagnostic unavailable: {exc}")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("Read-only disk inspection only: no process memory access, code injection, focus change, or game input.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "uia-diagnose":
        raise SystemExit(_run_uia_diagnostic(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "native-process-diagnose":
        raise SystemExit(_run_native_process_diagnostic(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "native-import-diagnose":
        raise SystemExit(_run_native_import_diagnostic(sys.argv[2:]))

    from .cli import main

    raise SystemExit(main())
