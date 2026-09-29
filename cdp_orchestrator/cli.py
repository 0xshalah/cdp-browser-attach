"""Agent-First CLI — cdp-attach entrypoint for AI agent orchestration."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from .stealth_launcher import StealthLauncher, BrowserSpawnError
from .process_supervisor import ProcessSupervisor, CDPReconnectFailedError

logger = logging.getLogger(__name__)

DEFAULT_PORT = 9222


def _find_chrome_path() -> Optional[str]:
    """Auto-detect Chrome executable path."""
    import platform
    system = platform.system()
    if system == "Windows":
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
    elif system == "Darwin":
        paths = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        paths = ["/usr/bin/google-chrome", "/usr/bin/chromium"]
    for p in paths:
        if p and Path(p).exists():
            return p
    return None


def _is_port_open(port: int) -> bool:
    """Check if a TCP port is open."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


async def _check_cdp_ready(port: int) -> Optional[dict]:
    """Check if CDP is ready on the given port."""
    import aiohttp
    url = f"http://127.0.0.1:{port}/json/version"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                if resp.status == 200:
                    return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    return None


def _output_json(data: dict) -> None:
    """Output JSON metadata to stdout."""
    print(json.dumps(data, indent=2))


def _output_hermes_config(port: int) -> None:
    """Output Hermes-compatible configuration."""
    print(f"# Hermes CDP Configuration")
    print(f"# Add to config.yaml or run: hermes config set browser.cdp_url http://127.0.0.1:{port}")
    print(f"export HERMES_BROWSER_CDP_URL=http://127.0.0.1:{port}")
    print(f"browser.cdp_url: http://127.0.0.1:{port}")


async def async_main(args: argparse.Namespace) -> int:
    """Async main entrypoint."""
    port = args.port
    agent = args.agent
    profile = args.profile

    # Check if already running
    if _is_port_open(port):
        logger.info("Port %d is open, checking CDP readiness...", port)
        version_info = await _check_cdp_ready(port)
        if version_info:
            logger.info("CDP already active: %s", version_info.get("Browser"))
            output = {
                "status": "ready",
                "port": port,
                "browser": version_info.get("Browser", "unknown"),
                "ws_url": version_info.get("webSocketDebuggerUrl", ""),
            }
            _output_json(output)
            if agent == "hermes":
                _output_hermes_config(port)
            return 0
        else:
            logger.warning("Port %d open but CDP not responding", port)

    # Need to spawn
    logger.info("CDP not active, spawning Chrome...")
    launcher = StealthLauncher(port=port, profile_dir=profile)

    try:
        version_info = await launcher.ensure_running()
        output = {
            "status": "ready",
            "port": port,
            "browser": version_info.get("Browser", "unknown"),
            "ws_url": version_info.get("webSocketDebuggerUrl", ""),
        }
        _output_json(output)
        if agent == "hermes":
            _output_hermes_config(port)
        return 0

    except BrowserSpawnError as e:
        logger.error("Failed to spawn browser: %s", e)
        return 1


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        prog="cdp-attach",
        description="Agent-First CDP Browser Attach — ensure Chrome is running with CDP",
    )
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="Ensure browser is running (default behavior)",
    )
    parser.add_argument(
        "--agent",
        choices=["hermes", "generic"],
        default="generic",
        help="Agent type for config output format",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"CDP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Chrome user-data-dir path (default: temp dir)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
