"""Log in to Chess.com in a separate Playwright Chrome profile and start a Hikaru bot game.

This only opens the game and presses Play. It never makes chess moves.
The password is entered at runtime (hidden prompt) and is not stored in this file.
"""
from __future__ import annotations

import os
import re
import time
from getpass import getpass
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".playwright_hikaru_profile"
HIKARU_URL = "https://www.chess.com/play/computer/hikaru"
LOGIN_URL = "https://www.chess.com/login"
DEFAULT_USERNAME = "diamond_membership-free"


def is_visible(locator) -> bool:
    try:
        return locator.first.is_visible()
    except Exception:
        return False


def page_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def login_link_visible(page) -> bool:
    pattern = re.compile(r"^\s*log\s*in\s*$", re.I)
    for role in ("link", "button"):
        try:
            if is_visible(page.get_by_role(role, name=pattern)):
                return True
        except Exception:
            pass
    return False


def find_visible_input(page, selectors: list[str]):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 10)):
                item = locator.nth(index)
                if item.is_visible():
                    return item
        except Exception:
            continue
    return None


def submit_login(page, password_input) -> None:
    patterns = (
        re.compile(r"^\s*log\s*in\s*$", re.I),
        re.compile(r"^\s*sign\s*in\s*$", re.I),
        re.compile(r"^\s*continue\s*$", re.I),
    )
    for pattern in patterns:
        try:
            locator = page.get_by_role("button", name=pattern)
            for index in reversed(range(locator.count())):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    candidate.click()
                    return
        except Exception:
            pass

    try:
        submit = page.locator('button[type="submit"], input[type="submit"]')
        for index in reversed(range(submit.count())):
            candidate = submit.nth(index)
            if candidate.is_visible() and not candidate.is_disabled():
                candidate.click()
                return
    except Exception:
        pass

    password_input.press("Enter")


def perform_login_if_needed(page) -> bool:
    # If this separate profile already has a valid session, reuse it.
    if not login_link_visible(page):
        return True

    print("[LOGIN] Chess.com login needed in this separate Chrome profile.")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
    except PlaywrightTimeoutError:
        print("[WARN] Login page navigation timed out; checking whether it loaded.")

    username_input = find_visible_input(
        page,
        [
            'input[name="username"]',
            'input[name="login"]',
            'input[autocomplete="username"]',
            'input[placeholder*="username" i]',
            'input[placeholder*="email" i]',
            'input[type="text"]',
        ],
    )
    password_input = find_visible_input(page, ['input[type="password"]'])

    if username_input is None or password_input is None:
        print("[LOGIN] Login form fields were not detected automatically.")
        input("Browser mein login/verification complete karo, phir yahan Enter dabao: ")
        return not login_link_visible(page) and not is_visible(
            page.locator('input[type="password"]')
        )

    username = os.environ.get("CHESSCOM_USERNAME", DEFAULT_USERNAME)
    # Clean up an HTML-encoded trailing space if it was copied with the username.
    username = username.replace("&#x20;", "").strip()
    password = os.environ.get("CHESSCOM_PASSWORD")
    if not password:
        password = getpass("Chess.com password (screen par hidden rahega): ")

    username_input.fill(username)
    password_input.fill(password)
    submit_login(page, password_input)
    del password

    # Allow normal login redirects and an optional verification challenge.
    for _ in range(12):
        page.wait_for_timeout(1000)
        if not is_visible(page.locator('input[type="password"]')):
            break

    if is_visible(page.locator('input[type="password"]')) or login_link_visible(page):
        print("[LOGIN] CAPTCHA/2-step verification ya login confirmation ho sakta hai.")
        input("Browser mein login/verification finish karo, phir yahan Enter dabao: ")

    still_needs_login = login_link_visible(page) or is_visible(
        page.locator('input[type="password"]')
    )
    if still_needs_login:
        print("[ERROR] Login confirm nahi hua. Password/verification check karo.")
        return False

    print("[LOGIN] Login appears complete.")
    return True


def find_play_button(page):
    """Find the main visible Play button, preferring one below the site navigation."""
    candidates = []
    for selector in ("button", '[role="button"]', "a"):
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 250)):
                item = locator.nth(index)
                if not item.is_visible():
                    continue
                try:
                    label = (item.inner_text(timeout=300) or "").strip()
                except Exception:
                    label = ""
                aria = (item.get_attribute("aria-label") or "").strip()
                title = (item.get_attribute("title") or "").strip()
                label = label or aria or title
                if label.casefold() not in {"play", "start game"}:
                    continue
                box = item.bounding_box()
                if not box:
                    continue
                href = item.get_attribute("href") or ""
                # Avoid the top navigation's "Play" link when searching for
                # the bot's start-game control.
                if selector == "a" and box["y"] < 140 and "/play/computer" not in href:
                    continue
                candidates.append((box["y"], item))
        except Exception:
            continue

    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


