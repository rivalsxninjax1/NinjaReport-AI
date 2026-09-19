#!/usr/bin/env python3
"""Desktop launcher for NinjaReport AI.

NOTE on packaging: the project's phase plan suggests a lightweight Tauri
shell "if practical." A Tauri wrapper needs a Rust + Node toolchain that
isn't part of this Python project's environment, so this launcher is the
practical fallback the same wording allows: it starts the Streamlit
backend (already bound to 127.0.0.1 only — see .streamlit/config.toml)
and opens it in the default browser, with clean startup/shutdown. A real
Tauri shell could later wrap this same `streamlit run app.py` process.

Usage:
    python scripts/launch_desktop.py
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8501
URL = f"http://{HOST}:{PORT}"
STARTUP_TIMEOUT_SECONDS = 30


def _wait_until_ready(timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(URL, timeout=1)
            return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.5)
    return False


def main() -> int:
    process = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "app.py"),
            "--server.address", HOST,
            "--server.port", str(PORT),
            "--server.headless", "true",
        ],
        cwd=PROJECT_ROOT,
    )

    def shutdown(*_args) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"Starting NinjaReport AI at {URL} ...")
    if _wait_until_ready(STARTUP_TIMEOUT_SECONDS):
        webbrowser.open(URL)
        print("Ready. Press Ctrl+C to stop.")
    else:
        print("Streamlit did not respond in time — check the terminal output above for errors.", file=sys.stderr)

    return process.wait()


if __name__ == "__main__":
    sys.exit(main())
