# RoK AI Governor

A read-only companion for analysing Rise of Kingdoms screenshots and turning visible account information into structured observations.

> **Safety scope:** This project is an advisory tool. It does not automate gameplay, send inputs to the game, inspect game memory, modify the client, or connect to game services. Check the game's current terms before using third-party tools alongside your account.

## Current capabilities

- Capture a screenshot of the primary desktop monitor.
- On Windows, list application windows and attempt window-only capture using the Windows PrintWindow API.
- Analyse city-layout regions locally with EasyOCR.
- Convert abbreviated resource quantities into integers and retain OCR confidence.
- Optionally send a screenshot and OCR hints to Gemini for structured visual interpretation.
- Save observations as JSON for later review.
- Keep Gemini optional: local OCR works without an API key.

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

The default model is `gemini-3.8-flash`. You can override it by setting `GEMINI_MODEL` in `.env` or passing `--gemini-model` to the command. Model availability can depend on your Google AI Studio project.

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
python -m rok_governor analyze screenshots\city.png --gemini --gemini-model gemini-3.8-flash
```

Capture the primary monitor instead:

```powershell
python -m rok_governor capture
```

### Background/minimised capture limitations

Window capture uses Windows `PrintWindow` with `PW_RENDERFULLCONTENT`. This asks Windows/the application to render the selected window into an off-screen bitmap, so it can work while other windows are in front. It is not guaranteed to work when BlueStacks is minimised: some GPU-accelerated game surfaces return a black or stale frame. The capture command rejects obvious blank frames rather than treating them as valid game screenshots. If that happens, restore BlueStacks and leave it behind your other apps while testing; do not assume minimised capture works until you have checked the resulting PNG.

The first OCR run may download EasyOCR model weights. OCR and Gemini output are both approximate. Gemini is a second opinion, not ground truth; compare its output with local OCR and the original screenshot. Missing or uncertain values must remain unknown rather than being guessed.

## Project layout

```text
src/rok_governor/
  __init__.py
  __main__.py
  capture.py
  window_capture.py
  ocr.py
  gemini_vision.py
  cli.py
screenshots/       # local captures (git-ignored)
observations/      # local JSON output (git-ignored)
.env               # local API key (git-ignored)
```

## Next milestones

1. Validate and reconcile local OCR with Gemini observations.
2. Add screen-type-specific extractors for city, map, commander, building, and research views.
3. Store user-reviewed observations in a local account history.
4. Generate recommendations from confirmed observations and maintained game knowledge.

No unattended gameplay or automated in-game actions are included.
