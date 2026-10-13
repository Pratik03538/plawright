"""Live chess DOM/API tracker with optional Stockfish for an authorized test site.

Uses Chrome DevTools Protocol directly, so Selenium/ChromeDriver attachment is not needed.
Reads exposed board.game methods and reconstructs moves from FEN transitions.
Autoplay is opt-in and blocked for known public competitive chess hosts.
No site URL, hostname, page title, or player name is written to terminal/CSV.
"""
from __future__ import annotations

import argparse
import csv
import json
import ipaddress
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

try:
    import websocket
    from websocket import WebSocketTimeoutException
except ImportError as exc:
    raise SystemExit("Install dependency: python -m pip install websocket-client") from exc

try:
    import chess
    import chess.engine
except ImportError:
    chess = None


BASE_DIR = Path(__file__).resolve().parent
SETTINGS_CANDIDATES = (
    BASE_DIR / "tracker_settings.txt",
    Path.cwd() / "tracker_settings.txt",
    BASE_DIR / "site_settings.txt",
    Path.cwd() / "site_settings.txt",
    BASE_DIR / "bot_settings.txt",
    Path.cwd() / "bot_settings.txt",
)
CSV_FIELDS = [
    "timestamp_utc", "event", "fen", "turn", "playing_as", "my_turn",
    "bottom_side", "move_count", "last_move_san", "last_move_uci",
    "legal_move_count", "result", "note",
]


def read_settings() -> dict[str, str]:
    values: dict[str, str] = {}
    seen: set[str] = set()
    for path in SETTINGS_CANDIDATES:
        if str(path.resolve()) in seen or not path.is_file():
            continue
        seen.add(str(path.resolve()))
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip().lower()] = value.strip().strip('"').strip("'")
        if values.get("site_domain") or values.get("allowed_domain"):
            break
    return values


def normalize_host(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    parsed = urlsplit(value if "://" in value else "//" + value)
    host = (parsed.hostname or "").strip(".").removeprefix("www.")
    if not host or any(not x for x in host.split(".")):
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if host == "localhost":
        return host
    if len(host.split(".")) < 2:
        return ""
    if host in {
        "com", "org", "net", "edu", "gov", "in", "co.in", "org.in",
        "uk", "co.uk", "org.uk", "io", "dev", "app", "com.au",
    }:
        return ""
    return host


def host_of(url: str | None) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".").removeprefix("www.")
    except ValueError:
        return ""


def is_public_competitive_host(host: str) -> bool:
    roots = ("chess.com", "lichess.org", "chess24.com", "chessclub.com")
    host = (host or "").lower().rstrip(".").removeprefix("www.")
    return any(host == root or host.endswith("." + root) for root in roots)


