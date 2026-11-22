# RoK AI Governor

A Gemini-powered secondary-account governor for Rise of Kingdoms. It observes the selected PC game window, reasons about the visible account state, and can dispatch a tightly bounded set of low-risk clicks. Background input is the default best-effort transport; foreground input is an explicit opt-in for clients that accept synthesized system mouse events. It does not inspect game memory, modify the client, or connect to game services.

> **Automation scope:** unattended input is restricted to the user's ordered task list: gathering marches, troop training, building upgrades, research, city resource collection, quests, then barbarian hunts when visible AP remains. A local allow-list filters and sorts Gemini proposals before dispatch; each action still requires visible coordinates, preconditions, a confidence threshold, freshness checks, idempotency, and an execution audit. Other activities and real-money purchases are not automated. Check the game's current terms before using third-party tools alongside your account.

## Current capabilities

- Capture a screenshot of the primary desktop monitor.
- On Windows, list application windows and attempt window-only capture using the Windows PrintWindow API.
- Analyse city-layout regions locally with EasyOCR.
- Convert abbreviated resource quantities into integers and retain OCR confidence.
- Optionally send a screenshot and OCR hints to Gemini for structured visual interpretation.
- Save observations as JSON for later review.
- Keep Gemini optional: local OCR works without an API key.
- Ask Gemini to produce structured action commands with visible targets, preconditions, one normalized click target per screenshot, risk, confidence, and verification criteria.
- Dispatch at most one eligible click per Gemini cycle through the configured input mode; re-capture and compare the live screen immediately before dispatch.
- Optionally use foreground mouse input for clients that accept synthesized system mouse events. This mode focuses the game and moves the pointer, and remains behind an explicit `--input-mode foreground` option.
- Persist command proposals and an idempotent execution audit in SQLite; ask Gemini to visually verify posted actions on the next strategy cycle.

## Requirements

- Windows 10/11
- Python 3.10+
- BlueStacks 5 or the official PC client
- Optional: Google AI Studio Gemini API key for Gemini vision analysis

## Setup (PowerShell)

