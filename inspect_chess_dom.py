"""Inspect visible DOM candidates on a local or authorized chess test page.

This script does not inspect network traffic or hidden/private endpoints. It prints
candidate board/piece/move-list DOM nodes and watches relevant DOM mutations.
"""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "playwright_chess_profile"
TARGET_URL = os.environ.get("CHESS_BOT_URL", "https://www.chess.com/play/computer")

DOM_SNAPSHOT_JS = r"""() => {
  const keywords = /chess|board|piece|square|move|notation|gamehistory|game-history|movelist|move-list/i;
  const picked = [];
  const seen = new Set();
  const fields = ['id','class','role','aria-label','data-cy','data-testid','data-state','data-fen','fen','data-ply','data-last-move'];

  function visitRoot(root) {
    let nodes = [];
    try { nodes = Array.from(root.querySelectorAll('*')); } catch (_) {}
    for (const el of nodes) {
      if (seen.has(el)) continue;
      seen.add(el);
      const tag = (el.tagName || '').toLowerCase();
      const attrs = {};
      for (const name of fields) {
        const value = el.getAttribute?.(name);
        if (value && value.length < 180) attrs[name] = value;
      }
      const clue = [tag, ...Object.values(attrs)].join(' ');
      const isCustom = tag.includes('-');
      const isCandidate = keywords.test(clue) || (isCustom && /chess|board|piece|move|game/i.test(tag));
      if (isCandidate) {
        const rect = el.getBoundingClientRect?.();
        const visible = !!rect && rect.width > 0 && rect.height > 0;
        const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 150);
        if (visible || attrs['data-fen'] || attrs['fen']) {
          picked.push({
            tag,
            attrs,
            visible,
            size: rect ? [Math.round(rect.width), Math.round(rect.height)] : null,
            childCount: el.children?.length ?? null,
            text,
            shadowRoot: !!el.shadowRoot
          });
        }
      }
      if (el.shadowRoot) visitRoot(el.shadowRoot);
    }
  }

  visitRoot(document);
  const customTags = [...new Set(picked.map(x => x.tag).filter(x => x.includes('-')))];
  return {
    url: location.href,
    title: document.title,
    totalCandidates: picked.length,
    customTags,
    candidates: picked.slice(0, 100)
  };
}"""

INSTALL_WATCHER_JS = r"""() => {
  if (window.__domDiagnosticObserver) window.__domDiagnosticObserver.disconnect();
  const clue = /chess|board|piece|square|move|notation|movelist|move-list|gamehistory|game-history/i;
  const attrFilter = ['class','data-cy','data-testid','data-state','data-fen','fen','data-ply','data-last-move','aria-label'];
  let lastLog = '';
  function summarize(el) {
    if (!el) return {};
    return {
      tag: (el.tagName || '').toLowerCase(),
      id: el.id || '',
      className: typeof el.className === 'string' ? el.className.slice(0, 180) : '',
      aria: el.getAttribute?.('aria-label') || '',
      dataCy: el.getAttribute?.('data-cy') || '',
      fen: el.getAttribute?.('data-fen') || el.getAttribute?.('fen') || '',
      ply: el.getAttribute?.('data-ply') || '',
      text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 100)
    };
  }
  function closestCandidate(node) {
    let el = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
    for (let i = 0; el && i < 5; i++, el = el.parentElement) {
      const s = [el.tagName, el.id, typeof el.className === 'string' ? el.className : '', el.getAttribute?.('data-cy'), el.getAttribute?.('aria-label')].join(' ');
      if (clue.test(s)) return el;
    }
    return null;
  }
  const observer = new MutationObserver(records => {
    const out = [];
    for (const record of records) {
      const target = closestCandidate(record.target);
      if (!target) continue;
      const summary = summarize(target);
      const key = JSON.stringify({type: record.type, attr: record.attributeName || '', summary});
      if (key === lastLog) continue;
      lastLog = key;
      out.push({type: record.type, attribute: record.attributeName || '', node: summary});
      if (out.length >= 10) break;
    }
    if (out.length && typeof window.__reportDomDiagnostic === 'function') {
      window.__reportDomDiagnostic(out);
    }
  });
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: attrFilter
  });
  window.__domDiagnosticObserver = observer;
  return {installed: true};
}"""


def main() -> None:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(5000)
            if not page.url.startswith(TARGET_URL):
                try:
                    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
                except PlaywrightTimeoutError as exc:
                    print(f"[WARN] Navigation timed out but the browser stays open: {exc}")

            print("\n[DOM INSPECTOR] Browser open:", page.url)
            print("[DOM INSPECTOR] Sign in if required, start/open the test game, and wait for the board + move list.")
            input("[DOM INSPECTOR] When the board is visible, return here and press ENTER... ")

            snapshot = page.evaluate(DOM_SNAPSHOT_JS)
            print("\n[DOM SNAPSHOT]")
            print(f"URL: {snapshot.get('url')}")
            print(f"Title: {snapshot.get('title')}")
            print(f"Candidate elements: {snapshot.get('totalCandidates')}")
            print(f"Custom element tags: {snapshot.get('customTags')}")
            for index, item in enumerate(snapshot.get("candidates", []), start=1):
                print(
                    f"#{index} <{item.get('tag')}> "
                    f"visible={item.get('visible')} size={item.get('size')} "
                    f"children={item.get('childCount')} shadowRoot={item.get('shadowRoot')} "
                    f"attrs={item.get('attrs')} text={item.get('text')!r}"
                )

            page.expose_binding(
                "__reportDomDiagnostic",
                lambda source, records: print(f"\n[DOM CHANGE] {records}"),
            )
            page.evaluate(INSTALL_WATCHER_JS)
            print("\n[DOM INSPECTOR] MutationObserver installed.")
            print("[DOM INSPECTOR] Make exactly one move on the test board and check whether board/piece/move-list changes are reported.")
            print("[DOM INSPECTOR] Press Ctrl+C here to stop.")
            try:
                page.wait_for_event("close", timeout=0)
            except KeyboardInterrupt:
                pass
        finally:
            context.close()


if __name__ == "__main__":
    main()
