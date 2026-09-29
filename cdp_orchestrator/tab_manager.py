"""Multi-tab management via CDP Target and Browser domains."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class TabManager:
    """Manage multiple browser tabs via CDP."""

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._msg_id = 0
        self._page_ws_url: Optional[str] = None

    async def connect(self) -> None:
        """Connect to CDP WebSocket, resolving to page target if browser-level."""
        if "/devtools/browser/" in self._ws_url:
            self._ws_url = await self._resolve_page_ws()
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url)
        logger.info("TabManager connected")

    async def _resolve_page_ws(self) -> str:
        """Resolve browser-level WS URL to first page target."""
        import aiohttp
        async with aiohttp.ClientSession() as session:
            # Extract host from ws URL
            from urllib.parse import urlparse
            parsed = urlparse(self._ws_url)
            host = f"http://{parsed.hostname}:{parsed.port}"
            async with session.get(f"{host}/json") as resp:
                targets = await resp.json()
                for t in targets:
                    if t.get("type") == "page":
                        return t["webSocketDebuggerUrl"]
                raise RuntimeError("No page targets found")

    async def _send(self, method: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        if not self._ws:
            raise RuntimeError("Not connected")
        self._msg_id += 1
        msg_id = self._msg_id
        await self._ws.send_json({"id": msg_id, "method": method, "params": params or {}})
        async for msg in self._ws:
            data = msg.json()
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error: {data['error']}")
                return data.get("result", {})
        raise RuntimeError("WebSocket closed")

    async def create_tab(self, url: str = "about:blank") -> str:
        """Create a new tab and return its targetId."""
        result = await self._send("Target.createTarget", {"url": url})
        target_id = result["targetId"]
        logger.info(f"Created tab: {target_id} -> {url}")
        return target_id

    async def list_tabs(self) -> List[Dict[str, Any]]:
        """List all open tabs with their targetId, URL, and title."""
        targets = await self._send("Target.getTargets")
        return [
            {
                "targetId": t["targetId"],
                "url": t.get("url", ""),
                "title": t.get("title", ""),
                "type": t.get("type", ""),
            }
            for t in targets.get("targetInfos", [])
            if t.get("type") == "page"
        ]

    async def switch_tab(self, target_id: str) -> None:
        """Switch to a specific tab by targetId."""
        await self._send("Target.activateTarget", {"targetId": target_id})
        logger.info(f"Switched to tab: {target_id}")
        await asyncio.sleep(0.3)

    async def close_tab(self, target_id: str) -> None:
        """Close a specific tab by targetId."""
        await self._send("Target.closeTarget", {"targetId": target_id})
        logger.info(f"Closed tab: {target_id}")

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("TabManager disconnected")
