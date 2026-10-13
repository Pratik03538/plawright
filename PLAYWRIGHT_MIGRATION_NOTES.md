# Playwright migration notes

`main_playwright.py` is a separate compatibility build based on the supplied FAST-MAIN baseline. The original `main.py` / baseline file is not modified.

## Screenshot-based compatibility build

- Opens a persistent Playwright-controlled Chromium profile at `https://www.chess.com/play/computer` (override with `CHESS_BOT_URL`).
- Replaces scrcpy-window capture with a screenshot of the browser viewport, converted to the same OpenCV BGR frame format the existing code expects.
- Routes existing coordinate-based clicks through `page.mouse` instead of Windows desktop mouse input.
- Keeps Stockfish, python-chess, piece templates, board calibration, move detection and visual verification logic.

## Event-driven DOM/FEN proof of concept

`dom_event_watcher.py` and `dom_event_test.html` form a separate local test. The page exposes a complete FEN and move metadata in DOM attributes and dispatches a `chess:position` custom event for every committed move. Playwright receives that event; a `MutationObserver` provides a fallback. Python validates the FEN with python-chess.

Run `python dom_event_watcher.py`, then click **Apply next move**. The watcher does not run a timed screenshot/grid scan loop. This is a proof of concept, not integrated into `main_playwright.py`.

## Inspecting the real page's visible DOM

Run `python inspect_chess_dom.py`. In the browser, open a local or otherwise authorized test game; when the board and move list are visible, return to the terminal and press Enter. The script prints candidate board/piece/move-list elements, then attaches a MutationObserver to log relevant changes when you make one move. It does not inspect private network endpoints or traffic.

The output is diagnostic only. It must establish whether the page exposes enough stable board or move-history data before we can responsibly replace screenshot tracking. The page may render some content in canvas or closed shadow DOM; in that case, a normal DOM observer may not expose the complete position.

## Important limitations

- `main_playwright.py` still uses screenshots and OpenCV, including manual grid calibration. It has not yet been converted to consume DOM events.
- The DOM watcher only works with pages that explicitly expose complete FEN or equivalent reliable move-state data.
- Neither path has completed end-to-end validation against a real game in this environment. Test only with a local or otherwise authorized setup and confirm position synchronization before relying on it.
