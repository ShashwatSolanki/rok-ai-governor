"""Command-line interface for the Gemini-powered RoK account governor."""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .capture import capture_primary_monitor
from .ocr import analyse_image, save_observation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rok-governor",
        description="Gemini-powered Rise of Kingdoms account governor.",
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
        "windows", help="List visible top-level windows and child HWNDs."
    )

    subparsers.add_parser(
        "vm-diagnose",
        help="Read host CPU, RAM, GPU, disk, and virtualization readiness for a Windows VM.",
    )

    input_diagnose_parser = subparsers.add_parser(
        "input-diagnose",
        help="Inspect game-window input geometry without moving the pointer or sending clicks.",
    )
    input_diagnose_parser.add_argument("--title", default="万国覚醒")
    input_diagnose_parser.add_argument("--x", type=int, default=500,
                                       help="Normalized horizontal target (0..1000; default center).")
    input_diagnose_parser.add_argument("--y", type=int, default=500,
                                       help="Normalized vertical target (0..1000; default center).")
    input_diagnose_parser.add_argument(
        "--screenshot-size", nargs=2, type=int, metavar=("WIDTH", "HEIGHT"),
        help="Optional original screenshot dimensions used for coordinate mapping.",
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
    smart_parser.add_argument("--observe-only", action="store_true", help="Disable all game input; monitor and advise only.")
    smart_parser.add_argument(
        "--input-mode", choices=("background", "foreground", "adb"), default="background",
        help="Input delivery. Foreground mode focuses the game and moves the mouse.",
    )

    run_parser = subparsers.add_parser(
        "run", help="Run the resilient Gemini-powered background governor."
    )
    run_parser.add_argument("--title", default="万国覚醒")
    run_parser.add_argument("--sample-interval", type=float, default=0.8)
    run_parser.add_argument("--change-threshold", type=float, default=0.025)
    run_parser.add_argument("--stable-frames", type=int, default=1)
    run_parser.add_argument("--heartbeat", type=float, default=15.0)
    run_parser.add_argument("--gemini-interval", type=float, default=12.0)
    run_parser.add_argument("--fast", action=argparse.BooleanOptionalAction, default=True,
                            help="Skip local CPU EasyOCR and rely on Gemini vision directly (much faster).")
    run_parser.add_argument("--duration", type=int, default=0,
                            help="Stop after this many seconds; 0 runs until Ctrl+C.")
    run_parser.add_argument("--profile", choices=("bluestacks", "pc"), default="pc")
    run_parser.add_argument("--database", default="data/account_history.sqlite3")
    run_parser.add_argument("--screenshot-dir", default="screenshots")
    run_parser.add_argument("--output-dir", default="observations")
    run_parser.add_argument("--gemini-model", default=None)
    run_parser.add_argument("--retry-delay", type=float, default=5.0)
    run_parser.add_argument("--max-retry-delay", type=float, default=60.0)
    run_parser.add_argument("--observe-only", action="store_true", help="Disable all game input; monitor and advise only.")
    run_parser.add_argument(
        "--input-mode", choices=("background", "foreground", "adb"), default="background",
        help="Input delivery. Foreground mode focuses the game and moves the mouse.",
    )

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
    health_parser = subparsers.add_parser(
        "health", help="Show the governor runtime heartbeat and last successful operations."
    )
    health_parser.add_argument("--database", default="data/account_history.sqlite3")
    tasks_parser = subparsers.add_parser("tasks", help="List persistent Gemini recommendations.")
    tasks_parser.add_argument("--database", default="data/account_history.sqlite3")
    tasks_parser.add_argument("--status", choices=("pending", "completed", "dismissed", "all"), default="pending")
    done_parser = subparsers.add_parser("task-done", help="Update a task after manual review.")
    done_parser.add_argument("task_id", type=int)
    done_parser.add_argument("--status", choices=("completed", "dismissed", "pending"), default="completed")
    done_parser.add_argument("--database", default="data/account_history.sqlite3")
    subparsers.add_parser("task-reviews", help="Show Gemini's visual task-verification suggestions.").add_argument(
        "--database", default="data/account_history.sqlite3"
    )
    commands_parser = subparsers.add_parser(
        "commands", help="List structured Gemini action proposals."
    )
    commands_parser.add_argument("--database", default="data/account_history.sqlite3")
    commands_parser.add_argument(
        "--status", choices=("proposed", "completed", "dismissed", "all"), default="proposed"
    )
    command_done_parser = subparsers.add_parser(
        "command-done", help="Record manual completion or dismissal of a proposed command."
    )
    command_done_parser.add_argument("command_id", type=int)
    command_done_parser.add_argument("--status", choices=("completed", "dismissed", "proposed"), default="completed")
    command_done_parser.add_argument("--database", default="data/account_history.sqlite3")
    automation_log_parser = subparsers.add_parser("automation-log", help="Show recent unattended input attempts.")
    automation_log_parser.add_argument("--database", default="data/account_history.sqlite3")
    automation_log_parser.add_argument("--limit", type=int, default=20)
    automation_resume_parser = subparsers.add_parser(
        "automation-resume",
        help="Resume background input after reviewing a failed or uncertain visual action.",
    )
    automation_resume_parser.add_argument("--database", default="data/account_history.sqlite3")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        # Windows terminals may still use a legacy code page that cannot encode
        # the game's Japanese title. Escape only unsupported characters rather
        # than crashing a diagnostic command such as `health`.
        sys.stdout.reconfigure(errors="backslashreplace")
    args = build_parser().parse_args()

    if args.command == "capture":
        output = capture_primary_monitor(args.output_dir)
        print(f"Screenshot saved: {output}")
        return 0

    if args.command == "windows":
        from .window_capture import find_windows, find_child_windows

        windows = find_windows()
        if not windows:
            print("No visible titled windows found.")
            return 0
        for item in windows:
            state = "minimised" if item["minimized"] else "not minimised"
            print(f'{item["title"]} | {state} | hwnd={item["hwnd"]}')
            if "万国覚醒" in item["title"]:
                children = find_child_windows(item["hwnd"])
                if not children:
                    print("  (no child windows)")
                for child in children:
                    visibility = "visible" if child["visible"] else "hidden"
                    child_title = child["title"] or "(untitled)"
                    print(
                        f'  child hwnd={child["hwnd"]} | class={child["class_name"]!r} '
                        f'| title={child_title!r} | {visibility} '
                        f'| size={child["size"]} | client={child["client_rect"]} '
                        f'| pid={child["process_id"]}'
                    )
        return 0

    if args.command == "vm-diagnose":
        from .vm_host import diagnose_vm_host

        try:
            report = diagnose_vm_host()
        except (RuntimeError, OSError) as exc:
            print(f"VM host diagnostic unavailable: {exc}")
            return 1
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("\nThis diagnostic is read-only; it did not change Windows, BIOS/UEFI, or VM settings.")
        return 0

    if args.command == "input-diagnose":
        from .input_controller import diagnose_input_geometry, WindowInputError

        try:
            geometry = diagnose_input_geometry(
                args.title,
                x_normalized=args.x,
                y_normalized=args.y,
                screenshot_size=tuple(args.screenshot_size) if args.screenshot_size else None,
            )
        except WindowInputError as exc:
            print(f"Input diagnostic unavailable: {exc}")
            return 1
        print(json.dumps(geometry, indent=2, ensure_ascii=False))
        print("\nNo pointer movement, focus change, or game input was performed.")
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
        from .task_store import TaskStore

        synced_tasks = TaskStore(args.database).ingest(result)
        saved_reviews = TaskStore(args.database).record_reviews(result)
        from .command_store import CommandStore

        saved_commands = CommandStore(args.database).ingest(result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"\\nGemini advice saved: {output}")
        print(f"Governor task queue updated: {synced_tasks} recommendation(s) synced.")
        print(f"Gemini task reviews saved: {saved_reviews}.")
        print(f"Structured player commands queued: {saved_commands}.")
        return 0

    if args.command == "commands":
        from .command_store import CommandStore

        commands = CommandStore(args.database).list(args.status)
        if not commands:
            print(f"No {args.status} governor commands.")
            return 0
        for item in commands:
            print(f"CMD #{item['id']} [{item['status'].upper()}] [{item['risk'].upper()}] {item['title']}")
            print(f"   Type: {item['action_type']} | Target: {item['target'] or 'not specified'} | Confidence: {item['confidence']}")
            if item["rationale"]:
                print(f"   Why: {item['rationale']}")
            for condition in item["preconditions"]:
                print(f"   Before: {condition}")
            for index, step in enumerate(item["steps"], 1):
                print(f"   {index}. {step}")
            if item["verification"]:
                print(f"   Verify: {item['verification']}")
            print(f"   Record after manual completion/confirmation: python -m rok_governor command-done {item['id']}")
        print("The background governor may dispatch eligible low-risk proposals; check automation-log for posted input.")
        return 0

    if args.command == "automation-log":
        from .execution_store import ExecutionStore

        entries = ExecutionStore(args.database).recent(max(1, min(args.limit, 200)))
        if not entries:
            print("No unattended input attempts recorded.")
            return 0
        for item in entries:
            print(f"#{item['id']} [{item['status'].upper()}] {item['command_title']} ({item['action_type']})")
            print(f"   Started: {item['started_at']} | Finished: {item['finished_at'] or 'in progress'}")
            if item["details"]:
                print("   Details: " + json.dumps(item["details"], ensure_ascii=False))
        return 0

    if args.command == "automation-resume":
        from .execution_store import ExecutionStore

        if not ExecutionStore(args.database).resume_automation():
            print("Background input is not paused.")
            return 0
        print("Background input resumed. Continue watching the game during unattended actions.")
        return 0

    if args.command == "command-done":
        from .command_store import CommandStore

        changed = CommandStore(args.database).set_status(args.command_id, args.status)
        if not changed:
            print(f"Command #{args.command_id} was not found.")
            return 1
        print(f"Command #{args.command_id} marked {args.status}.")
        return 0

    if args.command == "plan":
        from .decision_engine import build_farm_plan, save_farm_plan

        plan = build_farm_plan(database=args.database)
        output = save_farm_plan(plan, args.output)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        print(f"\\nFarm plan saved: {output}")
        return 0

    if args.command == "tasks":
        from .task_store import TaskStore

        tasks = TaskStore(args.database).list(args.status)
        if not tasks:
            print(f"No {args.status} governor tasks.")
            return 0
        for task in tasks:
            print(f"#{task['id']} [{task['status'].upper()}] [{task['priority'].upper()}] {task['action']}")
            print(f"   Category: {task['category']} | Seen: {task['seen_count']} time(s)")
            if task["why"]:
                print(f"   Why: {task['why']}")
            for step in task["manual_steps"]:
                print(f"   - {step}")
        return 0

    if args.command == "task-reviews":
        from .task_store import TaskStore

        reviews = TaskStore(args.database).list_reviews()
        if not reviews:
            print("No Gemini task reviews yet. Run 'advise --fast' to compare the current screen with pending tasks.")
            return 0
        for review in reviews:
            print(
                f"#{review['id']} [{review['assessment'].upper()}] "
                f"confidence={review['confidence']}"
            )
            print(f"   Task: {review['action']} [{review['priority'].upper()}]")
            print(f"   Visual evidence: {review['visual_evidence']}")
            print(f"   Reviewed: {review['reviewed_at']}")
            if review["assessment"] == "likely_completed":
                print(f"   Confirm manually with: python -m rok_governor task-done {review['id']}")
        return 0

    if args.command == "task-done":
        from .task_store import TaskStore

        changed = TaskStore(args.database).set_status(args.task_id, args.status)
        if not changed:
            print(f"Task #{args.task_id} was not found.")
            return 1
        print(f"Task #{args.task_id} marked {args.status}.")
        return 0

    if args.command == "health":
        health_path = Path(args.database).with_name("runtime_status.json")
        if not health_path.is_file():
            print("No runtime health record yet. Start the governor with 'python -m rok_governor run'.")
            return 0
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Runtime health record is unreadable: {type(exc).__name__}: {exc}")
            return 1
        print(f"State: {health.get('state', 'unknown')}")
        print(f"Mode: {health.get('mode', 'unknown')}")
        print(f"Input mode: {health.get('input_mode', 'background')}")
        print(f"Game window: {health.get('window_title', 'unknown')}")
        heartbeat = health.get("updated_at")
        print(f"Last heartbeat: {heartbeat or 'never'}")
        if heartbeat:
            try:
                heartbeat_time = datetime.fromisoformat(heartbeat)
                age_seconds = max(0, int((datetime.now(timezone.utc) - heartbeat_time).total_seconds()))
                print(f"Heartbeat age: {age_seconds}s" + (" (possibly stale)" if age_seconds > 180 else ""))
            except ValueError:
                print("Heartbeat age: unavailable (invalid timestamp)")
        print(f"Last capture: {health.get('last_capture_at') or 'never'}")
        print(f"Last OCR success: {health.get('last_ocr_at') or 'never'}")
        print(f"Last Gemini success: {health.get('last_gemini_at') or 'never'}")
        print(f"Observations this session: {health.get('observations_this_session', 0)}")
        from .task_store import TaskStore

        from .execution_store import ExecutionStore

        automation_pause = ExecutionStore(args.database).automation_pause()
        print(f"Background input: {'paused' if automation_pause else 'available'}")
        if automation_pause:
            print(f"Pause reason: {automation_pause['reason']}")
            print("After reviewing the game, run: python -m rok_governor automation-resume")

        task_store = TaskStore(args.database)
        print(f"Pending recommendations: {len(task_store.list('pending'))}")
        print(f"Completed recommendations: {len(task_store.list('completed'))}")
        print(f"Consecutive failures: {health.get('consecutive_failures', 0)}")
        if health.get("last_error"):
            print(f"Last error: {health['last_error']}")
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
            automation_enabled=not args.observe_only,
            input_mode=args.input_mode,
            fast=getattr(args, "fast", True),
        )
        return 0

    if args.command == "run":
        from .smart_monitor import run_resilient_monitor

        return run_resilient_monitor(
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
            gemini_interval=args.gemini_interval,
            gemini_model=args.gemini_model,
            retry_delay=args.retry_delay,
            max_retry_delay=args.max_retry_delay,
            automation_enabled=not args.observe_only,
            input_mode=args.input_mode,
            fast=getattr(args, "fast", True),
        )

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
