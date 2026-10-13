"""Read-only check of the current Chess.com Computer tab via existing Chrome CDP.

Reports whether the bot appears to be loading, whether a chessboard is present,
piece squares exposed in the DOM, and any visible move-list text it can find.
It does not click, make moves, inspect private APIs, or close the user's browser.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

CDP_ENDPOINT = os.environ.get("CHROME_CDP_ENDPOINT", "http://127.0.0.1:9222")


SNAPSHOT_JS = r"""() => {
  const visible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
  };

  const bodyText = (document.body?.innerText || '').trim();
  const loadingMessages = bodyText.split(/\n+/).map(x => x.trim()).filter(Boolean).filter(
    x => /couldn't load the bot engine|loading this bot is taking|check your connection and reload/i.test(x)
  );

  const board = document.querySelector('wc-chess-board#board-play-computer, wc-chess-board.board, wc-chess-board');
  const boardVisible = visible(board);
  const pieces = board ? Array.from(board.querySelectorAll('.piece')).map(el => {
    const classes = Array.from(el.classList);
    const identity = classes.find(c => /^([wb][prnbqk])$/i.test(c)) || '';
    const square = classes.find(c => /^square-\d{2}$/.test(c)) || '';
    return {piece: identity, square: square.replace('square-', ''), className: el.className};
  }) : [];

  const moveSelectors = [
    'wc-vertical-move-list',
    '[class*="move-text-component"]',
    '[class*="vertical-move-list"]',
    '[class*="move-list"]',
    '[class*="movelist"]',
    '[class*="notation"]',
    '[data-cy*="move" i]',
    '[data-testid*="move" i]',
    '[class*="move-row"]',
    '[class*="move-number"]'
  ];
  const seen = new Set();
  const moveCandidates = [];
  for (const selector of moveSelectors) {
    let nodes = [];
    try { nodes = Array.from(document.querySelectorAll(selector)).slice(0, 30); } catch (_) {}
    for (const el of nodes) {
      if (seen.has(el) || !visible(el)) continue;
      seen.add(el);
      const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
      if (!text) continue;
      moveCandidates.push({
        tag: el.tagName.toLowerCase(),
        className: typeof el.className === 'string' ? el.className.slice(0, 140) : '',
        text: text.slice(0, 500)
      });
      if (moveCandidates.length >= 25) break;
    }
    if (moveCandidates.length >= 25) break;
  }

  const buttons = Array.from(document.querySelectorAll('button,[role="button"]'))
    .filter(visible)
    .slice(0, 80)
    .map(el => ({
      text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 70),
      aria: el.getAttribute('aria-label') || '',
      title: el.getAttribute('title') || '',
      disabled: !!el.disabled,
      className: typeof el.className === 'string' ? el.className.slice(0, 100) : ''
    }));

  const moveText = moveCandidates.map(x => x.text).join(' | ');
  const likelyMoveList = moveCandidates.find(x =>
    /(?:^|\s)\d{1,3}\.\s*(?:\.\.\.\s*)?(?:O-O|O-O-O|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8]|[a-h]x?[a-h][1-8])/i.test(x.text)
  );

  let gameStatus;
  if (/couldn't load the bot engine/i.test(bodyText)) {
    gameStatus = 'BOT_ENGINE_LOAD_ERROR';
  } else if (/loading this bot is taking/i.test(bodyText)) {
    gameStatus = 'BOT_STILL_LOADING';
  } else if (!boardVisible) {
    gameStatus = 'BOARD_NOT_VISIBLE';
  } else if (likelyMoveList) {
    gameStatus = 'MOVES_VISIBLE_GAME_LIKELY_STARTED';
  } else {
    gameStatus = 'BOARD_VISIBLE_BUT_START_NOT_CONFIRMED';
  }

  return {
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    boardPresent: !!board,
    boardVisible,
    boardSize: board ? (() => {
      const r = board.getBoundingClientRect();
      return [Math.round(r.width), Math.round(r.height)];
    })() : null,
    pieceCount: pieces.length,
    pieces,
    loadingMessages,
    gameStatus,
    moveCandidates,
    moveText,
    buttons
  };
}"""


def main() -> None:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_ENDPOINT, timeout=10000)
        except PlaywrightError as exc:
            raise SystemExit(
                f"[ERROR] Cannot connect to existing Chrome at {CDP_ENDPOINT}: {exc}\n"
                "In the same Chrome window open chrome://inspect/#remote-debugging, enable "
                "'Allow remote debugging for this browser instance', approve the prompt, "
                "and rerun. Leave Chrome open."
            ) from exc

        try:
            pages = [
                page
                for context in browser.contexts
                for page in context.pages
            ]
            page = next(
                (
                    p for p in reversed(pages)
                    if (urlsplit(p.url).hostname or "").endswith("chess.com")
                    and "/play/computer" in p.url
                ),
                None,
            )
            if page is None:
                print("[CHECK] No existing Chess.com Computer tab was found.")
                print("[CHECK] Open the Computer tab in the same Chrome instance, then rerun.")
                return

            try:
                snapshot = page.evaluate(SNAPSHOT_JS)
            except PlaywrightError as exc:
                print(f"[CHECK] Page was navigating while reading state: {exc}")
                print("[CHECK] Wait for the spinner/message to settle, then run this checker again.")
                return

            print("\n========== CHESS.COM GAME STATE CHECK ==========")
            print("URL:", snapshot["url"])
            print("PAGE:", snapshot["title"])
            print("STATUS:", snapshot["gameStatus"])
            print("BOARD PRESENT:", snapshot["boardPresent"])
            print("BOARD VISIBLE:", snapshot["boardVisible"])
            print("BOARD SIZE:", snapshot["boardSize"])
            print("PIECE COUNT:", snapshot["pieceCount"])
            print("BOT/LOADING MESSAGES:", snapshot["loadingMessages"] or "(none visible)")

            print("\n[PIECES READ FROM DOM]")
            if snapshot["pieces"]:
                for item in snapshot["pieces"]:
                    print(f"  {item['piece'] or '?':2}  {item['square'] or '--'}  ({item['className']})")
            else:
                print("  No .piece elements found.")

            print("\n[MOVE LIST CANDIDATES]")
            if snapshot["moveCandidates"]:
                for item in snapshot["moveCandidates"]:
                    print(f"  <{item['tag']}> {item['className']}: {item['text']}")
            else:
                print("  No visible move-list text found.")
            print("\n[MOVE TEXT DETECTED]:", snapshot["moveText"] or "(none)")

            print("\n[VISIBLE BUTTONS]")
            for item in snapshot["buttons"]:
                label = item["text"] or item["aria"] or item["title"]
                if label:
                    print(f"  {'DISABLED ' if item['disabled'] else ''}{label}")

            print("\n[NOTE] Read-only DOM check; no clicks or moves were sent.")
            print("================================================")
        finally:
            # This is the user's Chrome: disconnect, but do not close its context/tabs.
            pass


if __name__ == "__main__":
    main()
