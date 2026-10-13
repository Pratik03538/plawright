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
CHESSCOM_USERNAME = os.environ.get("CHESSCOM_USERNAME", "").strip().casefold()

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
        if (cur.parentElement) {
          cur = cur.parentElement;
        } else {
          const ownerRoot = cur.getRootNode && cur.getRootNode();
          cur = ownerRoot && ownerRoot.host ? ownerRoot.host : null;
        }
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
    try { pieces = Array.from(boardRoot.querySelectorAll('[class*="piece"]')).filter(visible).slice(0, 120); } catch (_) {}
    const bySquare = new Map();
    for (const el of pieces) {
      const cls = String(el.className || '');
      if (/\bdragging\b/i.test(cls)) continue;
      const tokens = cls.split(/\s+/);
      let color = '', type = '';
      for (const tok of tokens) {
        const m = tok.match(/^([wb])([kqrbnp])$/i);
        if (m) { color = m[1].toLowerCase() === 'w' ? 'White' : 'Black'; type = m[2].toUpperCase(); break; }
      }
      const squareClass = cls.match(/\bsquare-([1-8][1-8])\b/);
      const dataSquare = el.getAttribute('data-square') || '';
      const rawSquare = squareClass ? squareClass[1] : '';
      const mappedSquare = rawSquare
        ? String.fromCharCode(96 + Number(rawSquare[0])) + rawSquare[1]
        : '';
      const square = /^[a-h][1-8]$/.test(dataSquare) ? dataSquare : mappedSquare;
      if (color && type && square && !bySquare.has(square)) {
        bySquare.set(square, {
          color, piece: type, square, rawSquare,
          className: cls.slice(0, 120),
          ariaLabel: norm(el.getAttribute('aria-label') || '')
        });
      }
    }
    boardPieces.push(...bySquare.values());
    let highlights = [];
    try { highlights = Array.from(boardRoot.querySelectorAll('[class*="highlight"],[class*="check"]')).filter(visible).slice(0, 40); } catch (_) {}
    for (const el of highlights) {
      const cls = String(el.className || '');
      const sq = cls.match(/\bsquare-([1-8][1-8])\b/);
      const ds = el.getAttribute('data-square') || '';
      const rawSquare = sq ? sq[1] : '';
      const mappedSquare = rawSquare
        ? String.fromCharCode(96 + Number(rawSquare[0])) + rawSquare[1]
        : '';
      highlightedSquares.push({
        square: /^[a-h][1-8]$/.test(ds) ? ds : mappedSquare,
        rawSquare,
        className: cls.slice(0, 120)
      });
    }
  }

  let domTurn = '';
  const activeClockForTurn = clocks.find(item => /clock-player-turn/i.test(item.context || ''));
  if (activeClockForTurn && (activeClockForTurn.side === 'White' || activeClockForTurn.side === 'Black')) {
    domTurn = activeClockForTurn.side;
  }
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
    "move_observed_at", "clock_1", "clock_2", "top_clock", "bottom_clock",
    "white_clock", "black_clock", "your_clock", "opponent_clock", "side_to_move",
    "white_player", "black_player",
    "move_count", "last_move", "game_status", "board_orientation", "board_size",
    "board_pieces_json", "highlighted_squares_json", "note"
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


def _resolve_san_or_unique_destination(board: chess.Board, token: str) -> chess.Move | None:
    """Resolve SAN or a rendered token with its piece glyph omitted."""
    try:
        return board.parse_san(token)
    except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError):
        pass

    cleaned = token.strip()
    gives_check = cleaned.endswith("+") or cleaned.endswith("#")
    cleaned = cleaned.rstrip("+#")
    dest_match = re.search(r"([a-h][1-8])$", cleaned)
    if not dest_match:
        return None
    destination = chess.parse_square(dest_match.group(1))
    wants_capture = "x" in cleaned
    candidates = []
    for move in board.legal_moves:
        if move.to_square != destination or board.is_capture(move) != wants_capture:
            continue
        if gives_check and not board.gives_check(move):
            continue
        candidates.append(move)
    return candidates[0] if len(candidates) == 1 else None


def _legal_prefix(candidates: list[str]) -> list[str]:
    board = chess.Board()
    legal: list[str] = []
    for token in candidates:
        move = _resolve_san_or_unique_destination(board, token)
        if move is None:
            break
        legal.append(board.san(move))
        board.push(move)
    return legal

