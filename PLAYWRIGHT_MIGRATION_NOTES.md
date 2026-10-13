# Playwright migration notes

`main_playwright.py` is a separate compatibility build based on the supplied FAST-MAIN baseline. The original `main.py` / baseline file is not modified.

## What changed in the screenshot-based compatibility build

- Opens a persistent Playwright-controlled Chromium profile at `https://www.chess.com/play/computer` (override with `CHESS_BOT_URL`).
- Replaces scrcpy-window capture with a screenshot of the browser viewport, converted to the same OpenCV BGR frame format the existing code expects.
- Routes the existing coordinate-based clicks through `page.mouse` instead of Windows desktop mouse input.
- Keeps Stockfish, python-chess, piece templates, board calibration, move detection and visual verification logic for an initial A/B test.

## Event-driven DOM/FEN proof of concept

The new `dom_event_watcher.py` and `dom_event_test.html` are a separate local test. The page exposes a complete FEN and move metadata in DOM attributes and dispatches a `chess:position` custom event for every committed move. Playwright receives that event immediately; a `MutationObserver` is the fallback for pages that expose only DOM state changes. Python validates the FEN with python-chess.

Run from the project directory:

```bat
python dom_event_watcher.py
```

Click **Apply next move** in the demo browser page and inspect the terminal output. The watcher does not run a timed screenshot/grid scan loop. It reads only visible/exposed DOM fields and does not inspect private network endpoints.

This is a proof of concept, not yet integration into `main_playwright.py`. It will work with another page only if that page explicitly exposes a complete and reliable FEN in a DOM attribute matching the chosen selector/attribute, or provides a corresponding position event. If such state is not exposed, the watcher cannot conjure it from DOM mutations.

## Install and run the existing browser build (Windows)

From the same project directory that contains `stockfish.exe`, `gm_book.py`, `pieces_png`, and the optional `Ultimate_GM_Bullet.bin`:

```bat
python -m pip install -r requirements.txt
python -m playwright install chromium
python main_playwright.py
```

The first run creates `playwright_chess_profile`. The existing visual pipeline still requires the `Chess Vision Tracker` window and manual grid initialization/calibration, because `main_playwright.py` has not yet been converted to consume DOM events.

## Limitations

- Chromium screenshots may be no faster than `mss`; the first compatibility build's main value is removing the phone/scrcpy dependency.
- The initial board grid is centered by the old calibration behavior.
- Automatic Rematch/New Match detection still uses the baseline's visual heuristics and may need a browser-specific locator adapter.
- Neither path has completed end-to-end validation in this environment. Test only with a local or otherwise authorized test setup, and confirm that each position is synchronized before relying on it.
