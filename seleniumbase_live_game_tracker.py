"""Read-only Chess.com live move/time recorder using SeleniumBase's standard Chrome mode.

SeleniumBase launches a normal browser window with a dedicated persistent profile.
This script reads visible DOM data only: it never clicks, starts games, sends moves,
or calls game APIs. UC/stealth mode is deliberately not enabled.

Reads rendered DOM for individual moves, visible time annotations, clocks, player labels, turn, result, and board-piece metadata. Only explicit DOM annotations are treated as move durations; poll timing is marked as an estimate.
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess

try:
    import chess
except ImportError as exc:
    raise SystemExit(
        "python-chess missing. Run: .\\venv\\Scripts\\python.exe -m pip install python-chess"
    ) from exc
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlsplit

try:
    from seleniumbase import Driver
    from selenium.common.exceptions import WebDriverException
except ImportError as exc:
    raise SystemExit(
        "SeleniumBase installed nahi hai. Run: .\\venv\\Scripts\\python.exe -m pip install -U seleniumbase"
    ) from exc

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".normal_chrome_profile"
PORT_FILE = PROFILE_DIR / "DevToolsActivePort"
LOG_FILE = BASE_DIR / "chesscom_live_moves_seleniumbase.csv"
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
    return s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
  };
  const norm = s => String(s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ').trim();
  const isClock = s => /^\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?$|^\d{1,2}[.,]\d+$/.test(norm(s));
  const roots = [document], seenRoots = new Set(roots);
  const moveTexts = [], directGroups = {}, clockEls = new Set(), dataPlyNodes = [];
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
  const addShadow = el => {
    try {
      if (el && el.shadowRoot && !seenRoots.has(el.shadowRoot) && roots.length < 18) {
        seenRoots.add(el.shadowRoot); roots.push(el.shadowRoot);
      }
    } catch (_) {}
  };
  for (let ri = 0; ri < roots.length && ri < 18; ri++) {
    const root = roots[ri];
    // Broad candidates remain available for debug/fallback only.
    for (const selector of moveSelectors) {
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll(selector)).slice(0, 100); } catch (_) {}
      for (const el of nodes) {
        if (!visible(el)) continue;
        const txt = norm(el.innerText || el.textContent);
        if (txt && txt.length < 6000) moveTexts.push({selector, text: txt.slice(0, 3000)});
        addShadow(el);
      }
    }
    // Prefer single notation nodes. Do not let a large parent consume the candidate limit.
    for (const selector of ['[class*="notation"]','[class*="main-line-ply"]']) {
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll(selector)).filter(visible); } catch (_) {}
      const unique = nodes.filter((el, i) => nodes.indexOf(el) === i);
      const leaves = unique.filter(el => !unique.some(other =>
        other !== el && el.contains(other) && norm(other.innerText || other.textContent)
      ));
      const group = [];
      for (const el of leaves) {
        const txt = norm(el.innerText || el.textContent);
        if (txt && txt.length <= 120) {
          group.push({
            text: txt,
            selector,
            className: String(el.className || '').slice(0, 140),
            dataPly: el.getAttribute('data-ply') || ''
          });
        }
      }
      if (group.length) directGroups[selector] = group.slice(0, 500);
    }
    let plyNodes = [];
    try { plyNodes = Array.from(root.querySelectorAll('[data-ply]')).filter(visible).slice(0, 250); } catch (_) {}
    for (const el of plyNodes) {
      const txt = norm(el.innerText || el.textContent);
      const title = norm(el.getAttribute('title') || '');
      const aria = norm(el.getAttribute('aria-label') || '');
      const className = String(el.className || '').slice(0, 140);
      const combined = [txt, title, aria].filter(Boolean).join(' ');
      const match = combined.match(/(?:^|\s)(\d+(?:[.,]\d+)?)\s*s(?:\s|$)/i);
      dataPlyNodes.push({
        ply: el.getAttribute('data-ply') || '',
        text: txt.slice(0, 120),
        title: title.slice(0, 100),
        ariaLabel: aria.slice(0, 100),
        className,
        seconds: match ? Number(match[1].replace(',', '.')) : null
      });
      addShadow(el);
    }
    for (const selector of clockSelectors) {
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll(selector)).slice(0, 100); } catch (_) {}
      for (const el of nodes) {
        if (visible(el) && isClock(el.innerText || el.textContent)) clockEls.add(el);
        addShadow(el);
      }
    }
    try {
      for (const el of Array.from(root.querySelectorAll(
        'wc-chess-board,wc-simple-move-list,wc-vertical-move-list,wc-chess-clock'
      )).slice(0, 50)) addShadow(el);
    } catch (_) {}
  }

  const contextOf = el => {
    const parts = [];
    let cur = el;
    for (let depth = 0; cur && depth < 6; depth++) {
      try {
        parts.push(String(cur.className || ''));
        parts.push(String(cur.id || ''));
        parts.push(String(cur.getAttribute && cur.getAttribute('aria-label') || ''));
        parts.push(String(cur.getAttribute && cur.getAttribute('data-cy') || ''));
        parts.push(String(cur.getAttribute && cur.getAttribute('data-testid') || ''));
        parts.push(String(cur.getAttribute && cur.getAttribute('title') || ''));
        cur = cur.parentElement;
      } catch (_) { break; }
    }
    return norm(parts.join(' '));
  };
  const clocks = [];
  const clockNodes = Array.from(clockEls).filter(el =>
    !Array.from(clockEls).some(other => other !== el && el.contains(other))
  );
  for (const el of clockNodes) {
    const value = norm(el.innerText || el.textContent);
    if (!isClock(value)) continue;
    const context = contextOf(el);
    let side = '';
    if (/\b(white|light|player-white|white-player|whiteclock|white-clock)\b/i.test(context)) side = 'White';
    else if (/\b(black|dark|player-black|black-player|blackclock|black-clock)\b/i.test(context)) side = 'Black';
    clocks.push({
      value,
      side,
      context: context.slice(0, 220),
      className: String(el.className || '').slice(0, 100),
      ariaLabel: norm(el.getAttribute('aria-label') || ''),
      title: norm(el.getAttribute('title') || '')
    });
  }

  const players = [];
  const playerLinks = new Set();
  for (const root of roots) {
    let links = [];
    try { links = Array.from(root.querySelectorAll('a[href*="/member/"]')).filter(visible).slice(0, 30); } catch (_) {}
    for (const link of links) {
      if (playerLinks.has(link)) continue;
      playerLinks.add(link);
      const username = (link.getAttribute('href') || '').match(/\/member\/([^/?#]+)/i);
      let cur = link, ctx = '', pickedText = '';
      for (let depth = 0; cur && depth < 6; depth++) {
        const t = norm(cur.innerText || cur.textContent);
        const a = contextOf(cur);
        if (t && t.length <= 180) { pickedText = t; ctx = a; }
        if (/(player|white|black|opponent|member)/i.test(a) && t.length <= 180) break;
        cur = cur.parentElement;
      }
      const meta = (ctx + ' ' + pickedText).toLowerCase();
      let side = '';
      if (/\bwhite\b|player-white|white-player/.test(meta)) side = 'White';
      else if (/\bblack\b|player-black|black-player/.test(meta)) side = 'Black';
      const isYou = /\b(you|current-user|own-player|player-self)\b/i.test(meta);
      const name = norm(link.innerText || link.textContent);
      if (name || username) players.push({
        name: name.slice(0, 80),
        username: username ? decodeURIComponent(username[1]) : '',
        side, isYou,
        context: ctx.slice(0, 180),
        cardText: pickedText.slice(0, 180)
      });
    }
  }

  let boardEl = null, boardSize = null, orientation = '';
  for (const root of roots) {
    for (const sel of ['wc-chess-board','chess-board','#board']) {
      try {
        const el = Array.from(root.querySelectorAll(sel)).find(visible);
        if (el) {
          boardEl = el;
          const r = el.getBoundingClientRect();
          boardSize = [Math.round(r.width), Math.round(r.height)];
          const cls = contextOf(el);
          if (/orientation-white|orientationwhite|flipped-false/i.test(cls)) orientation = 'White';
          else if (/orientation-black|orientationblack|flipped-true/i.test(cls)) orientation = 'Black';
          break;
        }
      } catch (_) {}
    }
    if (boardEl) break;
  }
  const boardPieces = [], highlightedSquares = [];
  if (boardEl) {
    const boardRoot = boardEl.shadowRoot || boardEl;
    let pieces = [];
    try { pieces = Array.from(boardRoot.querySelectorAll('[class*="piece"]')).filter(visible).slice(0, 64); } catch (_) {}
    for (const el of pieces) {
      const cls = String(el.className || '');
      const tokens = cls.split(/\s+/);
      let color = '', type = '';
      for (const tok of tokens) {
        const m = tok.match(/^([wb])([kqrbnp])$/i);
        if (m) { color = m[1].toLowerCase() === 'w' ? 'White' : 'Black'; type = m[2].toUpperCase(); break; }
      }
      const squareClass = cls.match(/\bsquare-([1-8][1-8])\b/);
      const dataSquare = el.getAttribute('data-square') || '';
      if (color && type) boardPieces.push({
        color, piece: type, square: /^[a-h][1-8]$/.test(dataSquare) ? dataSquare : '',
        rawSquare: squareClass ? squareClass[1] : '',
        className: cls.slice(0, 120),
        ariaLabel: norm(el.getAttribute('aria-label') || '')
      });
    }
    let highlights = [];
    try { highlights = Array.from(boardRoot.querySelectorAll('[class*="highlight"],[class*="check"]')).filter(visible).slice(0, 40); } catch (_) {}
    for (const el of highlights) {
      const cls = String(el.className || '');
      const sq = cls.match(/\bsquare-([1-8][1-8])\b/);
      const ds = el.getAttribute('data-square') || '';
      highlightedSquares.push({
        square: /^[a-h][1-8]$/.test(ds) ? ds : '',
        rawSquare: sq ? sq[1] : '',
        className: cls.slice(0, 120)
      });
    }
  }

  let domTurn = '';
  const turnSelectors = [
    '[aria-label*="to move" i]','[data-cy*="turn"]','[class*="turn-indicator"]',
    '[class*="side-to-move"]','[class*="player-to-move"]'
  ];
  for (const root of roots) {
    for (const sel of turnSelectors) {
      try {
        const el = Array.from(root.querySelectorAll(sel)).find(visible);
        if (el) {
          const t = norm((el.getAttribute('aria-label') || '') + ' ' + (el.innerText || el.textContent) + ' ' + contextOf(el));
          if (/\bwhite\b/i.test(t)) { domTurn = 'White'; break; }
          if (/\bblack\b/i.test(t)) { domTurn = 'Black'; break; }
        }
      } catch (_) {}
    }
    if (domTurn) break;
  }
  const activeRe = /^(resign|offer draw|takeback|claim draw|abort game)$/i;
  let activeControls = false;
  try {
    activeControls = Array.from(document.querySelectorAll('button,[role="button"],a')).filter(visible).some(el =>
      activeRe.test(norm([el.innerText || '',el.getAttribute('aria-label') || '',el.getAttribute('title') || ''].join(' ')))
    );
  } catch (_) {}
  const resultRe = /\b(checkmate|stalemate|game over|you won|you lost|white wins|black wins|won on time|resignation|resigned|draw by|drawn game|game aborted)\b/i;
  let resultVisible = false, resultText = '';
  for (const sel of ['[class*="game-over"]','[class*="game-result"]','[data-cy*="game-result"]','[data-cy*="game-over"]']) {
    try {
      const el = Array.from(document.querySelectorAll(sel)).find(el => visible(el) && resultRe.test(el.innerText || el.textContent || ''));
      if (el) { resultVisible = true; resultText = norm(el.innerText || el.textContent).slice(0, 250); break; }
    } catch (_) {}
  }
  const statusText = norm(document.body?.innerText || '').slice(0, 2500);
  const directMoves = directGroups['[class*="notation"]'] && directGroups['[class*="notation"]'].length
    ? directGroups['[class*="notation"]']
    : (directGroups['[class*="main-line-ply"]'] || []);
  const moveTimes = dataPlyNodes.filter(x => Number.isFinite(x.seconds));
  return {
    url: location.href, title: document.title,
    boardVisible: !!boardEl, boardSize, orientation, boardPieces, highlightedSquares,
    activeControls, resultVisible, resultText, clocks: clocks.slice(0, 6),
    players: players.slice(0, 8), domTurn,
    directMoves: directMoves.slice(0, 500),
    directMoveSelector: directMoves.length ? directMoves[0].selector : '',
    moveTimes: moveTimes.slice(0, 250),
    dataPlyNodes: dataPlyNodes.slice(0, 100),
    moveTexts: moveTexts.slice(0, 60),
    bodyHint: statusText.slice(0, 500)
  };
})();"""