def parse_moves(snapshot: dict) -> list[str]:
    """Prefer individual notation nodes; resolve missing piece glyphs only if unambiguous."""
    direct = snapshot.get("directMoves") or []
    direct_moves: list[str] = []
    direct_failed = False
    if direct:
        board = chess.Board()
        for candidate in direct:
            found = _candidate_tokens(candidate.get("text", ""))
            for token in found:
                move = _resolve_san_or_unique_destination(board, token)
                if move is None:
                    direct_failed = True
                    break
                direct_moves.append(board.san(move))
                board.push(move)
            if direct_failed:
                break

    best_generic: list[str] = []
    for candidate in snapshot.get("moveTexts", []):
        found = _candidate_tokens(candidate.get("text", ""))
        if not found:
            continue
        legal = _legal_prefix(found)
        if len(legal) > len(best_generic):
            best_generic = legal

    if direct_moves and not direct_failed and len(direct_moves) >= len(best_generic):
        return direct_moves
    return direct_moves if len(direct_moves) > len(best_generic) else best_generic

def get_clocks(snapshot: dict) -> tuple[str, str]:
    """Backwards-compatible ordered clock values for polling-change detection."""
    clocks = get_clock_details(snapshot)
    return clocks["clock_1"], clocks["clock_2"]


def get_clock_details(snapshot: dict) -> dict[str, str]:
    values = snapshot.get("clocks", [])
    result = {
        "clock_1": "", "clock_2": "",
        "top_clock": "", "bottom_clock": "",
        "white_clock": "", "black_clock": ""
    }
    ordered = []
    for item in values:
        value = str(item.get("value", "")).strip()
        if not value:
            continue
        ordered.append(value)
        side = str(item.get("side", "")).strip().lower()
        context = str(item.get("context", "")).lower()
        if "clock-top" in context or "player-top" in context:
            result["top_clock"] = value
        elif "clock-bottom" in context or "player-bottom" in context:
            result["bottom_clock"] = value
        if side == "white" and not result["white_clock"]:
            result["white_clock"] = value
        elif side == "black" and not result["black_clock"]:
            result["black_clock"] = value

    result["clock_1"] = ordered[0] if ordered else ""
    result["clock_2"] = ordered[1] if len(ordered) > 1 else ""
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
        username = str(player.get("username") or "").strip().casefold()
        side = str(player.get("side", "")).strip()
        if side == "White" and name and not result["white_player"]:
            result["white_player"] = name
        elif side == "Black" and name and not result["black_player"]:
            result["black_player"] = name
        username_matches = bool(CHESSCOM_USERNAME and username == CHESSCOM_USERNAME)
        if side in ("White", "Black") and (player.get("isYou") or username_matches):
            result["your_side"] = side

    # Bottom clock belongs to the local player when active-game controls are visible.
    if not result["your_side"] and snapshot.get("activeControls"):
        for clock in snapshot.get("clocks", []):
            context = str(clock.get("context", "")).lower()
            if "player-bottom" in context or "clock-bottom" in context:
                side = str(clock.get("side", ""))
                if side in ("White", "Black"):
                    result["your_side"] = side
                    break
    return result