```powershell
py -m venv .venv
.venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Optional Gemini setup

1. Create Gemini API keys in [Google AI Studio](https://aistudio.google.com/app/apikey). For independent quotas, use separate Google projects; multiple keys in the same project share its quota.
2. Copy `.env.example` to `.env`.
3. Paste each key into a numbered `GEMINI_API_KEY_1` through `GEMINI_API_KEY_10` entry. Set the one model in `GEMINI_MODEL`.
4. Keep `.env` private. It is ignored by Git; never paste your key into source code, chat, or GitHub.

The default model is `gemini-2.5-flash`. You can override it by setting `GEMINI_MODEL` in `.env` or passing `--gemini-model` to the command. Model availability can depend on your Google AI Studio project.

Configure one Gemini model and up to 10 Gemini API keys in `.env`. On quota, authentication, or model availability errors, the governor tries the next key in numeric order; every key uses the same model:

```dotenv
GEMINI_MODEL=gemini-2.5-flash
GEMINI_API_KEY_1=your_first_key
GEMINI_API_KEY_2=your_second_key
# GEMINI_API_KEY_3=your_third_key
# ...continue through GEMINI_API_KEY_10
```

`GEMINI_API_KEY` remains supported as a legacy key and is tried after numbered keys. `--gemini-model` overrides the single model for all keys. Do not add `GEMINI_MODEL_1` or model fallback lists; this setup intentionally uses one model only.

Gemini requests send the selected screenshot and a small summary of local OCR output to Google's API. Use `--gemini` only when you want that external analysis and associated API usage. Local OCR remains available without Gemini.

All configured keys are Google Gemini keys; no other AI providers are used. Gemini rate limits are project-based, not per key, so keys from the same Google AI Studio project share quota. Use keys from separate projects if you need independent project quotas, and follow Google's terms and quotas. [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

The governor enforces this exact task priority: **(1) send armies to gather, (2) train troops, (3) upgrade buildings, (4) research, (5) collect city resources, (6) complete quests, (7) hunt barbarians when AP remains.** Barbarian hunts require visible evidence of positive AP. Other activities, including all events and Expedition, are filtered out. When an event screen is open, the governor may use a clearly visible exit or return-to-city control.

## Usage

The `run` and `monitor` commands keep the newest 50 window/desktop screenshots in the screenshot directory and delete older captures. Change the cap with `--screenshot-retention`, for example `--screenshot-retention 25`.

List visible application windows and their titles:

```powershell
python -m rok_governor windows
```

Try to capture the BlueStacks window:

```powershell
python -m rok_governor capture-window
```

Analyse an existing screenshot locally:

```powershell
python -m rok_governor analyze screenshots\\city.png
```

Run local OCR plus Gemini visual interpretation:

```powershell
python -m rok_governor analyze screenshots\\city.png --gemini
```

Override the Gemini model if needed:

```powershell
python -m rok_governor analyze screenshots\\city.png --gemini --gemini-model gemini-3.6-flash
```

Capture the primary monitor instead:

```powershell
python -m rok_governor capture
```

### Background/minimised capture limitations

Window capture uses Windows `PrintWindow` with `PW_RENDERFULLCONTENT`. This asks Windows/the application to render the selected window into an off-screen bitmap, so it can work while other windows are in front. It is not guaranteed to work when BlueStacks is minimised: some GPU-accelerated game surfaces return a black or stale frame. The capture command rejects obvious blank frames rather than treating them as valid game screenshots. If that happens, restore BlueStacks and leave it behind your other apps while testing; do not assume minimised capture works until you have checked the resulting PNG.

The first OCR run may download EasyOCR model weights. OCR and Gemini output are both approximate. Gemini is a second opinion, not ground truth; compare its output with local OCR and the original screenshot. Missing or uncertain values must remain unknown rather than being guessed.

### Continuous Gemini governor

Start the resilient background monitor for the official PC client:

```powershell
python -m rok_governor run --title "万国覚醒" --observe-only
```

The `run` command samples the game window every two seconds, runs OCR after meaningful visual changes or on the heartbeat, and syncs recommendations, visual task reviews, and execution attempts to the local SQLite database. Use `--observe-only` to keep all game input disabled while monitoring. Gemini runs on its own schedule (default: every 180 seconds), independently of OCR; a local OCR failure does not block Gemini from interpreting the current screenshot. Gemini API failures use a separate exponential retry schedule, while capture or other monitor failures are recovered by the supervisor (5, 10, 20, 40, then at most 60 seconds). Unchanged diagnostic screenshots are discarded, and the screenshot folder is capped at the newest 200 retained captures. Use `Ctrl+C` to stop.

Useful overrides:

```powershell
python -m rok_governor run --title "万国覚醒" --gemini-interval 300 --heartbeat 45 --observe-only
python -m rok_governor run --title "万国覚醒" --duration 3600 --observe-only

