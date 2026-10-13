"""Read-only Chess.com live game/move-time recorder using Playwright CDP.

Launches a regular Chrome process with a dedicated user profile, attaches via CDP,
and observes visible page DOM only. It does not click, make moves, or call game APIs.
Move timing is approximate: time between visible move-list updates (poll interval 250 ms).
"""
from __future__ import annotations

import csv
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".normal_chrome_profile"
PORT_FILE = PROFILE_DIR / "DevToolsActivePort"
LOG_FILE = BASE_DIR / "chesscom_live_moves.csv"
POLL_SECONDS = 0.25

CHROME_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    / "Google/Chrome/Application/chrome.exe",
]

# Extract SAN-like tokens only from move-list candidates, not from the whole page.
SAN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:O-O-O|O-O|0-0-0|0-0|"
    r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?[!?]*)"
    r"(?![A-Za-z0-9])"
)
# Use Unicode code points rather than literal chess symbols here. This avoids
# Windows PowerShell code-page corruption when downloading the file via a pipe.
FIGURINE_MAP = {
    0x2654: "K", 0x2655: "Q", 0x2656: "R", 0x2657: "B", 0x2658: "N", 0x2659: "",
    0x265A: "K", 0x265B: "Q", 0x265C: "R", 0x265D: "B", 0x265E: "N", 0x265F: "",
}

SNAPSHOT_JS = r"""() => {
  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden';
  };
  const normalize = (s) => String(s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const isClock = (s) => /^(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?$|^\d{1,2}[.,]\d+$/.test(normalize(s));
  const roots = [document];
  const knownRoots = new Set(roots);
  const moveTexts = [];
  const clockEls = new Set();
  const moveSelectors = [
    'wc-simple-move-list', 'wc-vertical-move-list', 'wc-move-list',
    '[class*="move-text-component"]', '[class*="vertical-move-list"]',
    '[class*="move-list"]', '[class*="movelist"]', '[class*="notation"]',
    '[class*="main-line-ply"]', '[data-ply]'
  ];
  const clockSelectors = [
    '[data-cy="clock-time"]', '.clock-time-monospace',
    '[class*="clock-component"]', 'wc-chess-clock',
    '[class*="clock-time"]'
  ];
  const addMove = (el, selector) => {
    if (!el || !visible(el)) return;
    const txt = normalize(el.innerText || el.textContent);
    if (!txt || txt.length > 5000) return;
    moveTexts.push({ selector, text: txt.slice(0, 2500) });
    if (el.shadowRoot && !knownRoots.has(el.shadowRoot) && roots.length < 12) {
      knownRoots.add(el.shadowRoot);
      roots.push(el.shadowRoot);
    }
  };

  for (let ri = 0; ri < roots.length && ri < 12; ri++) {
    const root = roots[ri];
    for (const selector of moveSelectors) {
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll(selector)).slice(0, 30); } catch (_) {}
      for (const el of nodes) addMove(el, selector);
    }
    for (const selector of clockSelectors) {
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll(selector)).slice(0, 40); } catch (_) {}
      for (const el of nodes) {
        if (visible(el) && isClock(el.innerText || el.textContent)) clockEls.add(el);
        if (el.shadowRoot && !knownRoots.has(el.shadowRoot) && roots.length < 12) {
          knownRoots.add(el.shadowRoot);
          roots.push(el.shadowRoot);
        }
      }
    }
    // Discover open shadow roots on major Chess.com components without crawling the entire DOM.
    let components = [];
    try {
      components = Array.from(root.querySelectorAll(
        'wc-chess-board, wc-simple-move-list, wc-vertical-move-list, wc-chess-clock'
      )).slice(0, 20);
    } catch (_) {}
    for (const el of components) {
      if (el.shadowRoot && !knownRoots.has(el.shadowRoot) && roots.length < 12) {
        knownRoots.add(el.shadowRoot);
        roots.push(el.shadowRoot);
      }
    }
  }

  const clocks = [];
  const clockNodes = Array.from(clockEls).filter(el =>
    !Array.from(clockEls).some(other => other !== el && el.contains(other))
  );
  for (const el of clockNodes) {
    const value = normalize(el.innerText || el.textContent);
    if (isClock(value)) clocks.push({ value, className: String(el.className || '').slice(0, 100) });
  }

  const boardSelectors = ['wc-chess-board', 'chess-board', '#board'];
  let boardVisible = false;
  for (const selector of boardSelectors) {
    try {
      if (Array.from(document.querySelectorAll(selector)).some(visible)) {
        boardVisible = true;
        break;
      }
    } catch (_) {}
  }

  const activeControlPattern = /^(resign|offer draw|takeback|claim draw|abort game)$/i;
  let activeControls = false;
  try {
    activeControls = Array.from(document.querySelectorAll('button,[role="button"],a'))
      .filter(visible).some(el => activeControlPattern.test(normalize(
        (el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('title') || '')
      )));
  } catch (_) {}

  const resultPattern = /\b(checkmate|stalemate|game over|you won|you lost|white wins|black wins|won on time|resignation|draw by|drawn game|game aborted)\b/i;
  let resultVisible = false;
  for (const selector of ['[class*="game-over"]','[class*="game-result"]','[data-cy*="game-result"]','[role="dialog"]']) {
    try {
      if (Array.from(document.querySelectorAll(selector)).some(el =>
        visible(el) && resultPattern.test(el.innerText || el.textContent || '')
      )) {
        resultVisible = true;
        break;
      }
    } catch (_) {}
  }

  const r = document.querySelector('wc-chess-board, chess-board, #board')?.getBoundingClientRect();
  return {
    url: location.href,
    title: document.title,
    boardVisible,
    boardSize: r ? [Math.round(r.width), Math.round(r.height)] : null,
    activeControls,
    resultVisible,
    clocks: clocks.slice(0, 4),
    moveTexts: moveTexts.slice(0, 30),
    bodyHint: normalize(document.body?.innerText || '').slice(0, 500)
  };
}"""

