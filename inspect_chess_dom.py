"""Inspect visible DOM candidates on a local or authorized chess test page.

This script does not inspect network traffic or hidden/private endpoints. It uses
bounded DOM queries so large pages are not exhaustively traversed.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
# Use branded Chrome in its own profile (never point at your everyday Chrome profile).
# Override with CHESS_BROWSER_CHANNEL=chromium if you want the bundled Playwright build.
BROWSER_CHANNEL = os.environ.get("CHESS_BROWSER_CHANNEL", "chrome").strip() or "chrome"
PROFILE_DIR = BASE_DIR / "playwright_dom_inspector_chrome_profile"
TARGET_URL = os.environ.get("CHESS_BOT_URL", "https://www.chess.com/play/computer")

DOM_SNAPSHOT_JS = r"""() => {
  const selectors = [
    'wc-chess-board', 'chess-board', 'chess-board-view',
    'canvas',
    '[data-cy*="board" i]', '[data-testid*="board" i]', '[aria-label*="board" i]',
    '[class*="board" i]', '[class*="piece" i]', '[class*="square" i]',
    '[data-cy*="move" i]', '[data-testid*="move" i]',
    '[class*="move-list" i]', '[class*="movelist" i]', '[class*="notation" i]',
    '[class*="game-history" i]', '[class*="gamehistory" i]'
  ];
  const attrsToRead = [
    'id', 'class', 'role', 'aria-label', 'data-cy', 'data-testid',
    'data-state', 'data-fen', 'fen', 'data-ply', 'data-last-move'
  ];
  const roots = [document];
  const rootLabels = ['document'];
  const seenRoots = new Set(roots);
  const candidates = [];
  const seenNodes = new Set();

  // First inspect likely semantic/custom board nodes, and only then inspect
  // their open shadow roots. Do not enumerate every DOM node recursively.
  for (let rootIndex = 0; rootIndex < roots.length && rootIndex < 12; rootIndex++) {
    const root = roots[rootIndex];
    for (const selector of selectors) {
      let nodes = [];
      try { nodes = Array.from(root.querySelectorAll(selector)).slice(0, 18); } catch (_) {}
      for (const el of nodes) {
        if (seenNodes.has(el)) continue;
        seenNodes.add(el);
        const tag = (el.tagName || '').toLowerCase();
        const rect = el.getBoundingClientRect?.();
        const visible = !!rect && rect.width > 0 && rect.height > 0;
        const attrs = {};
        for (const name of attrsToRead) {
          const value = el.getAttribute?.(name);
          if (value && value.length < 180) attrs[name] = value;
        }
        const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120);
        const children = Array.from(el.children || []).slice(0, 8).map(child => ({
          tag: (child.tagName || '').toLowerCase(),
          className: typeof child.className === 'string' ? child.className.slice(0, 140) : '',
          aria: child.getAttribute?.('aria-label') || '',
          dataCy: child.getAttribute?.('data-cy') || ''
        }));
        const outerHTML = (el.outerHTML || '').slice(0, 650);
        candidates.push({
          root: rootLabels[rootIndex],
          tag, attrs, visible,
          size: rect ? [Math.round(rect.width), Math.round(rect.height)] : null,
          childCount: el.children?.length ?? null,
          shadowRoot: !!el.shadowRoot,
          text, children, outerHTML
        });
        if (el.shadowRoot && !seenRoots.has(el.shadowRoot) && roots.length < 12) {
          seenRoots.add(el.shadowRoot);
          roots.push(el.shadowRoot);
          rootLabels.push(tag + '.shadowRoot');
        }
        if (candidates.length >= 120) break;
      }
      if (candidates.length >= 120) break;
    }
  }
  return {
    url: location.href,
    title: document.title,
    readyState: document.readyState,
    totalCandidates: candidates.length,
    inspectedRoots: rootLabels,
    candidates
  };
}"""

INSTALL_WATCHER_JS = r"""() => {
  if (window.__domDiagnosticObservers) {
    for (const observer of window.__domDiagnosticObservers) observer.disconnect();
  }
  const selectors = [
    'wc-chess-board', 'chess-board', '[class*="board" i]',
    '[class*="piece" i]', '[class*="square" i]',
    '[data-cy*="move" i]', '[data-testid*="move" i]',
    '[class*="move-list" i]', '[class*="notation" i]'
  ];
  const attrFilter = ['class','data-cy','data-testid','data-state','data-fen','fen','data-ply','data-last-move','aria-label'];
  const observers = [];
  const roots = [document.documentElement];
  const seenRoots = new Set(roots);
  let lastLog = '';
  function summarize(el) {
    if (!el) return {};
    return {
      tag: (el.tagName || '').toLowerCase(),
      id: el.id || '',
      className: typeof el.className === 'string' ? el.className.slice(0, 160) : '',
      aria: el.getAttribute?.('aria-label') || '',
      dataCy: el.getAttribute?.('data-cy') || '',
      fen: el.getAttribute?.('data-fen') || el.getAttribute?.('fen') || '',
      ply: el.getAttribute?.('data-ply') || '',
      text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 90)
    };
  }
  function closestCandidate(node) {
    let el = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
    for (let i = 0; el && i < 5; i++, el = el.parentElement) {
      const s = [el.tagName, el.id, typeof el.className === 'string' ? el.className : '', el.getAttribute?.('data-cy'), el.getAttribute?.('aria-label')].join(' ');
      if (/chess|board|piece|square|move|notation|history/i.test(s)) return el;
    }
    return null;
  }
  function handleRecords(records) {
    const out = [];
    for (const record of records) {
      const target = closestCandidate(record.target);
      if (!target) continue;
      const summary = summarize(target);
      const key = JSON.stringify({type: record.type, attr: record.attributeName || '', summary});
      if (key === lastLog) continue;
      lastLog = key;
      out.push({type: record.type, attribute: record.attributeName || '', node: summary});
      if (out.length >= 8) break;
    }
    if (out.length && typeof window.__reportDomDiagnostic === 'function') {
      window.__reportDomDiagnostic(out);
    }
  }
  function attach(root) {
    if (!root || seenRoots.has(root) && root !== document.documentElement) return;
    if (!seenRoots.has(root)) seenRoots.add(root);
    const observer = new MutationObserver(handleRecords);
    observer.observe(root, {
      subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: attrFilter
    });
    observers.push(observer);
  }
  attach(document.documentElement);
  for (const selector of selectors) {
    let nodes = [];
    try { nodes = Array.from(document.querySelectorAll(selector)).slice(0, 40); } catch (_) {}
    for (const el of nodes) if (el.shadowRoot) attach(el.shadowRoot);
  }
  window.__domDiagnosticObservers = observers;
  return {installedObservers: observers.length};
}"""


def main() -> None:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel=BROWSER_CHANNEL,
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(10000)
            print(f"[DOM INSPECTOR] Browser channel: {BROWSER_CHANNEL}")
            print(f"[DOM INSPECTOR] Dedicated profile: {PROFILE_DIR}")

            # Diagnostic-only network logging: redact query strings/fragments so
            # session tokens and other URL parameters are not printed.
            net_counts = {"failed": 0, "http_error": 0}

            def safe_url(url: str) -> str:
                parts = urlsplit(url)
                return f"{parts.scheme}://{parts.netloc}{parts.path}"

            ad_domains = (
                "doubleclick.net",
                "googlesyndication.com",
                "btloader.com",
                "amazon-adsystem.com",
                "adnxs.com",
                "adsrvr.org",
                "criteo.com",
                "pubmatic.com",
                "openx.net",
                "rubiconproject.com",
            )

            def on_request_failed(request) -> None:
                parts = urlsplit(request.url)
                host = (parts.hostname or "").lower()
                failure = request.failure or "no failure detail"

                # Keep noisy ad requests from hiding failures in site/game assets.
                if any(host == domain or host.endswith("." + domain) for domain in ad_domains):
                    return
                if net_counts["failed"] >= 60:
                    return
                net_counts["failed"] += 1
                resource_type = request.resource_type
                print(
                    f"[NET FAIL] {resource_type} {request.method} {safe_url(request.url)} "
                    f"| {failure}"
                )

            def on_response(response) -> None:
                if response.status < 400 or net_counts["http_error"] >= 25:
                    return
                net_counts["http_error"] += 1
                print(f"[HTTP {response.status}] {safe_url(response.url)}")

            def on_websocket(websocket) -> None:
                safe_ws_url = safe_url(websocket.url)
                print(f"[WS OPEN] {safe_ws_url}")
                websocket.on(
                    "socketerror",
                    lambda error: print(f"[WS ERROR] {safe_ws_url} | {error}"),
                )
                websocket.on(
                    "close",
                    lambda _: print(f"[WS CLOSE] {safe_ws_url}"),
                )

            page.on("requestfailed", on_request_failed)
            page.on("response", on_response)
            page.on("websocket", on_websocket)
            page.on(
                "console",
                lambda msg: print(
                    f"[CONSOLE {msg.type.upper()}] {msg.text[:500]} "
                    f"| source={safe_url(msg.location.get('url', '')) if msg.location.get('url') else '(unknown)'}"
                ) if msg.type == "error" else None,
            )
            page.on("pageerror", lambda exc: print(f"[PAGE ERROR] {str(exc)[:800]}"))
            page.on("crash", lambda _: print("[ERROR] Chromium renderer crashed. Restart the inspector and try again."))
            page.on("close", lambda _: print("[WARN] Browser page was closed."))
            context.on("close", lambda _: print("[WARN] Chromium context closed."))

            if not page.url.startswith(TARGET_URL):
                try:
                    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
                except PlaywrightTimeoutError as exc:
                    print(f"[WARN] Navigation timed out: {exc}")
                    if page.is_closed():
                        print("[ERROR] Page closed during navigation. Run the command again.")
                        return

            print("\n[DOM INSPECTOR] Browser open:", page.url)
            print("[DOM INSPECTOR] Sign in if required, start/open the test game, and wait for the board + move list.")
            input("[DOM INSPECTOR] When the board is visible, return here and press ENTER... ")

            if page.is_closed() or context.pages == []:
                print("[ERROR] The browser window was closed before the DOM snapshot. Leave it open and retry.")
                return

            try:
                snapshot = page.evaluate(DOM_SNAPSHOT_JS)
            except PlaywrightError as exc:
                print(f"[ERROR] Could not inspect DOM: {exc}")
                print("[HINT] If Chromium crashed/closed, close other Playwright runs and retry; inspector uses its own profile.")
                return

            print("\n[DOM SNAPSHOT]")
            print(f"URL: {snapshot.get('url')}")
            print(f"Title: {snapshot.get('title')}")
            print(f"Ready state: {snapshot.get('readyState')}")
            print(f"Candidate elements: {snapshot.get('totalCandidates')}")
            print(f"Inspected roots: {snapshot.get('inspectedRoots')}")
            for index, item in enumerate(snapshot.get("candidates", []), start=1):
                print(
                    f"#{index} root={item.get('root')} <{item.get('tag')}> "
                    f"visible={item.get('visible')} size={item.get('size')} "
                    f"children={item.get('childCount')} shadowRoot={item.get('shadowRoot')} "
                    f"attrs={item.get('attrs')} text={item.get('text')!r}"
                )
                if item.get("children"):
                    print(f"   children={item['children']}")
                if item.get("outerHTML"):
                    print(f"   html={item['outerHTML']}")

            page.expose_binding(
                "__reportDomDiagnostic",
                lambda source, records: print(f"\n[DOM CHANGE] {records}"),
            )
            watcher = page.evaluate(INSTALL_WATCHER_JS)
            print(f"\n[DOM INSPECTOR] MutationObserver installed: {watcher.get('installedObservers')} root(s).")
            print("[DOM INSPECTOR] Make one move on the test board. Check whether board/piece/move-list changes are reported.")
            print("[DOM INSPECTOR] Press Ctrl+C here to stop.")
            try:
                page.wait_for_event("close", timeout=0)
            except KeyboardInterrupt:
                pass
        except KeyboardInterrupt:
            print("\n[DOM INSPECTOR] Stopped by user.")
        except PlaywrightError as exc:
            print(f"[ERROR] Playwright failure: {exc}")
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
