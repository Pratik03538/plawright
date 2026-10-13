# Playwright migration notes

`main_playwright.py` remains an isolated browser compatibility build. The original `main.py` and legacy `chesskida` repository are not modified.

## Existing Chrome attachment

The browser session now uses Playwright's CDP attachment instead of launching another persistent profile. It finds an already-open `chess.com/play/computer` tab and leaves other tabs, including ChatGPT, untouched.

One-time setup in the user's working Chrome window:

1. Open `chrome://inspect/#remote-debugging`.
2. Enable **Allow remote debugging for this browser instance** and accept the permission prompt.
3. Use the endpoint Chrome displays, normally `http://127.0.0.1:9222`. Override with `CHROME_CDP_ENDPOINT` if the port differs.

This is intentionally a local-only, user-approved attachment. Remote debugging provides control over all tabs in that Chrome session, so only enable it on a trusted computer and disable it when not needed.

## Current move-tracking implementation

- `main_playwright.py` attaches to an existing Chrome tab and uses the Chromium page screenshot with the retained OpenCV recognition/verification logic.
- The DOM inspector `inspect_chess_dom.py` attaches to the same existing Chrome session, logs selected network/console failures with URL parameters redacted, and reports candidate board/piece/move elements.
- `dom_event_watcher.py` and `dom_event_test.html` are a separate local example demonstrating event-driven FEN updates.

## Current findings and limits

The DOM inspection output showed a visible `wc-chess-board#board-play-computer` node with child `div.piece` elements whose classes include piece identity and square information. This is promising for DOM-based piece-position tracking, but it does not by itself prove the move list, special-move state, or complete FEN can all be obtained reliably.

The existing main bot still uses screenshots/OpenCV; the DOM reader has not yet replaced the tracking path. Test only with an authorized/local setup and verify state synchronization before relying on any automated moves.