CSV_FIELDS = [
    "event", "game_id", "game_url", "game_started_at", "ply", "side", "move",
    "time_taken_seconds_estimate", "move_observed_at", "clock_1", "clock_2", "note"
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def find_chrome() -> Path:
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit("Chrome.exe nahi mila. Chrome installation path check karo.")


def append_csv(row: dict) -> None:
    file_exists = LOG_FILE.exists() and LOG_FILE.stat().st_size > 0
    with LOG_FILE.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})


def chesscom_page(page) -> bool:
    try:
        parsed = urlsplit(page.url)
        return (parsed.hostname or "").lower().endswith("chess.com") and (
            parsed.path.startswith("/play") or parsed.path.startswith("/game/")
        )
    except Exception:
        return False


def parse_moves(snapshot: dict) -> tuple[list[str], str]:
    best_moves: list[str] = []
    best_text = ""
    for candidate in snapshot.get("moveTexts", []):
        text = str(candidate.get("text", "")).translate(FIGURINE_MAP)
        tokens = [token.strip() for token in SAN_PATTERN.findall(text)]
        # A move-list candidate should actually resemble notation, not just a UI label.
        if tokens and (len(tokens) > len(best_moves) or
                       (len(tokens) == len(best_moves) and len(text) > len(best_text))):
            best_moves, best_text = tokens, text
    return best_moves, best_text


def choose_clock_values(snapshot: dict) -> tuple[str, str]:
    values = [str(item.get("value", "")) for item in snapshot.get("clocks", [])]
    return (values[0] if values else "", values[1] if len(values) > 1 else "")


def wait_for_devtools_port(chrome: Path) -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # If a previous run's remote-debug Chrome is still open, try reusing its port.
    if PORT_FILE.exists():
        try:
            lines = PORT_FILE.read_text(encoding="utf-8").splitlines()
            port = int(lines[0].strip())
            if 1 <= port <= 65535:
                return port
        except (OSError, ValueError, IndexError):
            pass

    # Old Chrome instances opened without CDP may hold this profile; close them before retrying.
    try:
        PORT_FILE.unlink(missing_ok=True)
    except OSError:
        pass

    subprocess.Popen([
        str(chrome),
        f"--user-data-dir={PROFILE_DIR}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        "about:blank",
    ])

    deadline = time.time() + 25
    while time.time() < deadline:
        if PORT_FILE.exists():
            try:
                lines = PORT_FILE.read_text(encoding="utf-8").splitlines()
                port = int(lines[0].strip())
                if 1 <= port <= 65535:
                    return port
            except (OSError, ValueError, IndexError):
                pass
        time.sleep(0.2)

    raise SystemExit(
        "Chrome ka DevTools port nahi mila. Agar .normal_chrome_profile wali Chrome window pehle se open hai, "
        "use poori tarah band karke script dobara chalao."
    )


