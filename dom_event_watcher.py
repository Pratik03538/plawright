"""Event-driven DOM/FEN watcher for a local or otherwise authorized chess test page.

The page may publish a complete FEN through a declared DOM attribute and can
optionally dispatch a 'chess:position' CustomEvent for every committed move.
This intentionally does not inspect network traffic or private backend endpoints.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import chess
from playwright.sync_api import sync_playwright


def build_observer_script(board_selector: str, fen_attribute: str, moves_selector: str) -> str:
    """Create an early-injected event listener plus MutationObserver fallback."""
    return f"""(() => {{
      const BOARD_SELECTOR = {json.dumps(board_selector)};
      const FEN_ATTRIBUTE = {json.dumps(fen_attribute)};
      const MOVES_SELECTOR = {json.dumps(moves_selector)};
      let lastKey = null;
      let observer = null;

      function emit(payload) {{
        if (!payload || !payload.fen) return;
        payload.url = payload.url || location.href;
        const key = [payload.fen, payload.ply || '', payload.lastMove || '', payload.actor || ''].join('|');
        if (key === lastKey) return;
        lastKey = key;
        if (typeof window.onChessDomUpdate === 'function') {{
          window.onChessDomUpdate(payload);
        }}
      }}

      function snapshot() {{
        const boardEl = document.querySelector(BOARD_SELECTOR);
        if (!boardEl) return;
        const fen = boardEl.getAttribute(FEN_ATTRIBUTE);
        if (!fen) return;
        emit({{
          fen,
          ply: boardEl.getAttribute('data-ply') || '',
          lastMove: boardEl.getAttribute('data-last-move') || '',
          actor: boardEl.getAttribute('data-actor') || '',
          moves: document.querySelector(MOVES_SELECTOR)?.textContent?.trim() || '',
          url: location.href
        }});
      }}

      // Controlled pages can emit one payload per committed move. This event
      // path preserves each supplied state even if several updates happen fast.
      document.addEventListener('chess:position', (event) => {{
        const detail = event.detail || {{}};
        emit({{
          fen: detail.fen,
          ply: detail.ply,
          lastMove: detail.lastMove,
          actor: detail.actor,
          moves: detail.moves || document.querySelector(MOVES_SELECTOR)?.textContent?.trim() || '',
          url: location.href
        }});
      }});

      function install() {{
        if (!document.documentElement) return;
        observer = new MutationObserver(snapshot);
        observer.observe(document.documentElement, {{
          subtree: true,
          childList: true,
          characterData: true,
          attributes: true,
          attributeFilter: [FEN_ATTRIBUTE, 'data-ply', 'data-last-move', 'data-actor']
        }});
        snapshot();
      }}

      if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', install, {{ once: true }});
      }} else {{
        install();
      }}
      window.__chessDomWatcherCleanup = () => observer?.disconnect();
    }})();"""


def main() -> None:
    default_page = Path(__file__).with_name("dom_event_test.html").resolve().as_uri()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=default_page, help="Local test HTML or an authorized page URL")
    parser.add_argument("--board-selector", default="[data-chess-board]", help="CSS selector for the board-state element")
    parser.add_argument("--fen-attribute", default="data-fen", help="DOM attribute that contains the complete FEN")
    parser.add_argument("--moves-selector", default="[data-move-list]", help="CSS selector for the visible move list")
    args = parser.parse_args()

    last_fen: str | None = None

    def on_dom_update(source, payload: dict) -> None:
        nonlocal last_fen
        fen = str(payload.get("fen", "")).strip()
        if not fen or fen == last_fen:
            return
        try:
            board = chess.Board(fen)
        except ValueError as exc:
            print(f"[DOM] Ignored invalid FEN: {exc}")
            return

        last_fen = fen
        actor = payload.get("actor") or "unknown"
        ply = payload.get("ply") or "?"
        last_move = payload.get("lastMove") or "—"
        moves = payload.get("moves") or "(move list not exposed)"
        print(f"\n[DOM EVENT] ply={ply} | actor={actor} | last_move={last_move}")
        print(f"[MOVE LIST] {moves}")
        print(f"[FEN] {board.fen()}")
        print("[SYNC] Valid chess position received; no screenshot/grid scan used.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        page = browser.new_page()
        page.expose_binding("onChessDomUpdate", on_dom_update)
        page.add_init_script(
            script=build_observer_script(
                args.board_selector,
                args.fen_attribute,
                args.moves_selector,
            )
        )
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            browser.close()
            raise SystemExit(f"Could not open {args.url}: {exc}") from exc

        print("[WATCHER] Listening for position events and DOM changes; no timed scan loop is used.")
        print("[WATCHER] In the local demo, click 'Apply next move' to trigger an event.")
        print("[WATCHER] Close the browser window or press Ctrl+C here to exit.")
        try:
            page.wait_for_event("close", timeout=0)
        except KeyboardInterrupt:
            pass
        finally:
            browser.close()


if __name__ == "__main__":
    main()