def target_allowed(url: str | None, allowed_host: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = host_of(url)
    return (
        parsed.scheme in ("http", "https")
        and bool(host)
        and not is_public_competitive_host(host)
        and (host == allowed_host or host.endswith("." + allowed_host))
    )


def load_allowed_host(settings: dict[str, str], cli_value: str) -> tuple[str, str]:
    host = normalize_host(cli_value or settings.get("site_domain", "") or settings.get("allowed_domain", ""))
    if not host:
        host = normalize_host(settings.get("site_url", "") or settings.get("url", ""))
    source = "command-line/TXT settings"
    if not host:
        return "", source
    return host, source


class CDP:
    def __init__(self, address: str):
        self.root = "http://" + address.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
        self.ws = None
        self.target_id = ""
        self.next_id = 0
        self.timeout = 0.5

    def targets(self) -> list[dict]:
        with urlopen(self.root + "/json/list", timeout=3) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
        return [x for x in data if isinstance(x, dict) and x.get("type") == "page" and x.get("id")]

    def attach(self, target_id: str) -> None:
        target = next((t for t in self.targets() if str(t.get("id")) == str(target_id)), None)
        if not target or not str(target.get("webSocketDebuggerUrl", "")).startswith("ws"):
            raise RuntimeError("The selected tab has no DevTools socket.")
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = websocket.create_connection(
            target["webSocketDebuggerUrl"],
            timeout=3,
            suppress_origin=True,
            http_no_proxy=["127.0.0.1", "localhost", "::1"],
            enable_multithread=True,
        )
        self.ws.settimeout(self.timeout)
        self.target_id = str(target_id)

    def command(self, method: str, params: dict | None = None, timeout: float = 8) -> dict:
        if not self.ws:
            raise RuntimeError("No browser tab is attached.")
        self.next_id += 1
        cid = self.next_id
        self.ws.send(json.dumps({"id": cid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = self.ws.recv()
            except WebSocketTimeoutException:
                continue
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != cid:
                continue
            if "error" in msg:
                raise RuntimeError("Chrome rejected a DevTools command.")
            return msg.get("result", {})
        raise RuntimeError("Chrome DevTools command timed out.")

    def evaluate(self, expression: str, args: list | None = None):
        expression = "(function(){\n" + expression + "\n}).apply(null," + json.dumps(args or []) + ")"
        result = self.command("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
            "userGesture": True,
        })
        if result.get("exceptionDetails"):
            raise RuntimeError("Page-side JavaScript evaluation failed.")
        return result.get("result", {}).get("value")

    def close(self) -> None:
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None


INSTALL_JS = r"""
const cfg = arguments[0];
try { window.__liveGameReader?.observer?.disconnect(); } catch (_) {}

function rootsDeep() {
  const roots = [document], seen = new Set(roots);
  for (let i = 0; i < roots.length && i < 24; i++) {
    let els = [];
    try { els = roots[i].querySelectorAll('*'); } catch (_) {}
    for (const el of els) {
      try {
        if (el.shadowRoot && !seen.has(el.shadowRoot)) {
          seen.add(el.shadowRoot);
          roots.push(el.shadowRoot);
        }
      } catch (_) {}
    }
  }
  return roots;
}
function deepOne(selector) {
  if (!selector) return null;
  for (const root of rootsDeep()) {
    try { const e = root.querySelector(selector); if (e) return e; } catch (_) {}
  }
  return null;
}
function clean(v) { return String(v || '').replace(/\s+/g, ' ').trim(); }
function call(obj, method) {
  try { return obj && typeof obj[method] === 'function' ? obj[method]() : null; }
  catch (_) { return null; }
}
function getBoard() {
  return deepOne(cfg.boardSelector) || deepOne('chess-board') ||
         deepOne('wc-chess-board') || deepOne('#board-single');
}
function readSnapshot() {
  const el = getBoard();
  const game = el && el.game;
  const fenFromApi = call(game, 'getFEN');
  const fenNode = deepOne(cfg.fenSelector);
  const fen = typeof fenFromApi === 'string' && fenFromApi
    ? fenFromApi.trim()
    : (fenNode ? clean(fenNode.getAttribute(cfg.fenAttribute)) : '');
  let bottomSide = '';
  const coords = deepOne('.coordinates');
  const firstCoord = coords && coords.children && coords.children.length
    ? clean(coords.children[0].textContent) : '';
  if (firstCoord === '1' || firstCoord.toLowerCase() === 'a') bottomSide = 'white';
  else if (firstCoord === '8' || firstCoord.toLowerCase() === 'h') bottomSide = 'black';
  if (!bottomSide && el) {
    const cls = clean(el.className);
    if (/\bflipped\b|\borientation-black\b/i.test(cls)) bottomSide = 'black';
    else if (/\borientation-white\b/i.test(cls)) bottomSide = 'white';
  }
  const legal = call(game, 'getLegalMoves');
  let moves = [];
  for (const sel of ['.white.node', '.black.node', '[data-ply]', '.main-line-ply']) {
    const found = [];
    for (const root of rootsDeep()) {
      try {
        for (const n of root.querySelectorAll(sel)) {
          const t = clean(n.innerText || n.textContent);
          if (t && t.length <= 32 && !/^\d+(\.\d+)?s$/i.test(t)) found.push(t);
        }
      } catch (_) {}
    }
    if (found.length) { moves = Array.from(new Set(found)); break; }
  }
  const resultNode = deepOne(cfg.resultSelector);
  return {
    boardFound: !!el,
    gameApiFound: !!(game && typeof game.getFEN === 'function' && typeof game.getLegalMoves === 'function'),
    apiMethods: game ? ['getFEN', 'getTurn', 'getPlayingAs', 'getLegalMoves'].filter(k => typeof game[k] === 'function') : [],
    fen,
    turn: call(game, 'getTurn'),
    playingAs: call(game, 'getPlayingAs'),
    bottomSide,
    legalMoveCount: Array.isArray(legal) ? legal.length : 0,
    legalMoveUci: Array.isArray(legal) ? legal.map(m => ({
      from: m.from, to: m.to, promotion: m.promotion || null
    })) : [],
    domMoves: moves,
    result: resultNode ? clean(resultNode.innerText || resultNode.textContent).slice(0, 180) : ''
  };
}
function playUci(uci, expectedFen) {
  const board = getBoard(), game = board && board.game;
  if (!game || typeof game.getFEN !== 'function' || typeof game.getLegalMoves !== 'function' || typeof game.move !== 'function') {
    return {ok:false, reason:'game-move-api-unavailable'};
  }
  if (clean(game.getFEN()) !== String(expectedFen || '').trim()) {
    return {ok:false, reason:'stale-position'};
  }
  const text = String(uci || '');
  const from = text.slice(0, 2), to = text.slice(2, 4);
  const promo = text.length > 4 ? text[4].toLowerCase() : '';
  const legal = game.getLegalMoves();
  const move = Array.isArray(legal) ? legal.find(m =>
    m.from === from && m.to === to &&
    (!promo || String(m.promotion || '').toLowerCase() === promo)
  ) : null;
  if (!move) return {ok:false, reason:'move-not-in-legal-list'};
  try {
    game.move({...move, animate:false, userGenerated:true});
    return {ok:true};
  } catch (_) {
    return {ok:false, reason:'game-api-rejected-move'};
  }
}
const state = {observer:null, lastSignature:''};
function refresh() {
  try {
    const snap = readSnapshot();
    const sig = JSON.stringify(snap);
    if (sig !== state.lastSignature) state.lastSignature = sig;
  } catch (_) {}
}
state.observer = new MutationObserver(refresh);
try {
  state.observer.observe(document.documentElement, {
    subtree:true, childList:true, characterData:true, attributes:true,
    attributeFilter:['class','aria-label','data-fen','data-ply','data-turn']
  });
} catch (_) {}
window.__liveGameReader = {
  observer:state.observer,
  current:readSnapshot,
  playUci:playUci,
  stop:() => { try { state.observer?.disconnect(); } catch (_) {} }
};
return readSnapshot();
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def read_setting_float(settings: dict[str, str], key: str, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(settings.get(key, default))))
    except (TypeError, ValueError):
        return default


def read_setting_int(settings: dict[str, str], key: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(settings.get(key, default))))
    except (TypeError, ValueError):
        return default


def is_start_position(fen: str) -> bool:
    if chess is None:
        return False
    try:
        board = chess.Board(fen)
        return (
            board.board_fen() == chess.STARTING_BOARD_FEN
            and board.turn == chess.WHITE
            and board.fullmove_number == 1
            and board.halfmove_clock == 0
        )
    except Exception:
        return False

def same_position(board: "chess.Board", fen: str) -> bool:
    try:
        other = chess.Board(fen)
        return board.board_fen() == other.board_fen() and board.turn == other.turn
    except Exception:
        return False


def reconstruct_transition(old_fen: str, new_fen: str):
    if chess is None:
        return None
    try:
        board = chess.Board(old_fen)
        target = chess.Board(new_fen)
    except Exception:
        return None
    for move in board.legal_moves:
        san = board.san(move)
        candidate = board.copy()
        candidate.push(move)
        if same_position(candidate, new_fen):
            return {"san": san, "uci": move.uci()}
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Private-output live DOM/API tracker")
    parser.add_argument("--debugger-address", default="127.0.0.1:9223")
    parser.add_argument("--allowed-domain", default="")
    parser.add_argument("--board-selector", default="chess-board")
    parser.add_argument("--fen-selector", default="")
    parser.add_argument("--fen-attribute", default="data-fen")
    parser.add_argument("--csv", default=str(BASE_DIR / "live_dom_events_private.csv"))
    parser.add_argument("--poll-seconds", type=float, default=0.20)
    parser.add_argument("--auto-play", action="store_true", default=None)
    parser.add_argument("--no-auto-play", action="store_true")
    parser.add_argument("--stockfish-path", default="")
    parser.add_argument("--engine-time", type=float, default=None)
    parser.add_argument("--engine-depth", type=int, default=None)
    parser.add_argument("--multipv", type=int, default=None)
    parser.add_argument("--move-delay", type=float, default=None)
    args = parser.parse_args()

    settings = read_settings()
    allowed_host, _ = load_allowed_host(settings, args.allowed_domain)
    if not allowed_host:
        print("[CONFIG ERROR] Site hostname missing/invalid in TXT settings; value hidden.", file=sys.stderr)
        print("Set site_domain to the full hostname in tracker_settings.txt.", file=sys.stderr)
        return 2

    auto_setting = settings.get("auto_play", "").strip().lower() in {"1", "true", "yes", "on", "enabled"}
    auto_play = False if args.no_auto_play else (args.auto_play if args.auto_play is not None else auto_setting)
    engine_time = max(0.05, min(5.0, args.engine_time if args.engine_time is not None else read_setting_float(settings, "engine_time", 0.20, 0.05, 5.0)))
    engine_depth = max(1, min(40, args.engine_depth if args.engine_depth is not None else read_setting_int(settings, "engine_depth", 12, 1, 40)))
    multipv = max(1, min(16, args.multipv if args.multipv is not None else read_setting_int(settings, "multipv", 8, 1, 16)))
    move_delay = max(0.0, min(2.0, args.move_delay if args.move_delay is not None else read_setting_float(settings, "move_delay_seconds", 0.15, 0, 2)))

    print("[CONFIG] Settings loaded from TXT file.", flush=True)
    print("[CONNECT] Checking local Chrome DevTools endpoint...", flush=True)
    try:
        with urlopen("http://" + args.debugger_address + "/json/version", timeout=3) as res:
            info = json.loads(res.read().decode("utf-8", "replace"))
        print("[CONNECT] DevTools is online (" + str(info.get("Browser", "Chrome")) + ").", flush=True)
    except Exception:
        print("[CONNECT ERROR] Chrome DevTools endpoint is not responding.", file=sys.stderr, flush=True)
        return 2

    cdp = CDP(args.debugger_address)
    try:
        targets = cdp.targets()
    except Exception:
        print("[CONNECT ERROR] Could not list Chrome tabs. Restart debugging Chrome and retry.", file=sys.stderr, flush=True)
        return 2
    matches = []
    print("[TABS] Browser tabs (details hidden):", flush=True)
    for index, target in enumerate(targets):
        match = target_allowed(target.get("url"), allowed_host)
        print("  " + str(index) + ": " + ("matches settings" if match else "not matching"), flush=True)
        if match:
            matches.append((index, target))
    if not matches:
        print("[ERROR] No eligible tab matches saved site settings.", file=sys.stderr, flush=True)
        return 2
    valid_indexes = {i for i, _ in matches}
    print("[TABS] Select your configured test-game tab number:", flush=True)
    while True:
        choice = input("> ").strip()
        if choice.isdigit() and int(choice) in valid_indexes:
            target = next(t for i, t in matches if i == int(choice))
            break
        print("Choose one of: " + str(sorted(valid_indexes)), flush=True)
    try:
        cdp.attach(str(target["id"]))
        cfg = {
            "boardSelector": args.board_selector,
            "fenSelector": args.fen_selector,
            "fenAttribute": args.fen_attribute,
        }
        initial = cdp.evaluate(INSTALL_JS, [cfg])
        print("[CONNECTED] Selected browser tab.", flush=True)
        print(
            "[BOARD] found=" + str(initial.get("boardFound")) +
            " | game API=" + str(initial.get("gameApiFound")) +
            " | turn=" + str(initial.get("turn")) +
            " | playingAs=" + str(initial.get("playingAs")) +
            " | bottomSide=" + str(initial.get("bottomSide") or "unknown") +
            " | legalMoves=" + str(initial.get("legalMoveCount", 0)),
            flush=True,
        )
        print("[FEN] " + str(initial.get("fen") or "not exposed"), flush=True)

        engine = None
        if auto_play:
            if chess is None:
                raise RuntimeError("Autoplay requires python-chess.")
            if is_public_competitive_host(allowed_host):
                raise RuntimeError("Autoplay disabled for public competitive chess sites.")
            path_text = args.stockfish_path or settings.get("stockfish_path", "stockfish.exe")
            engine_path = Path(path_text).expanduser()
            if not engine_path.is_absolute():
                engine_path = (BASE_DIR / engine_path).resolve()
            if not engine_path.is_file():
                raise RuntimeError("Stockfish executable not found; configure stockfish_path in TXT.")
            engine = chess.engine.SimpleEngine.popen_uci(str(engine_path))
            print("[AUTO] Stockfish enabled for the configured authorized test site.", flush=True)
            print("[AUTO] Search=" + str(engine_time) + "s | depth=" + str(engine_depth) + " | MultiPV=" + str(multipv), flush=True)

        out_path = Path(args.csv).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not out_path.exists() or out_path.stat().st_size == 0
        fields = CSV_FIELDS
        previous_fen = ""
        history: list[str] = []
        game_number = 1
        last_auto_fen = ""
        last_auto_success_fen = ""
        previous_auto_submitted_at = 0.0
        poll_seconds = max(0.05, min(2.0, args.poll_seconds))

        with out_path.open("a", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            if fresh:
                writer.writeheader()
            print("[MODE] Direct CDP + game API reader active; Ctrl+C to stop.", flush=True)
            while True:
                current_url = cdp.targets()
                selected = next((t for t in current_url if str(t.get("id")) == str(target["id"])), None)
                if not selected:
                    print("[STOP] Selected browser tab is gone.", flush=True)
                    break
                if not target_allowed(selected.get("url"), allowed_host):
                    print("[STOP] Selected tab is no longer eligible.", flush=True)
                    break
                snap = cdp.evaluate("return window.__liveGameReader ? window.__liveGameReader.current() : null;")
                if not isinstance(snap, dict):
                    print("[WARN] Game state API is temporarily unavailable.", flush=True)
                    time.sleep(poll_seconds)
                    continue
                fen = str(snap.get("fen") or "").strip()
                last_san = ""
                last_uci = ""
                if fen and chess is not None:
                    if previous_fen and fen != previous_fen:
                        if is_start_position(fen):
                            game_number += 1
                            history = []
                            print("[GAME] New game/reset detected.", flush=True)
                        else:
                            transition = reconstruct_transition(previous_fen, fen)
                            if transition:
                                last_san, last_uci = transition["san"], transition["uci"]
                                history.append(last_san)
                                print("[MOVE] " + last_san + " (" + last_uci + ")", flush=True)
                                print("[HISTORY] " + " ".join(history[-24:]), flush=True)
                            else:
                                print("[MOVE] FEN changed; move could not be reconstructed legally.", flush=True)
                    try:
                        board_for_position = chess.Board(fen)
                    except Exception:
                        board_for_position = None

                    if auto_play and engine and board_for_position and fen != last_auto_fen:
                        turn = snap.get("turn")
                        playing_as = snap.get("playingAs")
                        # The exposed game's own API defines ownership reliably by equality;
                        # bottom orientation is logged separately and never assumed to be a color mapping.
                        our_turn = turn is not None and playing_as is not None and str(turn) == str(playing_as)
                        if our_turn and not board_for_position.is_game_over(claim_draw=True):
                            last_auto_fen = fen
                            try:
                                result = engine.analyse(
                                    board_for_position,
                                    chess.engine.Limit(time=engine_time, depth=engine_depth),
                                    multipv=multipv,
                                )
                                best = result[0] if isinstance(result, list) and result else result
                                pv = best.get("pv", []) if isinstance(best, dict) else []
                                chosen = pv[0] if pv else None
                                if chosen is not None:
                                    san = board_for_position.san(chosen)
                                    if move_delay:
                                        time.sleep(move_delay)
                                    live = cdp.evaluate("return window.__liveGameReader ? window.__liveGameReader.current() : null;")
                                    if not isinstance(live, dict) or live.get("fen") != fen:
                                        print("[AUTO] Position changed during search; stale move discarded.", flush=True)
                                    else:
                                        submitted = cdp.evaluate(
                                            "return window.__liveGameReader ? window.__liveGameReader.playUci(arguments[0], arguments[1]) : {ok:false,reason:'reader-missing'};",
                                            [chosen.uci(), fen],
                                        )
                                        if isinstance(submitted, dict) and submitted.get("ok"):
                                            previous_auto_submitted_at = time.monotonic()
                                            last_auto_success_fen = fen
                                            print("[AUTO] Submitted " + san + " (" + chosen.uci() + "); waiting for FEN confirmation.", flush=True)
                                        else:
                                            why = submitted.get("reason", "submission-failed") if isinstance(submitted, dict) else "submission-failed"
                                            print("[AUTO] Submission failed: " + str(why), flush=True)
                            except Exception:
                                print("[AUTO] Search/submission failed; will wait for next state.", flush=True)

                my_turn = snap.get("turn") is not None and snap.get("playingAs") is not None and str(snap.get("turn")) == str(snap.get("playingAs"))
                row = {
                    "timestamp_utc": utc_now(),
                    "event": "STATE",
                    "fen": fen,
                    "turn": snap.get("turn", ""),
                    "playing_as": snap.get("playingAs", ""),
                    "my_turn": my_turn,
                    "bottom_side": snap.get("bottomSide") or "",
                    "move_count": len(history),
                    "last_move_san": last_san,
                    "last_move_uci": last_uci,
                    "legal_move_count": snap.get("legalMoveCount", 0),
                    "result": str(snap.get("result") or ""),
                    "note": "FEN transition" if last_san else "",
                }
                writer.writerow(row)
                handle.flush()
                previous_fen = fen or previous_fen
                time.sleep(poll_seconds)

    except KeyboardInterrupt:
        print("\n[STOP] Reader stopped.", flush=True)
    except Exception:
        print("[ERROR] Browser/API/Stockfish operation failed. Check local configuration; site details were not logged.", file=sys.stderr, flush=True)
        return 2
    finally:
        try:
            if 'engine' in locals() and engine:
                engine.quit()
        except Exception:
            pass
        cdp.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
