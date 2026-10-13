# Playwright Chess Computer-Bot Test

A separate Playwright-based browser test build adapted from the FAST-MAIN chess vision baseline. The legacy `chesskida` repository and its branches are read-only inputs; this repository is the isolated workspace.

## What it currently does

- Launches a persistent Playwright Chromium profile.
- Uses Chromium viewport screenshots with the existing OpenCV board recognition and visual verification pipeline.
- Sends board clicks through Playwright's browser mouse API.
- Keeps the existing `python-chess`, Stockfish, and GM-book selection code.

## Requirements

- Windows desktop with Python 3.10+ recommended.
- Playwright Chromium, OpenCV, NumPy, mss, python-chess.
- Local `stockfish.exe` placed in the repository root.
- Piece templates in `pieces_png/`.
- Optional `Ultimate_GM_Bullet.bin` in the repository root.

## Setup

Open a terminal in this repository directory:

```bat
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Place the required local engine/template assets alongside the code. Then run:

```bat
python main_playwright.py
```

The browser profile is saved in `playwright_chess_profile/`. Use a computer-bot testing board or a local test board; verify each move before unattended use.

## Important implementation note

This is the first compatibility pass: screenshots are still analyzed by OpenCV. It is not a DOM/FEN-driven implementation yet, and speed gains are not guaranteed until the actual browser board's DOM/state interface is verified. Full end-to-end browser testing has not been completed in this environment.