CSV_FIELDS = [
    "event", "game_id", "game_url", "game_started_at", "ply", "side", "move",
    "time_taken_seconds_estimate", "move_time_seconds_dom", "time_source",
    "move_observed_at", "clock_1", "clock_2", "white_clock", "black_clock",
    "your_clock", "opponent_clock", "side_to_move", "white_player", "black_player",
    "move_count", "last_move", "game_status", "board_pieces_json",
    "highlighted_squares_json", "note"
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
    """Only accept a real game URL, not Chess.com lobby/home pages with a demo board."""
    try:
        parsed = urlsplit(url)
        path = parsed.path.rstrip("/")
        return (
            (parsed.hostname or "").lower().endswith("chess.com")
            and re.fullmatch(r"/game/(?:live/)?\d+", path) is not None
        )
    except Exception:
        return False


def _candidate_tokens(text: str) -> list[str]:
    cleaned = str(text or "").translate(FIGURINE_MAP)
    cleaned = re.sub(r"\b\d+\.(?:\.\.)?", " ", cleaned)
    return [token.strip() for token in SAN_PATTERN.findall(cleaned)]


def _legal_prefix(candidates: list[str]) -> list[str]:
    board = chess.Board()
    legal: list[str] = []
    for token in candidates:
        try:
            move = board.parse_san(token)
        except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError):
            break
        legal.append(board.san(move))
        board.push(move)
    return legal


