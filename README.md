# Playwright Chess Computer-Bot Test

A separate Playwright-based browser test build adapted from the FAST-MAIN chess vision baseline. The legacy `chesskida` repository and its branches are read-only inputs; this repository is the isolated workspace.

## What it currently does

- Launches a persistent Playwright Chromium profile.
- Uses Chromium viewport screenshots with the existing OpenCV board recognition and visual verification pipeline in `main_playwright.py`.
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

The browser profile is saved in `playwright_chess_profile/`. Use a local or otherwise authorized test setup, and verify each move before unattended use.

## Event-driven DOM/FEN experiment

A separate proof-of-concept shows how an owned test page can publish a complete FEN and move metadata for each committed move. Playwright listens for a `chess:position` custom event and uses a `MutationObserver` as a fallback, rather than repeatedly screenshotting the board.

Run:

```bat
python dom_event_watcher.py
```

A local demo page opens. Click **Apply next move**; the Python terminal should print the new ply, actor, move and validated FEN. The watcher observes `[data-chess-board]` and its `data-fen` attribute, and reads the visible move list from `[data-move-list]`.

## Inspect a browser page's visible DOM

To see which board/piece/move-list elements are actually exposed on the target test page, run (this uses a separate Chromium profile, so you may need to sign in once):

```bat
python inspect_chess_dom.py
```

Sign in if needed, start/open the test game, wait for the board and move list, then return to the terminal and press Enter. It prints candidate DOM elements and installs a `MutationObserver` that logs relevant board/piece/move-list changes. Make one move and inspect the output. It uses bounded DOM queries to avoid exhaustively traversing a large page. It only inspects browser DOM; it does not inspect network traffic or private endpoints. Stop it with Ctrl+C.

## Important implementation note

The DOM watcher is currently a separate test experiment. `main_playwright.py` still uses screenshots and OpenCV; it has not yet been converted to consume DOM events. The diagnostic output must first show whether the target page exposes enough reliable position or move-history state to support such a conversion. Full end-to-end testing has not been completed in this environment.
