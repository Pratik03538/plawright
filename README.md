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

A separate proof-of-concept shows how an owned test page can publish a complete FEN and move metadata for each committed move. Playwright listens for a `chess:position` custom event (the preferred path) and uses a `MutationObserver` as a fallback, rather than repeatedly screenshotting the board.

Run:

```bat
python dom_event_watcher.py
```

A local demo page opens. Click **Apply next move**; the Python terminal should print the new ply, actor, move and validated FEN. The watcher observes `[data-chess-board]` and its `data-fen` attribute, and reads the visible move list from `[data-move-list]`.

For another local/authorized page that explicitly exposes the same kind of state, options can be changed, for example:

```bat
python dom_event_watcher.py --url http://127.0.0.1:8000/board --board-selector "[data-chess-board]" --fen-attribute data-fen --moves-selector "[data-move-list]"
```

This watcher does not inspect network traffic or private backend endpoints. It can only read state the page actually exposes in its DOM. If a page does not expose a complete, reliable FEN, the watcher cannot infer it from this attribute contract alone.

## Important implementation note

The DOM watcher is currently a separate test experiment. `main_playwright.py` still uses screenshots and OpenCV; it has not yet been converted to consume DOM events. Full end-to-end testing has not been completed in this environment.
