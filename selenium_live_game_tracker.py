"""Read-only Chess.com live move/time recorder using Selenium attached to regular Chrome.

The script starts a normal Chrome process with a dedicated persistent profile and
remote debugging enabled, then Selenium attaches to that running window. It reads
visible DOM data only: it never clicks, starts games, sends moves, or calls game APIs.

Per-move time is an estimate based on the time between visible move-list updates.
"""
from __future__ import annotations

import csv
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlsplit

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import WebDriverException
except ImportError as exc:
    raise SystemExit(
        "Selenium installed nahi hai. Run: .\\venv\\Scripts\\python.exe -m pip install selenium"
    ) from exc

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".normal_chrome_profile"
PORT_FILE = PROFILE_DIR / "DevToolsActivePort"
LOG_FILE = BASE_DIR / "chesscom_live_moves_selenium.csv"
POLL_SECONDS = 0.35

CHROME_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    / "Google/Chrome/Application/chrome.exe",
]

SAN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:O-O-O|O-O|0-0-0|0-0|"
    r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?[!?]*)"
    r"(?![A-Za-z0-9])"
)
FIGURINE_MAP = str.maketrans({
    chr(0x2654): "K", chr(0x2655): "Q", chr(0x2656): "R",
    chr(0x2657): "B", chr(0x2658): "N", chr(0x2659): "",
    chr(0x265A): "K", chr(0x265B): "Q", chr(0x265C): "R",
    chr(0x265D): "B", chr(0x265E): "N", chr(0x265F): "",
})

SNAPSHOT_JS = r"""return (() => {
  const visible = el => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden';
  };
  const norm = s => String(s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const isClock = s => /^\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?$|^\d{1,2}[.,]\d+$/.test(norm(s));
  const roots = [document], seenRoots = new Set(roots), moveTexts = [], clockEls = new Set();
  const moveSelectors = [
    'wc-simple-move-list','wc-vertical-move-list','wc-move-list',
    '[class*="move-text-component"]','[class*="vertical-move-list"]',
    '[class*="move-list"]','[class*="movelist"]','[class*="notation"]',
    '[class*="main-line-ply"]','[data-ply]'
  ];
  const clockSelectors = [
    '[data-cy="clock-time"]','.clock-time-monospace',
    '[class*="clock-component"]','wc-chess-clock','[class*="clock-time"]'
  ];
  for (let ri = 0; ri < roots.length && ri < 14; ri++) {
    const root = roots[ri];
    for (const selector of moveSelectors) {
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll(selector)).slice(0, 30); } catch (_) {}
      for (const el of nodes) {
        if (!visible(el)) continue;
        const txt = norm(el.innerText || el.textContent);
        if (txt && txt.length < 6000) moveTexts.push({ selector, text: txt.slice(0, 3000) });
        if (el.shadowRoot && !seenRoots.has(el.shadowRoot) && roots.length < 14) {
          seenRoots.add(el.shadowRoot); roots.push(el.shadowRoot);
        }
      }
    }
    for (const selector of clockSelectors) {
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll(selector)).slice(0, 60); } catch (_) {}
      for (const el of nodes) {
        if (!visible(el)) continue;
        if (isClock(el.innerText || el.textContent)) clockEls.add(el);
        if (el.shadowRoot && !seenRoots.has(el.shadowRoot) && roots.length < 14) {
          seenRoots.add(el.shadowRoot); roots.push(el.shadowRoot);
        }
      }
    }
    try {
      for (const el of Array.from(root.querySelectorAll('wc-chess-board,wc-simple-move-list,wc-vertical-move-list,wc-chess-clock')).slice(0, 25)) {
        if (el.shadowRoot && !seenRoots.has(el.shadowRoot) && roots.length < 14) {
          seenRoots.add(el.shadowRoot); roots.push(el.shadowRoot);
        }
      }
    } catch (_) {}
  }
  const clocks = [];
  const clockNodes = Array.from(clockEls).filter(el =>
    !Array.from(clockEls).some(other => other !== el && el.contains(other))
  );
  for (const el of clockNodes) {
    const value = norm(el.innerText || el.textContent);
    if (isClock(value)) clocks.push({value, className: String(el.className || '').slice(0, 100)});
  }
  let boardVisible = false, boardSize = null;
  for (const sel of ['wc-chess-board','chess-board','#board']) {
    try {
      const el = Array.from(document.querySelectorAll(sel)).find(visible);
      if (el) {
        boardVisible = true;
        const r = el.getBoundingClientRect();
        boardSize = [Math.round(r.width), Math.round(r.height)];
        break;
      }
    } catch (_) {}
  }
  const activeRe = /^(resign|offer draw|takeback|claim draw|abort game)$/i;
  let activeControls = false;
  try {
    activeControls = Array.from(document.querySelectorAll('button,[role="button"],a')).filter(visible).some(el =>
      activeRe.test(norm([el.innerText || '',el.getAttribute('aria-label') || '',el.getAttribute('title') || ''].join(' ')))
    );
  } catch (_) {}
  const resultRe = /\b(checkmate|stalemate|game over|you won|you lost|white wins|black wins|won on time|resignation|resigned|draw by|drawn game|game aborted)\b/i;
  let resultVisible = false;
  for (const sel of ['[class*="game-over"]','[class*="game-result"]','[data-cy*="game-result"]','[role="dialog"]']) {
    try {
      if (Array.from(document.querySelectorAll(sel)).some(el => visible(el) && resultRe.test(el.innerText || el.textContent || ''))) {
        resultVisible = true; break;
      }
    } catch (_) {}
  }
  return {
    url: location.href, title: document.title, boardVisible, boardSize,
    activeControls, resultVisible, clocks: clocks.slice(0, 4),
    moveTexts: moveTexts.slice(0, 35),
    bodyHint: norm(document.body?.innerText || '').slice(0, 350)
  };
})();"""

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


