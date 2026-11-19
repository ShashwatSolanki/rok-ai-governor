# RoK AI Governor

A Gemini-powered secondary-account governor for Rise of Kingdoms. It observes the selected PC game window, reasons about the visible account state, and can dispatch a tightly bounded set of low-risk background clicks without taking focus away from other apps. It does not inspect game memory, modify the client, or connect to game services.

> **Automation scope:** unattended input is limited to visible, low-risk routine actions (screen inspection/navigation and gathering starts) with explicit Gemini coordinates, a confidence threshold, an allow-list, idempotency, and an execution audit. Purchases, gems, rare items, upgrades, troop training, text entry, and other higher-risk actions are not automated. Check the game's current terms before using third-party tools alongside your account.

## Current capabilities

- Capture a screenshot of the primary desktop monitor.
- On Windows, list application windows and attempt window-only capture using the Windows PrintWindow API.
- Analyse city-layout regions locally with EasyOCR.
- Convert abbreviated resource quantities into integers and retain OCR confidence.
- Optionally send a screenshot and OCR hints to Gemini for structured visual interpretation.
- Save observations as JSON for later review.
- Keep Gemini optional: local OCR works without an API key.
- Ask Gemini to produce structured action commands with visible targets, preconditions, normalized click coordinates, risk, confidence, and verification criteria.
- Optionally dispatch at most one eligible command per Gemini cycle through background window messages (no desktop-wide mouse movement and no foreground activation).
- Persist command proposals and an idempotent execution audit in SQLite.

## Requirements

- Windows 10/11
- Python 3.10+
- BlueStacks 5 or the official PC client
- Optional: Google AI Studio Gemini API key for Gemini vision analysis

## Setup (PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Optional Gemini setup

1. Create an API key in [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Copy `.env.example` to `.env`.
3. Put your key in `.env` as the value of `GEMINI_API_KEY`.
4. Keep `.env` private. It is ignored by Git; never paste your key into source code, chat, or GitHub.

The default model is `gemini-3.6-flash`. You can override it by setting `GEMINI_MODEL` in `.env` or passing `--gemini-model` to the command. Model availability can depend on your Google AI Studio project.

Gemini requests send the selected screenshot and a small summary of local OCR output to Google's API. Use `--gemini` only when you want that external analysis and associated API usage. Local OCR remains available without Gemini.

## Usage

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
python -m rok_governor analyze screenshots\city.png
```

Run local OCR plus Gemini visual interpretation:

```powershell
python -m rok_governor analyze screenshots\city.png --gemini
```

Override the Gemini model if needed:

```powershell
python -m rok_governor analyze screenshots\city.png --gemini --gemini-model gemini-3.6-flash
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
python -m rok_governor run --title "万国覚醒"
```

The `run` command enables Gemini strategy reviews and bounded background input by default. It samples the game window every two seconds, runs OCR after meaningful visual changes or on the heartbeat, and syncs recommendations, visual task reviews, and execution attempts to the local SQLite database. Use `--observe-only` to disable all input. Gemini runs on its own schedule (default: every 180 seconds), independently of OCR; a local OCR failure does not block Gemini from interpreting the current screenshot. Gemini API failures use a separate exponential retry schedule, while capture or other monitor failures are recovered by the supervisor (5, 10, 20, 40, then at most 60 seconds). Unchanged diagnostic screenshots are discarded, and the screenshot folder is capped at the newest 200 retained captures. Use `Ctrl+C` to stop.

Useful overrides:

```powershell
python -m rok_governor run --title "万国覚醒" --gemini-interval 300 --heartbeat 45
python -m rok_governor run --title "万国覚醒" --duration 3600

# Disable game input while keeping Gemini monitoring enabled
python -m rok_governor run --title "万国覚醒" --observe-only
```

Inspect the latest runtime heartbeat from another terminal:

```powershell
python -m rok_governor health
```

The health command reads `data/runtime_status.json` and reports heartbeat freshness, the last successful capture, OCR and Gemini operations, pending/completed recommendation counts, current monitor state, and the latest error. A heartbeat older than three minutes is flagged as possibly stale. It is a local diagnostic snapshot; if the process is killed abruptly, its state may remain at the last recorded heartbeat rather than changing to `stopped`.

### Structured command queue

Gemini now emits a second, more operational output alongside strategic recommendations: structured command proposals. Each proposal includes an action type, visible target, parameters, preconditions, ordered steps, risk, confidence, rationale, and a verification check. The background `run` process saves these proposals to the local SQLite database.

List commands awaiting your action:

```powershell
python -m rok_governor commands
```

Show completed or dismissed entries:

```powershell
python -m rok_governor commands --status all
```

After you perform a command in the game, record it:

```powershell
python -m rok_governor command-done 1
```

Dismiss a proposal instead:

```powershell
python -m rok_governor command-done 1 --status dismissed
```

Commands without eligible input actions remain in this queue for manual review. The unattended controller supports only explicit left-clicks on visible controls, limited to inspect_screen, open_menu, select_tab, and start_gathering, with Gemini confidence of at least 0.88 and a low-risk label. Each command fingerprint is reserved before dispatch so a failed or repeated Gemini response cannot replay it automatically. Every attempted action is logged in SQLite. A post-action screenshot is saved, but a posted Windows message is not proof the game accepted the click; the next visual assessment must confirm the result.

Background input is best-effort: some game/emulator renderers ignore Windows background mouse messages. In that case the governor records the failure and does not fall back to foreground/desktop clicks. Keep the game window restored (it may sit behind other windows); minimised GPU rendering can produce stale or blank captures. Gemini calls use API quota and send screenshots to Google. The first unattended run should be watched closely while you confirm that your specific client accepts background input.

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