def move_times_by_ply(snapshot: dict, total_moves: int) -> dict[int, float]:
    """Read explicit per-ply DOM times, then the durations printed in move-list rows."""
    found: dict[int, float] = {}
    numeric_plies = []
    for item in snapshot.get("moveTimes", []):
        try:
            ply = int(str(item.get("ply", "")).strip())
            seconds = float(item.get("seconds"))
        except (TypeError, ValueError):
            continue
        if 0 <= ply <= max(total_moves, 250):
            numeric_plies.append(ply)
            found[ply] = seconds
    if numeric_plies and min(numeric_plies) == 0 and 1 not in found:
        found = {ply + 1: seconds for ply, seconds in found.items()}

    candidates = [
        item for item in snapshot.get("moveTexts", [])
        if item.get("text")
        and re.search(r"(?<!\S)\d+\.\s+", item.get("text", ""))
        and re.search(r"(?<![\w.])\d+(?:[.,]\d+)?s\b", item.get("text", ""), re.I)
    ]
    if candidates:
        full_list = max(
            candidates,
            key=lambda item: (item.get("selector") == "wc-simple-move-list", len(item.get("text", "")))
        )
        text = str(full_list.get("text", ""))
        headings = list(re.finditer(r"(?<!\S)(\d+)\.\s+", text))
        for index, heading in enumerate(headings):
            try:
                move_number = int(heading.group(1))
            except ValueError:
                continue
            if move_number < 1 or move_number > 250:
                continue
            row_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            row = text[heading.end():row_end].strip()
            times = [
                float(m.group(1).replace(",", "."))
                for m in re.finditer(r"(?<![\w.])(\d+(?:[.,]\d+)?)s\b", row, re.I)
            ]
            if not times:
                continue
            first_time = re.search(r"(?<![\w.])\d+(?:[.,]\d+)?s\b", row, re.I)
            moves_part = row[:first_time.start()] if first_time else row
            tokens = _candidate_tokens(moves_part)
            white_ply = (move_number - 1) * 2 + 1
            black_ply = white_ply + 1
            if tokens and len(times) >= 1:
                found.setdefault(white_ply, times[0])
            if len(tokens) >= 2 and len(times) >= 2:
                found.setdefault(black_ply, times[1])

    return {ply: seconds for ply, seconds in found.items() if 1 <= ply <= total_moves}

def clock_context(snapshot: dict, moves: list[str]) -> dict[str, str]:
    clocks = get_clock_details(snapshot)
    players = get_player_details(snapshot)
    turn = get_turn(snapshot, moves)
    # For this layout the opponent is at the top and the local player is at the bottom.
    your_clock = clocks["bottom_clock"]
    opponent_clock = clocks["top_clock"]
    if not players["your_side"]:
        for item in snapshot.get("clocks", []):
            context = str(item.get("context", "")).lower()
            if ("clock-bottom" in context or "player-bottom" in context) and item.get("side") in ("White", "Black"):
                players["your_side"] = str(item.get("side"))
                break
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
        "board_orientation": str(snapshot.get("orientation") or ""),
        "board_size": json.dumps(snapshot.get("boardSize") or []),
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
    print("[START] Read-only Chess.com tracker.")
    print("[CSV]", LOG_FILE)
    print("[WAIT] Open a Chess.com game in the Chrome window. Stop with Ctrl+C.")

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
                if time.monotonic() - last_wait_message > 30:
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

            if os.environ.get("CHESS_DOM_DEBUG", "").lower() == "verbose":
                ctx_dbg = event_context(snapshot, moves)
                signature = repr({
                    "directCount": len(snapshot.get("directMoves", [])),
                    "directTail": [x.get("text", "") for x in snapshot.get("directMoves", [])[-8:]],
                    "parsedMoves": len(moves),
                    "timeValues": len(dom_move_times),
                    "clockSides": [x.get("side", "") for x in snapshot.get("clocks", [])],
                    "playerNames": [x.get("username") or x.get("name") for x in snapshot.get("players", [])[:3]],
                    "turn": ctx_dbg["side_to_move"],
                    "pieceCount": len(snapshot.get("boardPieces", [])),
                })
                if signature != last_debug_signature:
                    print(
                        f"[DOM] parsed={len(moves)} direct={len(snapshot.get('directMoves', []))} "
                        f"times={len(dom_move_times)} clocks=W:{ctx_dbg['white_clock'] or '?'} "
                        f"B:{ctx_dbg['black_clock'] or '?'} turn={ctx_dbg['side_to_move'] or '?'} "
                        f"pieces={len(snapshot.get('boardPieces', []))}"
                    )
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
                result_label = str(snapshot.get("resultText") or "result text not exposed by DOM").replace("\\n", " ").strip()[:120]
                print(f"[GAME END] {result_label}")
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
                print(f"[BOARD] You-bottom {ctx['your_clock'] or '?'} | Opponent-top {ctx['opponent_clock'] or '?'} | White {ctx['white_clock'] or '?'} / Black {ctx['black_clock'] or '?'} | turn {ctx['side_to_move'] or 'unknown'} | pieces {len(snapshot.get('boardPieces', []))}")
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
                        print(f"[MOVE] {side} {added[0]} | {duration_label} | You {ctx['your_clock'] or '?'} / Opp {ctx['opponent_clock'] or '?'} | turn {ctx['side_to_move'] or '?'}")
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

            elif time.monotonic() - last_wait_message > 30:
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