def port_is_live(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as response:
            return response.status == 200
    except (OSError, URLError, ValueError):
        return False


def wait_for_port(chrome: Path) -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        lines = PORT_FILE.read_text(encoding="utf-8").splitlines()
        old_port = int(lines[0].strip())
        if 1 <= old_port <= 65535 and port_is_live(old_port):
            return old_port
    except (OSError, ValueError, IndexError):
        pass

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
        try:
            lines = PORT_FILE.read_text(encoding="utf-8").splitlines()
            port = int(lines[0].strip())
            if 1 <= port <= 65535 and port_is_live(port):
                return port
        except (OSError, ValueError, IndexError):
            pass
        time.sleep(0.2)

    raise SystemExit(
        "Chrome debugging port nahi mila. .normal_chrome_profile use karne wali sabhi Chrome windows band karo, phir rerun karo."
    )


def append_csv(row: dict) -> None:
    exists = LOG_FILE.exists() and LOG_FILE.stat().st_size > 0
    with LOG_FILE.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})


def is_chess_page(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.hostname or "").lower().endswith("chess.com") and (
            parsed.path.startswith("/play") or parsed.path.startswith("/game/")
        )
    except Exception:
        return False


def parse_moves(snapshot: dict) -> list[str]:
    best: list[str] = []
    best_text = ""
    for candidate in snapshot.get("moveTexts", []):
        text = str(candidate.get("text", "")).translate(FIGURINE_MAP)
        found = [token.strip() for token in SAN_PATTERN.findall(text)]
        if found and (len(found) > len(best) or (len(found) == len(best) and len(text) > len(best_text))):
            best, best_text = found, text
    return best


def get_clocks(snapshot: dict) -> tuple[str, str]:
    values = [str(x.get("value", "")) for x in snapshot.get("clocks", [])]
    return (values[0] if values else "", values[1] if len(values) > 1 else "")


def num_clock(value: str) -> float | None:
    try:
        parts = value.replace(",", ".").split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (TypeError, ValueError):
        return None


