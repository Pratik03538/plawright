# Playwright migration notes

`main_playwright.py` is a separate compatibility build based on the supplied FAST-MAIN baseline. The original `main.py` / baseline file is not modified.

## What changed

- Opens a persistent Playwright-controlled Chromium profile at `https://www.chess.com/play/computer` (override with `CHESS_BOT_URL`).
- Replaces scrcpy-window capture with a screenshot of the browser viewport, converted to the same OpenCV BGR frame format the existing code expects.
- Routes the existing coordinate-based clicks through `page.mouse` instead of Windows desktop mouse input.
- Keeps Stockfish, python-chess, piece templates, board calibration, move detection and visual verification logic for an initial A/B test.

## Install and run (Windows)

From the same project directory that contains `stockfish.exe`, `gm_book.py`, `pieces_png`, and the optional `Ultimate_GM_Bullet.bin`:

```bat
python -m pip install playwright
python -m playwright install chromium
python main_playwright.py
```

The first run creates `playwright_chess_profile`. Sign in in that Chromium window if needed, open/start a game against the Chess.com computer opponent, then use the `Chess Vision Tracker` window and press `R` to initialize the board. Use the existing grid controls to adjust calibration if the board is not centered.

## Limitations of this first pass

- It is a screen-capture compatibility layer, not yet a DOM-driven board reader. Chromium screenshots may be no faster than `mss`; the main value of this pass is removing the phone/scrcpy dependency while preserving the existing vision checks.
- The initial board grid is centered by the old calibration behavior. If the browser layout differs, manually calibrate with the existing controls.
- Automatic Rematch/New Match detection still uses the baseline's visual heuristics and may need a browser-specific locator adapter.
- This file has passed Python syntax compilation in the build environment, but has not been end-to-end tested against a logged-in Chess.com session. Test one game and verify each move before leaving it unattended.