# Foreground input takes focus and moves the physical pointer; it is not multitasking-safe
python -m rok_governor run --title "万国覚醒" --input-mode foreground
```

Inspect the latest runtime heartbeat from another terminal:

```powershell
python -m rok_governor health
```

Inspect recent background input attempts:

```powershell
python -m rok_governor automation-log
```

If Gemini visually marks a posted click as failed or uncertain, the governor automatically continues. It puts that same action title/type on a two-minute cooldown so the bot can consider other proposals instead of immediately repeating a questionable click. `automation-log` retains the review history. The manual `automation-resume` command remains available for explicit operator control:

```powershell
python -m rok_governor automation-resume
```

Posting an input message still does not prove the game accepted it; visual reviews remain recorded in the execution log.

The health command reads `data/runtime_status.json` and reports heartbeat freshness, the last successful capture, OCR and Gemini operations, whether background automation is enabled, pending/completed recommendation counts, current monitor state, and the latest error. A heartbeat older than three minutes is flagged as possibly stale. It is a local diagnostic snapshot; if the process is killed abruptly, its state may remain at the last recorded heartbeat rather than changing to `stopped`.

### Structured command queue

Gemini now emits a second, more operational output alongside strategic recommendations: structured command proposals. Each proposal includes an action type, visible target, parameters, preconditions, ordered steps, risk, confidence, rationale, and a verification check. The background `run` process saves these proposals to the local SQLite database.

List Gemini's structured action proposals (eligible low-risk proposals may be dispatched automatically):

```powershell
python -m rok_governor commands
```

Show completed or dismissed entries:

```powershell
python -m rok_governor commands --status all
```

After you manually perform or visually confirm a command, record it:

```powershell
python -m rok_governor command-done 1
```

Dismiss a proposal instead:

```powershell
python -m rok_governor command-done 1 --status dismissed
```

Commands without eligible input actions remain in this queue for manual review. The unattended controller supports only one explicit left-click on a visible control per Gemini assessment, limited to inspect_screen, open_menu, open_academy (navigation only), select_tab, and start_gathering, with Gemini confidence of at least 0.88 and a low-risk label. Immediately before dispatch, it captures the window again and skips the click if the screen changed by more than 2% from the screenshot Gemini assessed. Each command fingerprint is reserved before dispatch so an identical proposal cannot be replayed automatically. Every attempt is logged in SQLite. A post-action screenshot and verification criterion are saved; the next Gemini cycle reviews the current screen and records verified, failed, or uncertain. The governor waits for that review cycle before dispatching another click. Coordinates are mapped from the exact decision-screenshot dimensions into the current Win32 window rectangle and then into client coordinates, with DPI-awareness and geometry recorded in each successful attempt. Edge checks use the actual client-area bounds instead of a fixed normalized inset, so visible controls near the top/side are not rejected solely for being near the screenshot edge. Blocked attempts retain the proposed target, coordinates, and geometry/rejection reason in the audit. Opening the Academy is navigation only; starting research remains excluded from unattended execution. A posted Windows message is never treated as proof the game accepted a click.

The default `background` mode posts `WM_MOUSEMOVE`, `WM_LBUTTONDOWN`, and `WM_LBUTTONUP` messages without moving your mouse or changing focus. This works only with clients that consume those window messages. The native Rise of Kingdoms Unity client investigated so far imports Windows Raw Input APIs from `UnityPlayer.dll` (`RegisterRawInputDevices`, `GetRawInputData`, and related functions). That is strong static evidence of a Raw Input code path, not proof that it is active for every game interaction. It does explain why ordinary posted mouse messages may be ignored: they do not synthesize hardware Raw Input.

Foreground `SendInput` is a different, system-wide input path. It focuses the game and moves the physical pointer, so it interrupts use of other desktop apps; it is not a solution for simultaneous multitasking, and it is not guaranteed to satisfy a game's Raw Input handling. The controller never silently falls back from background to foreground mode. For the native Unity client, keep the governor in `--observe-only` while we finish evaluating safe input options rather than repeatedly dispatching clicks that the game may ignore. Captures try both Win32 `PrintWindow` modes; if both fail, an on-screen capture is used only while the selected game window is foreground, so the monitor does not mistake another app's pixels for the game's pixels.

Keep the game window restored; minimised GPU rendering can produce stale or blank captures. Gemini calls use API quota and send screenshots to Google. The first unattended run should be watched closely while you confirm that your specific client accepts the selected input mode.

## Project layout

```text
src/rok_governor/
  __init__.py
  __main__.py
  capture.py
  window_capture.py
  ocr.py
  gemini_vision.py
  gemini_advisor.py
  account_store.py
  task_store.py
  command_store.py
  execution_store.py
  input_controller.py
  smart_monitor.py
  cli.py