def game_looks_active(page) -> bool:
    try:
        board_visible = is_visible(
            page.locator("wc-chess-board#board-play-computer, wc-chess-board")
        )
        body = page_text(page)
        active_words = re.search(
            r"\b(resign|offer draw|claim draw|new game|takeback)\b",
            body,
            re.I,
        )
        return board_visible and bool(active_words)
    except Exception:
        return False


def wait_for_bot_page(page) -> bool:
    """Wait briefly for Hikaru's game UI, with one reload on a bot-engine error."""
    retried = False
    deadline = time.time() + 35

    while time.time() < deadline:
        body = page_text(page)
        if "Hikaru" not in body and "/play/computer/hikaru" not in page.url.lower():
            page.wait_for_timeout(750)
            continue

        lower = body.lower()
        if "couldn't load the bot engine" in lower:
            if retried:
                print("[ERROR] Chess.com says it couldn't load the bot engine.")
                print("[ERROR] Check this separate Chrome window's internet connection, then rerun.")
                return False
            retried = True
            print("[WARN] Bot engine load error seen; reloading Hikaru page once...")
            try:
                page.reload(wait_until="domcontentloaded", timeout=45000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(3500)
            continue

        if "loading this bot is taking" in lower:
            page.wait_for_timeout(1000)
            continue

        # The direct path should be Hikaru's page; the page text check prevents
        # accidentally pressing Play on a different bot if the site redirects.
        if "/play/computer/hikaru" in page.url.lower() or "hikaru" in lower:
            return True

        page.wait_for_timeout(750)

    print("[ERROR] Hikaru bot page did not become ready in time.")
    return False


def main() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print("[START] Launching a separate Chrome window for Chess.com.")
    print("[INFO] Profile:", PROFILE_DIR)
    print("[INFO] Existing Chrome/ChatGPT window will not be controlled.")

    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="chrome",
                headless=False,
                no_viewport=True,
                args=["--start-maximized"],
            )
        except Exception as exc:
            raise SystemExit(
                "[ERROR] Separate Chrome launch nahi hua. Confirm karo Google Chrome "
                "installed hai aur koi dusra process isi .playwright_hikaru_profile ko "
                f"use nahi kar raha.\nDetails: {exc}"
            ) from exc

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(7000)

            try:
                page.goto(HIKARU_URL, wait_until="domcontentloaded", timeout=60000)
            except PlaywrightTimeoutError:
                print("[WARN] Initial page load timed out; checking current page.")

            if not perform_login_if_needed(page):
                return

            # Go back to the exact Hikaru computer-bot page after login.
            try:
                page.goto(HIKARU_URL, wait_until="domcontentloaded", timeout=60000)
            except PlaywrightTimeoutError:
                print("[WARN] Hikaru page load timed out; waiting for the UI to settle.")
            page.wait_for_timeout(2500)

            if not wait_for_bot_page(page):
                print("[INFO] Browser left open for troubleshooting.")
                return

            print("[OK] Hikaru bot page confirmed:", page.url)
            if game_looks_active(page):
                print("[OK] A game appears to be active already; no extra Play click sent.")
            else:
                play_button = find_play_button(page)
                if play_button is not None:
                    play_button.click()
                    print("[ACTION] Clicked Play on Hikaru's bot page.")
                    page.wait_for_timeout(5000)
                else:
                    print("[WARN] Play button wasn't identified safely.")
                    input("Hikaru page par Play button manually click karo, phir Enter dabao: ")

                if game_looks_active(page):
                    print("[SUCCESS] Hikaru game appears to have started.")
                else:
                    print("[CHECK] Play action was attempted; visually confirm the game in Chrome.")
                    print("[CHECK] If a confirmation dialog is open, finish it manually.")

            print("[DONE] No chess move was made by this script.")
            print("[DONE] Chrome window will stay open until you press Enter here.")
            input("Browser band karne ke liye Enter dabao (window manually bhi band kar sakte ho): ")
        finally:
            # Closing this dedicated context closes only the separate Playwright Chrome.
            context.close()


if __name__ == "__main__":
    main()