def parse_moves(snapshot: dict) -> list[str]:
    """Prefer per-move notation nodes and validate them as one continuous game."""
    direct = snapshot.get("directMoves") or []
    if direct:
        board = chess.Board()
        direct_moves: list[str] = []
        failed = False
        for candidate in direct:
            found = _candidate_tokens(candidate.get("text", ""))
            if not found:
                continue
            for token in found:
                try:
                    move = board.parse_san(token)
                except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError):
                    failed = True
                    break
                direct_moves.append(board.san(move))
                board.push(move)
            if failed:
                break
        # If direct notation nodes begin at the start of the game, this is the trusted sequence.
        if len(direct_moves) >= 2 or (direct_moves and not snapshot.get("moveTexts")):
            return direct_moves

    best_generic: list[str] = []
    for candidate in snapshot.get("moveTexts", []):
        found = _candidate_tokens(candidate.get("text", ""))
        if not found:
            continue
        legal = _legal_prefix(found)
        if len(legal) > len(best_generic):
            best_generic = legal
    if direct:
        # Preserve a short, valid direct prefix rather than returning an unrelated parent-text parse.
        if len(direct_moves) >= len(best_generic):
            return direct_moves
    return best_generic

def get_clock_details(snapshot: dict) -> dict[str, str]:
    values = snapshot.get("clocks", [])
    result = {"clock_1": "", "clock_2": "", "white_clock": "", "black_clock": ""}
    ordered = []
    for item in values:
        value = str(item.get("value", "")).strip()
        if not value:
            continue
        ordered.append(value)
        side = str(item.get("side", "")).strip().lower()
        if side == "white" and not result["white_clock"]:
            result["white_clock"] = value
        elif side == "black" and not result["black_clock"]:
            result["black_clock"] = value
    result["clock_1"] = ordered[0] if ordered else ""
    result["clock_2"] = ordered[1] if len(ordered) > 1 else ""
    # Order-only clocks are intentionally not guessed to be White/Black.
    if not result["white_clock"] and len(ordered) == 1 and values[0].get("side", "") == "White":
        result["white_clock"] = ordered[0]
    return result


