"""Browser spawner — auto-detect Chrome, launch with CDP, verify connection."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
CDP_VERSION_URL = f"{CDP_URL}/json/version"
LAUNCH_TIMEOUT = 15.0
POLL_INTERVAL = 0.5


class BrowserLaunchError(Exception):
    """Raised when Chrome cannot be launched or becomes ready in time."""


def _find_chrome_path() -> Optional[str]:
    """Auto-detect Chrome executable path across OS."""
    system = platform.system()

    if system == "Windows":
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        # Try registry
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
                val, _ = winreg.QueryValueEx(key, None)
                if val and Path(val).exists():
                    return val
        except (OSError, ImportError):
            pass

    elif system == "Darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]

    else:  # Linux
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        ]
        # Try shutil.which as fallback
        which = shutil.which("google-chrome") or shutil.which("chromium")
        if which:
            return which

    for p in paths:
        if p and Path(p).exists():
            return p

    return None


def _is_port_open(port: int) -> bool:
    """Check if a TCP port is open."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _has_profile_lock(user_data_dir: Path) -> bool:
    """Check if Chrome profile has an active lock."""
    lock_file = user_data_dir / "SingletonLock"
    if not lock_file.exists():
        return False
    # Check if the lock is stale (process no longer running)
    try:
        import psutil
        lock_pid = int(lock_file.read_text().strip().split("-")[-1])
        if not psutil.pid_exists(lock_pid):
            lock_file.unlink(missing_ok=True)
            return False
        return True
    except (ValueError, ImportError):
        return True


async def _wait_for_cdp(timeout: float = LAUNCH_TIMEOUT) -> dict:
    """Poll CDP endpoint until ready or timeout."""
    start = time.monotonic()
    async with aiohttp.ClientSession() as session:
        while time.monotonic() - start < timeout:
            try:
                async with session.get(CDP_VERSION_URL, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info("CDP ready: %s", data.get("Browser", "unknown"))
                        return data
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(POLL_INTERVAL)

    raise BrowserLaunchError(
        f"Chrome did not become ready within {timeout}s on port {CDP_PORT}"
    )


async def ensure_browser_running() -> dict:
    """Ensure Chrome is running with CDP. Returns CDP version info.

    If CDP is already active, returns immediately.
    Otherwise, auto-detects Chrome, spawns it, and waits for readiness.
    """
    # Pre-flight check
    if _is_port_open(CDP_PORT):
        logger.info("CDP port %d already open, verifying...", CDP_PORT)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(CDP_VERSION_URL, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info("CDP already active: %s", data.get("Browser"))
                        return data
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning("Port %d open but CDP not responding, will respawn", CDP_PORT)

    # Find Chrome
    chrome_path = _find_chrome_path()
    if not chrome_path:
        raise BrowserLaunchError(
            "Chrome not found. Install Google Chrome or Chromium."
        )
    logger.info("Found Chrome: %s", chrome_path)

    # Create isolated profile dir
    profile_dir = Path(tempfile.gettempdir()) / "cdp-browser-attach-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    # Check profile lock
    if _has_profile_lock(profile_dir):
        logger.warning("Profile lock detected at %s, removing stale lock", profile_dir)
        (profile_dir / "SingletonLock").unlink(missing_ok=True)

    # Spawn Chrome
    args = [
        chrome_path,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ]

    logger.info("Spawning Chrome: %s", " ".join(args))

    if platform.system() == "Windows":
        # Detached process on Windows
        subprocess.Popen(
            args,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            args,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Wait for CDP
    logger.info("Waiting for CDP to become ready (timeout: %.0fs)...", LAUNCH_TIMEOUT)
    version_info = await _wait_for_cdp(LAUNCH_TIMEOUT)

    logger.info("Chrome launched successfully with CDP on port %d", CDP_PORT)
    return version_info


def main():
    """CLI entrypoint for standalone usage."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    async def run():
        try:
            info = await ensure_browser_running()
            print(f"\n{'='*50}")
            print(f"CDP Browser Ready")
            print(f"{'='*50}")
            print(f"Browser: {info.get('Browser', 'unknown')}")
            print(f"WebSocket: {info.get('webSocketDebuggerUrl', 'N/A')}")
            print(f"Port: {CDP_PORT}")
            print(f"{'='*50}")
        except BrowserLaunchError as e:
            logger.error("Failed to launch browser: %s", e)
            raise SystemExit(1)

    asyncio.run(run())


if __name__ == "__main__":
    main()
