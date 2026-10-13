"""Resolve Chrome's remote-debugging endpoint for existing-session attachment.

Recent Chrome versions can expose a DevToolsActivePort WebSocket endpoint while
HTTP discovery at /json/version returns 404. This helper reads the local port file
instead of guessing ports or printing the endpoint/token into logs.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit


def resolve_chrome_cdp_endpoint(configured: str | None = None) -> str:
    endpoint = (
        configured
        or os.environ.get("CHROME_CDP_ENDPOINT")
        or "http://127.0.0.1:9222"
    ).strip()

    # Already a complete browser WebSocket endpoint: use it as provided.
    if endpoint.startswith(("ws://", "wss://")) and "/devtools/browser/" in endpoint:
        return endpoint

    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return endpoint

    # User may override the Chrome profile's DevToolsActivePort location.
    explicit_file = os.environ.get("CHROME_DEVTOOLS_ACTIVE_PORT_FILE")
    candidates: list[Path] = []
    if explicit_file:
        candidates.append(Path(explicit_file).expanduser())
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Google" / "Chrome" / "User Data" / "DevToolsActivePort"
        )

    try:
        configured_port = parsed.port
    except ValueError:
        # e.g. a user previously copied the literal placeholder :PORT.
        configured_port = None

    for active_file in candidates:
        try:
            lines = active_file.read_text(encoding="utf-8").splitlines()
            if len(lines) < 2:
                continue
            port = int(lines[0].strip())
            ws_path = lines[1].strip()
            if not ws_path.startswith("/devtools/browser/"):
                continue
            # If a non-default port was requested, only use a matching active file.
            if configured_port and configured_port != port:
                continue
            host = parsed.hostname
            scheme = "wss" if parsed.scheme == "https" else "ws"
            return f"{scheme}://{host}:{port}{ws_path}"
        except (OSError, ValueError):
            continue

    # Legacy remote-debugging mode can still serve /json/version. In that case
    # Playwright can connect directly to the HTTP endpoint.
    return endpoint