def main() -> None:
    chrome = find_chrome()
    print("[START] Chrome ko normal process ke roop mein launch kar raha hoon; Playwright sirf read-only DOM tracking karega.")
    print("[INFO] Profile:", PROFILE_DIR)
    print("[INFO] CSV log:", LOG_FILE)
    print("[INFO] Chrome mein Chess.com khol kar apna game normal tarike se khelo.")
    print("[INFO] Koi click ya chess move send nahi hoga. Stop: Ctrl+C.")

    with sync_playwright() as playwright:
        port = wait_for_devtools_port(chrome)
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=15000)
        except PlaywrightError as exc:
            raise SystemExit(f"Playwright Chrome CDP se connect nahi hua: {exc}") from exc

        active_game: dict | None = None
        last_url = ""
        last_moves: list[str] = []
        last_move_clock_start = time.monotonic()
        last_clock_signature: tuple[str, ...] = ()
        last_clock_change = 0.0
        last_wait_message = 0.0
        last_no_moves_warning = False

        try:
            while True:
                try:
                    pages = [
                        page
                        for context in browser.contexts
                        for page in context.pages
                        if not page.is_closed() and chesscom_page(page)
                    ]
                except PlaywrightError:
                    print("[WARN] Chrome connection ended.")
                    break

                snapshot = None
                current_page = None
                for page in reversed(pages):
                    try:
                        candidate = page.evaluate(SNAPSHOT_JS)
                    except PlaywrightError:
                        continue
                    if candidate.get("boardVisible") or candidate.get("clocks") or candidate.get("moveTexts"):
                        snapshot, current_page = candidate, page
                        break

                if snapshot is None:
                    if time.monotonic() - last_wait_message > 10:
                        print("[WAIT] Chess.com game DOM ka wait kar raha hoon. Browser mein game khol kar khelo.")
                        last_wait_message = time.monotonic()
                    time.sleep(POLL_SECONDS)
                    continue

                url = str(snapshot.get("url", ""))
                moves, move_text = parse_moves(snapshot)
                clock_values = tuple(str(item.get("value", "")) for item in snapshot.get("clocks", [])[:2])
                now_wall = now_iso()
                mono = time.monotonic()
                clock_ticked = False
                if len(clock_values) == 2 and len(last_clock_signature) == 2:
                    def clock_number(value: str) -> float | None:
                        try:
                            pieces = value.replace(",", ".").split(":")
                            if len(pieces) == 3:
                                return float(pieces[0]) * 3600 + float(pieces[1]) * 60 + float(pieces[2])
                            if len(pieces) == 2:
                                return float(pieces[0]) * 60 + float(pieces[1])
                            return float(pieces[0])
                        except (ValueError, TypeError):
                            return None
                    old_nums = [clock_number(x) for x in last_clock_signature]
                    new_nums = [clock_number(x) for x in clock_values]
                    clock_ticked = any(
                        old is not None and new is not None and new < old - 0.05
                        for old, new in zip(old_nums, new_nums)
                    )
                if clock_ticked:
                    last_clock_change = mono
                last_clock_signature = clock_values

                # URL navigation between different games closes the prior record cleanly.
                if active_game and url != last_url:
                    append_csv({
                        "event": "GAME_ENDED", "game_id": active_game["id"],
                        "game_url": active_game["url"], "game_started_at": active_game["started_at"],
                        "move_observed_at": now_wall, "note": "Chess.com URL changed"
                    })
                    print("[GAME END] Page changed:", last_url, "->", url)
                    active_game = None
                    last_moves = []

                last_url = url
                parsed_url = urlsplit(url)
                is_play_page = parsed_url.path.startswith("/play")

                can_start = (
                    bool(snapshot.get("boardVisible"))
                    and (bool(snapshot.get("activeControls")) or clock_ticked or (is_play_page and len(moves) > 0))
                )

                if active_game and snapshot.get("resultVisible"):
                    append_csv({
                        "event": "GAME_ENDED", "game_id": active_game["id"],
                        "game_url": active_game["url"], "game_started_at": active_game["started_at"],
                        "move_observed_at": now_wall, "note": "Result text visible"
                    })
                    print("[GAME END] Result visible; logged", active_game["id"])
                    active_game = None
                    last_moves = []

                if active_game and (
                    len(moves) < len(last_moves)
                    or any(a != b for a, b in zip(last_moves, moves[:len(last_moves)]))
                ):
                    append_csv({
                        "event": "GAME_ENDED", "game_id": active_game["id"],
                        "game_url": active_game["url"], "game_started_at": active_game["started_at"],
                        "move_observed_at": now_wall, "note": "Move list reset or changed"
                    })
                    active_game = None
                    last_moves = []

                if active_game is None and can_start and not snapshot.get("resultVisible"):
                    active_game = {
                        "id": "chesscom-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
                        "url": url,
                        "started_at": now_wall,
                    }
                    last_moves = moves[:]
                    last_move_clock_start = mono
                    c1, c2 = choose_clock_values(snapshot)
                    append_csv({
                        "event": "GAME_DETECTED", "game_id": active_game["id"],
                        "game_url": url, "game_started_at": now_wall,
                        "move_observed_at": now_wall, "clock_1": c1, "clock_2": c2,
                        "note": "Game board and active controls/clock/moves detected"
                    })
                    print(f"[GAME DETECTED] {url}")
                    print(f"[INFO] Visible move count at detection: {len(moves)}")
                    for index, move in enumerate(moves, start=1):
                        c1, c2 = choose_clock_values(snapshot)
                        append_csv({
                            "event": "HISTORY_MOVE", "game_id": active_game["id"],
                            "game_url": url, "game_started_at": active_game["started_at"],
                            "ply": index, "side": "White" if index % 2 else "Black",
                            "move": move, "move_observed_at": now_wall,
                            "clock_1": c1, "clock_2": c2,
                            "note": "Move already visible when tracker attached; time unavailable"
                        })
                    if moves:
                        print("[MOVES] Existing history saved with unknown timings; only new moves get estimated times.")
                    last_move_clock_start = mono
                    last_no_moves_warning = False

                if active_game:
                    if len(moves) > len(last_moves) and moves[:len(last_moves)] == last_moves:
                        added = moves[len(last_moves):]
                        c1, c2 = choose_clock_values(snapshot)
                        if len(added) == 1:
                            taken = round(max(0.0, mono - last_move_clock_start), 2)
                            note = "Estimated from consecutive move-list updates; polling interval 0.25s"
                            side = "White" if (len(last_moves) + 1) % 2 else "Black"
                            append_csv({
                                "event": "MOVE", "game_id": active_game["id"],
                                "game_url": active_game["url"], "game_started_at": active_game["started_at"],
                                "ply": len(last_moves) + 1, "side": side, "move": added[0],
                                "time_taken_seconds_estimate": taken,
                                "move_observed_at": now_wall, "clock_1": c1, "clock_2": c2,
                                "note": note
                            })
                            print(f"[MOVE] {side} {added[0]} | ~{taken:.2f}s | clocks {c1 or '?'} / {c2 or '?'}")
                            last_move_clock_start = mono
                        else:
                            for offset, move in enumerate(added, start=1):
                                ply = len(last_moves) + offset
                                append_csv({
                                    "event": "MOVE", "game_id": active_game["id"],
                                    "game_url": active_game["url"], "game_started_at": active_game["started_at"],
                                    "ply": ply, "side": "White" if ply % 2 else "Black",
                                    "move": move, "move_observed_at": now_wall, "clock_1": c1, "clock_2": c2,
                                    "note": "Several moves appeared between scans; per-move time unknown"
                                })
                                print(f"[MOVE] {'White' if ply % 2 else 'Black'} {move} | time unknown (multiple moves appeared together)")
                            last_move_clock_start = mono
                        last_moves = moves[:]
                        last_no_moves_warning = False
                    elif snapshot.get("activeControls") and not snapshot.get("moveTexts") and not last_no_moves_warning:
                        print("[WAIT] Game detected, but move-list DOM selector was not found yet.")
                        print("[DEBUG] Page URL:", url, "| title:", snapshot.get("title"))
                        last_no_moves_warning = True
                else:
                    if time.monotonic() - last_wait_message > 10:
                        print("[WAIT] Game start confirm nahi hua. Board open rakho; tracker sirf read karta hai.")
                        last_wait_message = time.monotonic()

                time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("\n[STOP] Tracker stopped.")
        finally:
            if active_game:
                append_csv({
                    "event": "TRACKER_STOPPED", "game_id": active_game["id"],
                    "game_url": active_game["url"], "game_started_at": active_game["started_at"],
                    "move_observed_at": now_iso(), "note": "Tracker stopped manually"
                })
            # Intentionally do not call browser.close(): keep the user's Chrome window open.


if __name__ == "__main__":
    main()