def main() -> None:
    chrome = find_chrome()
    print("[START] Regular Chrome launch + Selenium read-only tracker.")
    print("[INFO] Profile:", PROFILE_DIR)
    print("[INFO] CSV:", LOG_FILE)
    print("[INFO] Chrome par Chess.com kholo aur game manually start karo.")
    print("[INFO] Tracker kisi button ko click nahi karega aur move nahi bhejega. Stop: Ctrl+C.")

    port = wait_for_port(chrome)
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")

    try:
        driver = webdriver.Chrome(options=options)
    except Exception as exc:
        raise SystemExit(
            "Selenium ChromeDriver attach nahi kar paya. Package update karo: "
            ".\\venv\\Scripts\\python.exe -m pip install -U selenium\n"
            f"Details: {exc}"
        ) from exc

    active_game: dict | None = None
    last_url = ""
    last_moves: list[str] = []
    last_move_time = time.monotonic()
    last_clock_signature: tuple[str, ...] = ()
    last_wait_message = 0.0
    warned_move_list = False

    try:
        while True:
            snapshot = None
            url = ""
            try:
                handles = list(driver.window_handles)
            except WebDriverException:
                print("[WARN] Chrome session disconnected.")
                break

            # Inspect only open Chess.com tabs. No navigation is performed.
            for handle in reversed(handles):
                try:
                    driver.switch_to.window(handle)
                    candidate_url = driver.current_url
                    if not is_chess_page(candidate_url):
                        continue
                    candidate = driver.execute_script(SNAPSHOT_JS)
                    if candidate.get("boardVisible") or candidate.get("clocks") or candidate.get("moveTexts"):
                        snapshot, url = candidate, candidate_url
                        break
                except WebDriverException:
                    continue

            if snapshot is None:
                if time.monotonic() - last_wait_message > 10:
                    print("[WAIT] Chess.com game DOM ka wait. Chrome mein game manually open/start karo.")
                    last_wait_message = time.monotonic()
                time.sleep(POLL_SECONDS)
                continue

            moves = parse_moves(snapshot)
            clocks = tuple(str(x.get("value", "")) for x in snapshot.get("clocks", [])[:2])
            now = now_iso()
            mono = time.monotonic()

            ticked = False
            if len(clocks) == 2 and len(last_clock_signature) == 2:
                old = [num_clock(x) for x in last_clock_signature]
                new = [num_clock(x) for x in clocks]
                ticked = any(a is not None and b is not None and b < a - 0.05 for a, b in zip(old, new))
            last_clock_signature = clocks

            if active_game and url != last_url:
                append_csv({
                    "event": "GAME_ENDED", "game_id": active_game["id"], "game_url": active_game["url"],
                    "game_started_at": active_game["started_at"], "move_observed_at": now,
                    "note": "Chess.com URL changed"
                })
                print("[GAME END] Page changed.")
                active_game, last_moves = None, []

            last_url = url
            parsed = urlsplit(url)
            start_candidate = (
                snapshot.get("boardVisible")
                and (snapshot.get("activeControls") or ticked or bool(moves))
                and not snapshot.get("resultVisible")
            )

            if active_game and snapshot.get("resultVisible"):
                append_csv({
                    "event": "GAME_ENDED", "game_id": active_game["id"], "game_url": active_game["url"],
                    "game_started_at": active_game["started_at"], "move_observed_at": now,
                    "note": "Result shown in visible page DOM"
                })
                print("[GAME END] Result shown.")
                active_game, last_moves = None, []

            if active_game and (
                len(moves) < len(last_moves)
                or any(a != b for a, b in zip(last_moves, moves[:len(last_moves)]))
            ):
                append_csv({
                    "event": "GAME_ENDED", "game_id": active_game["id"], "game_url": active_game["url"],
                    "game_started_at": active_game["started_at"], "move_observed_at": now,
                    "note": "Visible move list reset or changed"
                })
                active_game, last_moves = None, []

            if active_game is None and start_candidate:
                active_game = {
                    "id": "chesscom-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
                    "url": url, "started_at": now
                }
                last_moves = moves[:]
                last_move_time = mono
                c1, c2 = get_clocks(snapshot)
                append_csv({
                    "event": "GAME_DETECTED", "game_id": active_game["id"], "game_url": url,
                    "game_started_at": now, "move_observed_at": now, "clock_1": c1, "clock_2": c2,
                    "note": "Board and active-game controls/clock/moves detected"
                })
                print(f"[GAME DETECTED] {url} | existing moves: {len(moves)}")
                for index, move in enumerate(moves, start=1):
                    append_csv({
                        "event": "HISTORY_MOVE", "game_id": active_game["id"], "game_url": url,
                        "game_started_at": now, "ply": index,
                        "side": "White" if index % 2 else "Black", "move": move,
                        "move_observed_at": now, "clock_1": c1, "clock_2": c2,
                        "note": "Move already visible at detection; time unknown"
                    })
                warned_move_list = False

            if active_game:
                if len(moves) > len(last_moves) and moves[:len(last_moves)] == last_moves:
                    added = moves[len(last_moves):]
                    c1, c2 = get_clocks(snapshot)
                    if len(added) == 1:
                        elapsed = round(max(0.0, mono - last_move_time), 2)
                        ply = len(last_moves) + 1
                        side = "White" if ply % 2 else "Black"
                        append_csv({
                            "event": "MOVE", "game_id": active_game["id"], "game_url": active_game["url"],
                            "game_started_at": active_game["started_at"], "ply": ply, "side": side,
                            "move": added[0], "time_taken_seconds_estimate": elapsed,
                            "move_observed_at": now, "clock_1": c1, "clock_2": c2,
                            "note": "Approximate interval between move-list updates; not official clock time"
                        })
                        print(f"[MOVE] {side} {added[0]} | ~{elapsed:.2f}s | clocks {c1 or '?'} / {c2 or '?'}")
                        last_move_time = mono
                    else:
                        for offset, move in enumerate(added, start=1):
                            ply = len(last_moves) + offset
                            append_csv({
                                "event": "MOVE", "game_id": active_game["id"], "game_url": active_game["url"],
                                "game_started_at": active_game["started_at"], "ply": ply,
                                "side": "White" if ply % 2 else "Black", "move": move,
                                "move_observed_at": now, "clock_1": c1, "clock_2": c2,
                                "note": "Multiple moves arrived between polls; individual timing unknown"
                            })
                            print(f"[MOVE] {'White' if ply % 2 else 'Black'} {move} | time unknown")
                        last_move_time = mono
                    last_moves = moves[:]
                    warned_move_list = False
                elif snapshot.get("activeControls") and not moves and not warned_move_list:
                    print("[WAIT] Game-like controls detected, but visible move list selectors returned no SAN moves.")
                    print("[DEBUG] URL:", url, "| title:", snapshot.get("title"))
                    warned_move_list = True

            elif time.monotonic() - last_wait_message > 10:
                print("[WAIT] Game start not confirmed yet. Play manually; tracker is read-only.")
                last_wait_message = time.monotonic()

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print("\n[STOP] Tracker stopped by user.")
    finally:
        if active_game:
            append_csv({
                "event": "TRACKER_STOPPED", "game_id": active_game["id"], "game_url": active_game["url"],
                "game_started_at": active_game["started_at"], "move_observed_at": now_iso(),
                "note": "Tracker manually stopped"
            })
        # Stop ChromeDriver service only; leave the real Chrome window open.
        try:
            driver.service.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
