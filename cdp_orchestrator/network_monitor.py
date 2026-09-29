"""Network response monitoring — capture API responses after actions."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


class NetworkMonitor:
    """Monitor network traffic and capture responses matching patterns."""

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._msg_id = 0
        self._responses: Dict[str, Dict[str, Any]] = {}
        self._response_events: Dict[str, asyncio.Event] = {}
        self._listen_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if "/devtools/browser/" in self._ws_url:
            self._ws_url = await self._resolve_page_ws()
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url)
        self._listen_task = asyncio.create_task(self._listen_loop())
        await self._send("Network.enable")
        logger.info("NetworkMonitor connected")

    async def _resolve_page_ws(self) -> str:
        import aiohttp
        from urllib.parse import urlparse
        parsed = urlparse(self._ws_url)
        host = f"http://{parsed.hostname}:{parsed.port}"
        async with aiohttp.ClientSession() as session:
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

    async def _listen_loop(self) -> None:
        """Background listener for network events."""
        try:
            async for msg in self._ws:
                data = msg.json()
                method = data.get("method", "")
                params = data.get("params", {})

                if method == "Network.responseReceived":
                    request_id = params.get("requestId")
                    response = params.get("response", {})
                    url = response.get("url", "")
                    self._responses[request_id] = {
                        "url": url,
                        "status": response.get("status"),
                        "headers": response.get("headers", {}),
                        "body": None,
                    }

                elif method == "Network.loadingFinished":
                    request_id = params.get("requestId")
                    if request_id in self._responses:
                        try:
                            body_result = await self._send(
                                "Network.getResponseBody",
                                {"requestId": request_id},
                            )
                            body = body_result.get("body", "")
                            if body_result.get("base64Encoded"):
                                import base64
                                body = base64.b64decode(body).decode("utf-8", errors="replace")
                            self._responses[request_id]["body"] = body
                        except Exception as e:
                            logger.warning(f"Failed to get body for {request_id}: {e}")

                        # Notify waiters
                        event = self._response_events.get(request_id)
                        if event:
                            event.set()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Network listener error: {e}")

    async def wait_for_response(
        self,
        url_pattern: str,
        timeout: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        """Wait for a network response matching the URL pattern."""
        pattern = re.compile(url_pattern)
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < timeout:
            for request_id, response in self._responses.items():
                if pattern.search(response["url"]) and response["body"] is not None:
                    return response
            await asyncio.sleep(0.2)

        logger.warning(f"No response matching '{url_pattern}' within {timeout}s")
        return None

    async def capture_response_after_action(
        self,
        action_callback: Callable,
        url_pattern: str,
        timeout: float = 10.0,
    ) -> Optional[Dict[str, Any]]:
        """Execute an action and capture the matching network response.

        Args:
            action_callback: Async callable that performs the action (e.g., click submit).
            url_pattern: Regex pattern to match the response URL.
            timeout: Maximum wait time in seconds.

        Returns:
            Dict with url, status, headers, body — or None if timeout.
        """
        # Clear previous responses
        self._responses.clear()

        # Execute the action
        await action_callback()

        # Wait for matching response
        return await self.wait_for_response(url_pattern, timeout)

    async def disconnect(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("NetworkMonitor disconnected")
