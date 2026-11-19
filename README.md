# RoK AI Governor

A read-only companion for analysing Rise of Kingdoms screenshots and turning visible account information into structured observations.

> **Safety scope:** This project is an advisory tool. It does not automate gameplay, send inputs to the game, inspect game memory, modify the client, or connect to game services. Check the game's current terms before using third-party tools alongside your account.

## Phase 1: Screenshot vision

Current capabilities:
- Capture a screenshot of the primary desktop monitor.
- On Windows, list application windows and attempt window-only capture using the Windows PrintWindow API.
- Analyse a supplied screenshot with EasyOCR.
- Save OCR observations as JSON for later processing.
- Keep capture and analysis separate so screenshots can be reviewed before any recommendations are made.

## Requirements

- Windows 10/11
- Python 3.10+
- BlueStacks 5 or the official PC client

## Setup (PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Usage

List visible application windows and their titles:

```powershell
python -m rok_governor windows
```

Try to capture the BlueStacks window without capturing the rest of the desktop:

```powershell
python -m rok_governor capture-window
```

If the window title differs, pass a distinctive part of its title:

```powershell
python -m rok_governor capture-window --title "BlueStacks"
```

Capture the primary monitor instead:

```powershell
python -m rok_governor capture
```

Analyse an existing screenshot:

```powershell
python -m rok_governor analyze screenshots\city.png
```

### Background/minimised capture limitations

Window capture uses Windows `PrintWindow` with `PW_RENDERFULLCONTENT`. This asks Windows/the application to render the selected window into an off-screen bitmap, so it can work while other windows are in front. It is not guaranteed to work when BlueStacks is minimised: some GPU-accelerated game surfaces return a black or stale frame. The capture command rejects obvious blank frames rather than treating them as valid game screenshots. If that happens, restore BlueStacks and leave it behind your other apps while testing; do not assume minimised capture works until you have checked the resulting PNG.

The first OCR run may download EasyOCR model weights. OCR output is an approximation: verify all resource counts and timers visually before relying on them.

## Project layout

```text
src/rok_governor/
  __init__.py
  __main__.py
  capture.py
  window_capture.py
  ocr.py
  cli.py
screenshots/       # local captures (git-ignored)
observations/      # local OCR JSON output (git-ignored)
```

## Next milestones

1. Improve OCR by defining screenshot regions and validating extracted values.
2. Add screen-type classification (city, map, commander, research, etc.).
3. Store user-reviewed observations in a local account history.
4. Generate recommendations from confirmed observations.

No unattended gameplay or automated in-game actions are included.
