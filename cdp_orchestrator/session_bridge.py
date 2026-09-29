"""Session & Cookie Bridge — export/import cookies and storage via CDP."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class SessionBridge:
    """Bridge browser session state to/from HTTP clients."""

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._msg_id = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._listen_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        """Connect to CDP WebSocket (auto-resolves page target if browser-level)."""
        if "/devtools/browser/" in self._ws_url:
            self._ws_url = await self._get_page_ws_url()
        session = aiohttp.ClientSession()
        self._ws = await session.ws_connect(self._ws_url)
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("SessionBridge connected to %s", self._ws_url)

    async def _get_page_ws_url(self) -> str:
        """Get page-level WebSocket URL from /json endpoint."""
        import aiohttp
        port = self._ws_url.split(":")[2].split("/")[0]
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/json") as resp:
                targets = await resp.json()
                for target in targets:
                    if target.get("type") == "page":
                        return target["webSocketDebuggerUrl"]
                raise RuntimeError("No page target found")

    async def disconnect(self) -> None:
        """Disconnect and cleanup."""
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
        logger.info("SessionBridge disconnected")

    async def export_cookies(self) -> list[dict[str, Any]]:
        """Export all browser cookies via CDP.

        Returns:
            List of cookie dictionaries
        """
        result = await self._send("Network.getAllCookies")
        cookies = result.get("cookies", [])
        logger.info("Exported %d cookies", len(cookies))
        return cookies

    async def sync_to_aiohttp(
        self,
        session: aiohttp.ClientSession,
    ) -> None:
        """Sync browser cookies to aiohttp session.

        Args:
            session: aiohttp ClientSession to inject cookies into
        """
        cookies = await self.export_cookies()

        for cookie in cookies:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            domain = cookie.get("domain", "").lstrip(".")
            path = cookie.get("path", "/")

            if name and domain:
                from yarl import URL
                session.cookie_jar.update_cookies(
                    {name: value},
                    response_url=URL(f"https://{domain}{path}"),
                )

        logger.info("Synced %d cookies to aiohttp session", len(cookies))

    async def inject_cookies(
        self,
        cookies: list[dict[str, Any]],
    ) -> None:
        """Inject cookies into browser via CDP.

        Args:
            cookies: List of cookie dictionaries
        """
        await self._send("Network.setCookies", {"cookies": cookies})
        logger.info("Injected %d cookies into browser", len(cookies))

    async def extract_storage(self) -> dict[str, dict[str, str]]:
        """Extract localStorage and sessionStorage.

        Returns:
            Dictionary with 'localStorage' and 'sessionStorage' keys
        """
        expression = """
        (function() {
            const local = {};
            const session = {};

            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                local[key] = localStorage.getItem(key);
            }

            for (let i = 0; i < sessionStorage.length; i++) {
                const key = sessionStorage.key(i);
                session[key] = sessionStorage.getItem(key);
            }

            return { localStorage: local, sessionStorage: session };
        })()
        """
        result = await self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })

        value = result.get("result", {}).get("value", {})
        if not isinstance(value, dict):
            value = {}

        logger.info(
            "Extracted storage: %d local, %d session",
            len(value.get("localStorage", {})),
            len(value.get("sessionStorage", {})),
        )
        return value

    async def get_auth_headers(self) -> dict[str, str]:
        """Extract authorization headers from browser.

        Attempts to find Bearer <_REDACTED> or auth headers from
        localStorage/sessionStorage and document.cookie.

        Returns:
            Dictionary of auth headers
        """
        storage = await self.extract_storage()
        headers: dict[str, str] = {}

        # Check localStorage for tokens
        local = storage.get("localStorage", {})
        for key, value in local.items():
            key_lower = key.lower()
            if "token" in key_lower or "auth" in key_lower or "bearer" in key_lower:
                headers["Authorization"] = f"Bearer {value}"
                break

        # Check sessionStorage
        session = storage.get("sessionStorage", {})
        for key, value in session.items():
            key_lower = key.lower()
            if "token" in key_lower or "auth" in key_lower:
                if "Authorization" not in headers:
                    headers["Authorization"] = f"Bearer {value}"
                break

        return headers

    async def _send(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> dict:
        """Send CDP command and wait for result."""
        if not self._ws:
            raise RuntimeError("Not connected")

        self._msg_id += 1
        msg_id = self._msg_id
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[str(msg_id)] = future

        await self._ws.send_json({
            "id": msg_id,
            "method": method,
            "params": params or {},
        })

        return await asyncio.wait_for(future, timeout=timeout)

    async def _listen_loop(self) -> None:
        """Main WebSocket listener loop."""
        assert self._ws is not None

        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if "id" in data:
                    future = self._pending.pop(str(data["id"]), None)
                    if future and not future.done():
                        if "error" in data:
                            future.set_exception(
                                RuntimeError(data["error"].get("message", "CDP error"))
                            )
                        else:
                            future.set_result(data.get("result", {}))
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break