def get_turn(snapshot: dict, moves: list[str]) -> str:
    explicit = str(snapshot.get("domTurn", "")).strip()
    if explicit in ("White", "Black"):
        return explicit
    # Parity is a fallback only; it depends on a complete move list from move one.
    if moves:
        return "White" if len(moves) % 2 == 0 else "Black"
    return ""


def get_player_details(snapshot: dict) -> dict[str, str]:
    players = snapshot.get("players", [])
    result = {"white_player": "", "black_player": "", "your_side": ""}
    for player in players:
        name = str(player.get("name") or player.get("username") or "").strip()
        side = str(player.get("side", "")).strip()
        if side == "White" and name and not result["white_player"]:
            result["white_player"] = name
        elif side == "Black" and name and not result["black_player"]:
            result["black_player"] = name
        if player.get("isYou") and side in ("White", "Black"):
            result["your_side"] = side
    return result


def move_times_by_ply(snapshot: dict, total_moves: int) -> dict[int, float]:
    """Read only explicit time annotations tied to a data-ply node."""
    records = snapshot.get("moveTimes", [])
    found: dict[int, float] = {}
    numeric_plies = []
    for item in records:
        try:
            ply = int(str(item.get("ply", "")).strip())
        except (TypeError, ValueError):
            continue
        seconds = item.get("seconds")
        if seconds is None:
            continue
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            continue
        if 0 <= ply <= max(total_moves, 250):
            numeric_plies.append(ply)
            found[ply] = seconds
    # Some DOMs number data-ply from zero; shift only when the first mapped ply is zero.
    if numeric_plies and min(numeric_plies) == 0 and 1 not in found:
        found = {ply + 1: seconds for ply, seconds in found.items()}
    # Never assign a displayed duration to an unrelated ply based on list order.
    return {ply: seconds for ply, seconds in found.items() if 1 <= ply <= total_moves}


