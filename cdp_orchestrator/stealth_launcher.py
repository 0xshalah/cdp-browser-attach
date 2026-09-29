"""StealthLauncher — leak-masking Chrome spawn with CDP inspection hygiene."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

CDP_PORT = 9222
CDP_VERSION_URL = f"http://127.0.0.1:{CDP_PORT}/json/version"


class BrowserSpawnError(Exception):
    """Raised when Chrome cannot be launched or becomes ready in time."""


STEALTH_FLAGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-popup-blocking",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-features=TranslateUI",
    "--disable-ipc-flooding-protection",
    "--no-default-browser-check",
    "--no-pings",
    "--password-store=basic",
    "--use-mock-keychain",
]

STEALTH_SCRIPT = """
// Delete navigator.webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Normalize plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5]
});

// Normalize languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['id-ID', 'id', 'en-US', 'en']
});

// Mock chrome runtime
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};

// Override permissions query
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);

// Override hardwareConcurrency
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });

// Override deviceMemory
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
"""


def _find_chrome_path() -> Optional[str]:
    """Auto-detect Chrome executable path across OS."""
    system = platform.system()

    if system == "Windows":
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            ) as key:
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

    else:
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
        ]
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
    try:
        import psutil
        lock_pid = int(lock_file.read_text().strip().split("-")[-1])
        if not psutil.pid_exists(lock_pid):
            lock_file.unlink(missing_ok=True)
            return False
        return True
    except (ValueError, ImportError):
        return True


async def _wait_for_cdp(port: int, timeout: float = 15.0) -> dict:
    """Poll CDP endpoint until ready or timeout."""
    url = f"http://127.0.0.1:{port}/json/version"
    start = asyncio.get_event_loop().time()
    async with aiohttp.ClientSession() as session:
        while asyncio.get_event_loop().time() - start < timeout:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        logger.info("CDP ready: %s", data.get("Browser", "unknown"))
                        return data
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.5)
    raise BrowserSpawnError(f"Chrome did not become ready within {timeout}s on port {port}")


class StealthLauncher:
    """Launches Chrome with stealth flags and CDP inspection hygiene."""

    def __init__(self, port: int = CDP_PORT, profile_dir: Optional[str] = None):
        self._port = port
        self._profile_dir = profile_dir
        self._process: Optional[subprocess.Popen] = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def profile_dir(self) -> Path:
        if self._profile_dir:
            return Path(self._profile_dir)
        return Path(tempfile.gettempdir()) / "cdp-stealth-profile"

    def is_running(self) -> bool:
        """Check if CDP is already active on the configured port."""
        return _is_port_open(self._port)

    async def ensure_running(self) -> dict:
        """Ensure Chrome is running with stealth CDP. Returns version info."""
        if self.is_running():
            logger.info("CDP port %d already open, verifying...", self._port)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{self._port}/json/version",
                        timeout=aiohttp.ClientTimeout(total=2)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            logger.info("CDP already active: %s", data.get("Browser"))
                            return data
            except (aiohttp.ClientError, asyncio.TimeoutError):
                logger.warning("Port %d open but CDP not responding", self._port)

        # Find Chrome
        chrome_path = _find_chrome_path()
        if not chrome_path:
            raise BrowserSpawnError("Chrome not found. Install Google Chrome or Chromium.")
        logger.info("Found Chrome: %s", chrome_path)

        # Setup profile
        profile_dir = self.profile_dir
        profile_dir.mkdir(parents=True, exist_ok=True)

        if _has_profile_lock(profile_dir):
            logger.warning("Profile lock detected, removing stale lock")
            (profile_dir / "SingletonLock").unlink(missing_ok=True)

        # Build args
        args = [
            chrome_path,
            f"--remote-debugging-port={self._port}",
            f"--user-data-dir={profile_dir}",
            *STEALTH_FLAGS,
        ]

        logger.info("Spawning Chrome with stealth flags")

        if platform.system() == "Windows":
            self._process = subprocess.Popen(
                args,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            self._process = subprocess.Popen(
                args,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        version_info = await _wait_for_cdp(self._port)
        logger.info("Chrome launched with stealth CDP on port %d", self._port)
        return version_info

    def get_stealth_script(self) -> str:
        """Return the stealth evasion script for Page.addScriptToEvaluateOnNewDocument."""
        return STEALTH_SCRIPT

    def terminate(self) -> None:
        """Terminate the Chrome process if we spawned it."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            logger.info("Chrome process terminated")
