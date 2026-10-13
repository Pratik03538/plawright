# Playwright Chess Computer-Bot Test

A separate Playwright-based browser test build adapted from the FAST-MAIN chess vision baseline. The legacy `chesskida` repository and its branches are read-only inputs; this repository is the isolated workspace.


## Separate Chrome: log in and start the Hikaru bot

This runner opens a separate, Playwright-controlled Google Chrome profile. It does not attach to or navigate the everyday Chrome window.

Run from PowerShell in this repo:

```powershell
git pull --ff-only origin main
python -m pip install -r requirements.txt
python -m py_compile .\start_hikaru_game.py
python .\start_hikaru_game.py
```

The username defaults to `diamond_membership-free`. On the first run, the script asks for the password with hidden input; it does not store the password in source code. The dedicated browser profile keeps the login session between runs and is excluded from Git. If Chess.com presents a CAPTCHA or extra verification, finish it in the opened Chrome window and press Enter in the terminal.

The script opens `https://www.chess.com/play/computer/Hikaru-bot`, requires that exact Hikaru URL before clicking, and ignores the general bot-picker navigation link. It does not make any chess moves. Keep the terminal running while you use the separate Chrome window; press Enter in the terminal to close that Playwright window.


## Browser mode: reuse your existing Chrome window

This build can attach to the already-open Chrome instance so Chess.com loads with that browser's normal setup. It will find the existing Chess.com Computer tab and will not navigate the ChatGPT tab.

### One-time setup

1. In the Chrome window that already has ChatGPT and Chess.com open, enter `chrome://inspect/#remote-debugging`.
2. Enable **Allow remote debugging for this browser instance** and approve Chrome's confirmation prompt.
3. Keep Chrome open. This build reads Chrome's `DevToolsActivePort` file and constructs the full WebSocket endpoint automatically. In this permission-gated mode, `/json/version` may return 404 even though the WebSocket endpoint works.

Only enable remote debugging on your own trusted machine. It gives the local automation process control of open browser tabs.

### Run the DOM inspector

```powershell
git pull --ff-only origin main
python -m py_compile .\inspect_chess_dom.py
python .\inspect_chess_dom.py
```

The checker, inspector, and bot resolve Chrome's WebSocket endpoint from `DevToolsActivePort` automatically. If using another Chrome user-data directory, set `$env:CHROME_DEVTOOLS_ACTIVE_PORT_FILE = "C:\\path\\to\\User Data\\DevToolsActivePort"` before running. A full WebSocket endpoint can also be supplied with `$env:CHROME_CDP_ENDPOINT`.

### Run the current browser bot

```powershell
python -m py_compile .\main_playwright.py
python .\main_playwright.py
```

The bot also attaches to the existing Chrome. It does not close the browser when it exits. Its chess tracking still uses the legacy screenshot/OpenCV pipeline at this stage; DOM-driven move tracking is not yet integrated.

## Requirements

- Windows desktop with Python 3.10+ recommended.
- Playwright, OpenCV, NumPy, mss, python-chess.
- Local `stockfish.exe` placed in the repository root.
- Piece templates in `pieces_png/`.
- Optional `Ultimate_GM_Bullet.bin` in the repository root.

Install packages if needed:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## Event-driven DOM/FEN experiment

A local proof-of-concept shows how an owned test page can publish a complete FEN and move metadata for each move. Playwright listens for a `chess:position` event with a `MutationObserver` fallback:

```powershell
python .\dom_event_watcher.py
```

Click **Apply next move** in the demo page; the Python terminal should print the move and validated FEN. This demo is separate from `main_playwright.py`.

## Known limitations

- `main_playwright.py` still uses screenshots and OpenCV for move recognition; it has not yet been converted to consume the Chess.com DOM.
- The DOM watcher requires a page to expose complete, reliable position data. An observed DOM element is not automatically a complete FEN.
- End-to-end testing against a real computer game is still pending. Verify the position before relying on the bot.

## Read-only game-state check (recommended next test)

Once the existing Chrome window has remote debugging enabled and the Chess.com Computer tab is open, run:

```powershell
python -m py_compile .\check_chess_game_state.py
python .\check_chess_game_state.py
```

It reads only the current page DOM and reports the visible bot-loading status, board presence, raw piece/square classes, and move-list candidates. It does not click or make moves, and it does not close the user's Chrome tabs. The check is a snapshot: if the game is still loading, rerun after the page's state changes.