screenshots/       # local captures (git-ignored)
observations/      # local JSON output (git-ignored)
.env               # local API key (git-ignored)
```

## Next milestones

1. Improve screen-specific visual interpretation and confidence tracking.
2. Add structured rotating logs and richer recovery diagnostics beyond the runtime heartbeat.
3. Reconcile legacy duplicate tasks and improve long-term task lifecycle/history.
4. Expand account context and user-configurable farm objectives.

Only allow-listed, low-risk background click actions are automated; broader gameplay actions are deliberately excluded.

### Isolated Windows VM path for native Unity clients

The official native RoK client uses a Unity input path that may ignore ordinary background `WM_MOUSE` messages. Foreground input takes over the host desktop, so it is not suitable when the host must remain available for another account or other work.

A Windows guest VM is the next option to evaluate. The intended design is to run the official RoK client and a small governor agent inside the guest. The host governor will exchange screenshots and bounded action requests with that guest agent over a private, authenticated VM-only channel. Input will be generated inside the guest OS, not injected into the host game process and not sent through the VMware console's physical mouse forwarding. This should keep the host pointer and host focus independent, subject to VM and game compatibility.

VMware Workstation Pro is the first hypervisor candidate. VMware currently documents Workstation Pro as free for personal use and supports Windows guests; see the [official desktop hypervisor FAQ](https://www.vmware.com/docs/desktop-hypervisor-faqs). Enable 3D acceleration in the VM settings, but note that VMware exposes a virtual graphics adapter rather than guaranteeing direct GPU passthrough. The game may still refuse to launch or render correctly in a guest. Use a properly licensed Windows guest installation and install the game only from Lilith's official source.

Before installing a guest, run the read-only host check:

```powershell
python -m rok_governor vm-diagnose
```

It reports Windows version, CPU/core count, firmware virtualization status, host hypervisor presence, installed graphics adapters and driver versions, and free system-drive space. It suggests a conservative starting VM allocation where host resources permit. It does not enable virtualization, change Windows features, modify BIOS/UEFI settings, install software, or create a VM.

Do not disable Hyper-V, Memory Integrity, VBS, or other Windows security features just to make the VM run without first reviewing the security and compatibility trade-offs. We will decide the hypervisor configuration from the diagnostic report. The first guest integration milestone is capture-only; guest input remains disabled until the game is visibly working and the guest-side channel is verified.

### Native Windows process/input-path diagnostic

Inspect the official PC client's process metadata and names of modules already loaded in that process:

```powershell
python -m rok_governor native-process-diagnose --hwnd 921252
```

Use the current game HWND from `python -m rok_governor windows` if it changes. This read-only diagnostic reports the executable path and PE architecture, process architecture, window/client geometry and styles, plus loaded module paths and a filtered list of input/rendering-related module names. It does not read process memory, write to the process, inject code, alter focus, or send input. Windows may deny module enumeration if the diagnostic and game run at different integrity levels; the report will explain access failures rather than attempting privilege escalation.

Loaded module names are clues, not proof of the input API actually used at runtime. In particular, seeing `user32.dll`, `winmm.dll`, or an input-related module does not establish that the game accepts background window messages. We will use the output to choose a safe next diagnostic, not to hook or modify the game.

### Native Windows UI Automation diagnostic

Before choosing another native input mechanism, inspect whether the official Windows client exposes accessible controls through Microsoft's UI Automation tree. This command is read-only: it does not focus the game, move the pointer, or send input.

Install the updated dependencies, then run:

```powershell
python -m pip install -r requirements.txt
python -m rok_governor uia-diagnose --title "万国覚醒"
```

The report lists accessible element names, control types, automation IDs, bounds, and visibility. A sparse tree is expected for some Unity canvas-rendered clients; if the game exposes only its top-level window, UI Automation is not a useful click route and we should investigate a different native mechanism rather than fabricate controls from the screenshot.

### Native Windows PE import-table diagnostic

Inspect the on-disk import tables for the executable associated with a game HWND and the adjacent `UnityPlayer.dll` and `GameAssembly.dll` files:

```powershell
python -m rok_governor native-import-diagnose --hwnd 921252
```

Use the current HWND from `python -m rok_governor windows` if it changes. The command opens those three files read-only and reports statically imported functions related to Raw Input, XInput, DirectInput, mouse/keyboard input, and dynamic API lookup (such as `GetProcAddress`). Missing adjacent DLLs are reported rather than treated as fatal.

This is an on-disk PE parser: it does not load or execute the binaries, inspect process memory, inject code, change focus, or send game input. It examines both the normal import directory and the PE delay-load import directory, reporting input-related matches separately. Static imports are clues only: they do not prove an API is called at runtime, and functions resolved dynamically may not appear by their target name. An empty match list does not prove that an input API is unused.