def clock_context(snapshot: dict, moves: list[str]) -> dict[str, str]:
    clocks = get_clock_details(snapshot)
    players = get_player_details(snapshot)
    turn = get_turn(snapshot, moves)
    your_clock = ""
    opponent_clock = ""
    if players["your_side"] == "White":
        your_clock, opponent_clock = clocks["white_clock"], clocks["black_clock"]
    elif players["your_side"] == "Black":
        your_clock, opponent_clock = clocks["black_clock"], clocks["white_clock"]
    return {
        **clocks,
        "your_clock": your_clock,
        "opponent_clock": opponent_clock,
        "side_to_move": turn,
        "white_player": players["white_player"],
        "black_player": players["black_player"],
    }


def board_json(snapshot: dict, key: str) -> str:
    try:
        return json.dumps(snapshot.get(key, []), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "[]"


def game_status(snapshot: dict) -> str:
    if snapshot.get("resultVisible"):
        return str(snapshot.get("resultText") or "finished")
    if snapshot.get("activeControls"):
        return "active"
    if snapshot.get("boardVisible"):
        return "board-visible"
    return "unknown"


def event_context(snapshot: dict, moves: list[str]) -> dict[str, str]:
    return {
        **clock_context(snapshot, moves),
        "move_count": str(len(moves)),
        "last_move": moves[-1] if moves else "",
        "game_status": game_status(snapshot),
        "board_pieces_json": board_json(snapshot, "boardPieces"),
        "highlighted_squares_json": board_json(snapshot, "highlightedSquares"),
    }


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
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("[START] SeleniumBase normal Chrome mode + read-only Chess.com tracker.")
    print("[INFO] Profile:", PROFILE_DIR)
    print("[INFO] CSV:", LOG_FILE)
    print("[INFO] Browser will open blank. Open Chess.com and start your game manually.")
    print("[INFO] DOM-only/read-only. Captures visible clocks, move annotations, players, turn, result, and piece metadata. No clicks or chess moves. Stop: Ctrl+C.")

    try:
        driver = Driver(
            browser="chrome",
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            uc=False,
        )
        driver.implicitly_wait(0)
    except Exception as exc:
        raise SystemExit(
            "SeleniumBase Chrome launch nahi kar paya. Try: "
            ".\\venv\\Scripts\\python.exe -m pip install -U seleniumbase\n"
            f"Details: {exc}"
        ) from exc

    active_game: dict | None = None
    last_url = ""
    last_moves: list[str] = []
    last_move_time = time.monotonic()
    last_clock_signature: tuple[str, ...] = ()
    last_wait_message = 0.0
    warned_move_list = False
    result_visible_streak = 0
    completed_game_urls: set[str] = set()
    last_debug_signature = ""
    last_clock_log_signature: tuple[str, ...] = ()
    last_clock_log_mono = 0.0

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
            clock_details = get_clock_details(snapshot)
            clocks = (clock_details["clock_1"], clock_details["clock_2"])
            now = now_iso()
            mono = time.monotonic()
            dom_move_times = move_times_by_ply(snapshot, len(moves))

            if snapshot.get("resultVisible"):
                result_visible_streak += 1
            else:
                result_visible_streak = 0

            if os.environ.get("CHESS_DOM_DEBUG") == "1":
                signature = repr({
                    "directSelector": snapshot.get("directMoveSelector"),
                    "directMoves": [x.get("text", "") for x in snapshot.get("directMoves", [])[:50]],
                    "moveTimes": snapshot.get("moveTimes", [])[:30],
                    "clocks": snapshot.get("clocks", []),
                    "players": snapshot.get("players", []),
                    "turn": snapshot.get("domTurn"),
                    "pieces": snapshot.get("boardPieces", [])[:8],
                })
                if signature != last_debug_signature:
                    print("[DOM DEBUG] structured visible DOM:", signature[:6000])
                    if snapshot.get("moveTexts"):
                        print("[DOM DEBUG] generic fallback:", repr([
                            (item.get("selector"), item.get("text", "")[:180])
                            for item in snapshot["moveTexts"][:8]
                        ]))
                    last_debug_signature = signature

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
                completed_game_urls.add(active_game["url"])
                print("[GAME END] Page changed.")
                active_game, last_moves = None, []
                result_visible_streak = 0

            last_url = url
            parsed = urlsplit(url)
            # A board on /play/online or another lobby is only a preview.
            # Since is_chess_page() accepts only /game/{id}, this is an actual game page.
            start_candidate = (
                url not in completed_game_urls
                and bool(re.fullmatch(r"/game/(?:live/)?\d+", parsed.path.rstrip("/")))
                and snapshot.get("boardVisible")
                and (
                    snapshot.get("activeControls")
                    or ticked
                    or (bool(moves) and len(snapshot.get("clocks", [])) >= 2)
                )
                and result_visible_streak == 0
            )

            if active_game and result_visible_streak >= 3:
                append_csv({
                    "event": "GAME_ENDED", "game_id": active_game["id"], "game_url": active_game["url"],
                    "game_started_at": active_game["started_at"], "move_observed_at": now, **event_context(snapshot, moves),
                    "note": "Explicit game-result container remained visible for 3 polls"
                })
                completed_game_urls.add(active_game["url"])
                print("[GAME END] Result stayed visible; game marked finished.")
                active_game, last_moves = None, []

            if active_game and (
                len(moves) < len(last_moves)
                or any(a != b for a, b in zip(last_moves, moves[:len(last_moves)]))
            ):
                append_csv({
                    "event": "GAME_ENDED", "game_id": active_game["id"], "game_url": active_game["url"],
                    "game_started_at": active_game["started_at"], "move_observed_at": now, **event_context(snapshot, moves),
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
                    "game_started_at": now, "move_observed_at": now, **event_context(snapshot, moves),
                    "note": "Actual game page detected from visible DOM"
                })
                ctx = event_context(snapshot, moves)
                print(f"[GAME DETECTED] {url} | existing moves: {len(moves)}")
                print(f"[BOARD] {ctx['white_player'] or 'White?'} ({ctx['white_clock'] or 'clock unknown'}) vs {ctx['black_player'] or 'Black?'} ({ctx['black_clock'] or 'clock unknown'}) | turn {ctx['side_to_move'] or 'unknown'} | pieces {len(snapshot.get('boardPieces', []))}")
                print(f"[DOM] direct move nodes: {len(snapshot.get('directMoves', []))} via {snapshot.get('directMoveSelector') or 'fallback'} | explicit move-time values: {len(snapshot.get('moveTimes', []))} | highlighted squares: {len(snapshot.get('highlightedSquares', []))}")
                for index, move in enumerate(moves, start=1):
                    append_csv({
                        "event": "HISTORY_MOVE", "game_id": active_game["id"], "game_url": url,
                        "game_started_at": now, "ply": index,
                        "side": "White" if index % 2 else "Black", "move": move,
                        "move_observed_at": now, **event_context(snapshot, moves),
                        "note": "Move already visible at detection; individual move duration unavailable"
                    })
                warned_move_list = False

            if active_game:
                ctx_now = event_context(snapshot, moves)
                live_signature = (
                    ctx_now["white_clock"], ctx_now["black_clock"], ctx_now["clock_1"], ctx_now["clock_2"],
                    ctx_now["side_to_move"], ctx_now["white_player"], ctx_now["black_player"],
                    ctx_now["move_count"], ctx_now["game_status"]
                )
                if live_signature != last_clock_log_signature and mono - last_clock_log_mono >= 0.9:
                    append_csv({
                        "event": "CLOCK", "game_id": active_game["id"], "game_url": active_game["url"],
                        "game_started_at": active_game["started_at"], "move_observed_at": now, **ctx_now,
                        "note": "Visible DOM clock/board snapshot; remaining time is the displayed clock value"
                    })
                    print(
                        f"[CLOCK] White {ctx_now['white_clock'] or ctx_now['clock_1'] or '?'} | "
                        f"Black {ctx_now['black_clock'] or ctx_now['clock_2'] or '?'} | "
                        f"you {ctx_now['your_clock'] or '?'} / opponent {ctx_now['opponent_clock'] or '?'} | "
                        f"turn {ctx_now['side_to_move'] or '?'} | moves {ctx_now['move_count']} | "
                        f"pieces {len(snapshot.get('boardPieces', []))}"
                    )
                    last_clock_log_signature = live_signature
                    last_clock_log_mono = mono

                if len(moves) > len(last_moves) and moves[:len(last_moves)] == last_moves:
                    added = moves[len(last_moves):]
                    c1, c2 = get_clocks(snapshot)
                    if len(added) == 1:
                        elapsed = round(max(0.0, mono - last_move_time), 2)
                        ply = len(last_moves) + 1
                        side = "White" if ply % 2 else "Black"
                        dom_seconds = dom_move_times.get(ply)
                        ctx = event_context(snapshot, moves)
                        time_source = "Chess.com DOM annotation" if dom_seconds is not None else "poll-interval estimate"
                        append_csv({
                            "event": "MOVE", "game_id": active_game["id"], "game_url": active_game["url"],
                            "game_started_at": active_game["started_at"], "ply": ply, "side": side,
                            "move": added[0],
                            "time_taken_seconds_estimate": "" if dom_seconds is not None else elapsed,
                            "move_time_seconds_dom": "" if dom_seconds is None else dom_seconds,
                            "time_source": time_source,
                            "move_observed_at": now, **ctx,
                            "note": "Exact displayed per-move duration from DOM" if dom_seconds is not None else "Displayed per-move duration not found; poll interval is only an estimate"
                        })
                        duration_label = f"DOM {dom_seconds:.2f}s" if dom_seconds is not None else f"~{elapsed:.2f}s estimate"
                        print(f"[MOVE] {side} {added[0]} | {duration_label} | White {ctx['white_clock'] or '?'} / Black {ctx['black_clock'] or '?'} | turn {ctx['side_to_move'] or '?'}")
                        last_move_time = mono
                    else:
                        for offset, move in enumerate(added, start=1):
                            ply = len(last_moves) + offset
                            dom_seconds = dom_move_times.get(ply)
                            ctx = event_context(snapshot, moves)
                            append_csv({
                                "event": "MOVE", "game_id": active_game["id"], "game_url": active_game["url"],
                                "game_started_at": active_game["started_at"], "ply": ply,
                                "side": "White" if ply % 2 else "Black", "move": move,
                                "move_time_seconds_dom": "" if dom_seconds is None else dom_seconds,
                                "time_source": "Chess.com DOM annotation" if dom_seconds is not None else "unknown (multiple moves between polls)",
                                "move_observed_at": now, **ctx,
                                "note": "Explicit DOM duration available" if dom_seconds is not None else "Multiple moves arrived between polls; individual duration unavailable"
                            })
                            duration_label = f"DOM {dom_seconds:.2f}s" if dom_seconds is not None else "time unknown"
                            print(f"[MOVE] {'White' if ply % 2 else 'Black'} {move} | {duration_label}")
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
