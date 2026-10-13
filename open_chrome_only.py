from pathlib import Path
import os
import subprocess

base = Path(__file__).resolve().parent
profile = base / ".normal_chrome_profile"
extension = base / "chess_game_tracker"

candidates = [
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Google/Chrome/Application/chrome.exe",
]

chrome = next((p for p in candidates if p.is_file()), None)
if chrome is None:
    raise SystemExit("Chrome.exe nahi mila. Google Chrome installation check karo.")

if not (extension / "manifest.json").is_file():
    raise SystemExit(f"Tracker extension missing: {extension}")

profile.mkdir(parents=True, exist_ok=True)

subprocess.Popen([
    str(chrome),
    f"--user-data-dir={profile}",
    f"--load-extension={extension}",
    "--new-window",
    "about:blank",
])

print("Normal Chrome opened with the read-only Chess.com move tracker.")
print("Open Chess.com and play normally. No moves are clicked or sent by this tool.")
