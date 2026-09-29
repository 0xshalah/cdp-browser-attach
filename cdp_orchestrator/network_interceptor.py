"""Network Interceptor — async CDP network monitoring and response capture."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Pattern, Union

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class NetworkResponse:
    """Captured network response."""
    url: str
    status: int
    headers: dict[str, str]
    body: Any = None
    body_raw: str = ""
    mime_type: str = ""
    request_id: str = ""


@dataclass
class InterceptorConfig:
    """Configuration for network interception."""
    url_patterns: list[Union[str, Pattern[str]]] = field(default_factory=list)
    content_types: list[str] = field(default_factory=lambda: ["application/json"])
    max_buffer_size: int = 10 * 1024 * 1024  # 10MB
    capture_all: bool = False


class NetworkInterceptor:
    """Async CDP network interceptor with filtering and response capture."""

    def __init__(self, ws_url: str, config: Optional[InterceptorConfig] = None):
        self._ws_url = ws_url
        self._config = config or InterceptorConfig()
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._msg_id = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._responses: dict[str, dict] = {}
        self._listeners: list[Callable[[NetworkResponse], Awaitable[None]]] = []
        self._response_queue: asyncio.Queue[NetworkResponse] = asyncio.Queue()
        self._listen_task: Optional[asyncio.Task] = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to CDP WebSocket and enable Network domain.

        If connected to browser-level WS, auto-discovers page target.
        """
        # If browser-level WS, get page target
        if "/devtools/browser/" in self._ws_url:
            self._ws_url = await self._get_page_ws_url()

        session = aiohttp.ClientSession()
        self._ws = await session.ws_connect(self._ws_url)
        self._connected = True

        # Start listener BEFORE sending commands
        self._listen_task = asyncio.create_task(self._listen_loop())

        # Enable Network domain
        await self._send("Network.enable", {
            "maxTotalBufferSize": self._config.max_buffer_size,
            "maxResourceBufferSize": self._config.max_buffer_size,
        })

        logger.info("NetworkInterceptor connected to %s", self._ws_url)

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
        self._connected = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
        logger.info("NetworkInterceptor disconnected")

    def add_listener(self, callback: Callable[[NetworkResponse], Awaitable[None]]) -> None:
        """Add a listener callback for captured responses."""
        self._listeners.append(callback)

    async def wait_for_response(
        self,
        url_pattern: Union[str, Pattern[str]],
        timeout: float = 10.0,
    ) -> NetworkResponse:
        """Wait for a response matching the URL pattern."""
        loop = asyncio.get_event_loop()
        future: asyncio.Future[NetworkResponse] = loop.create_future()

        def _callback(response: NetworkResponse) -> None:
            if not future.done():
                future.set_result(response)

        self.add_listener(_callback)

        # Check already captured responses
        for resp in list(self._responses.values()):
            if self._matches(resp.get("url", ""), url_pattern):
                future.set_result(self._to_network_response(resp))
                self._listeners.remove(_callback)
                return await future

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"No response matching '{url_pattern}' within {timeout}s"
            )
        finally:
            if _callback in self._listeners:
                self._listeners.remove(_callback)

    async def _send(self, method: str, params: Optional[dict] = None) -> dict:
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

        result = await asyncio.wait_for(future, timeout=30.0)
        return result

    async def _listen_loop(self) -> None:
        """Main WebSocket listener loop."""
        assert self._ws is not None

        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                await self._handle_message(data)
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _handle_message(self, data: dict) -> None:
        """Handle incoming CDP message."""
        # Response to our command
        if "id" in data:
            future = self._pending.pop(str(data["id"]), None)
            if future and not future.done():
                if "error" in data:
                    future.set_exception(RuntimeError(data["error"].get("message", "CDP error")))
                else:
                    future.set_result(data.get("result", {}))
            return

        # Event
        method = data.get("method", "")
        params = data.get("params", {})

        if method == "Network.responseReceived":
            await self._on_response_received(params)
        elif method == "Network.loadingFinished":
            await self._on_loading_finished(params)

    async def _on_response_received(self, params: dict) -> None:
        """Handle Network.responseReceived event."""
        response = params.get("response", {})
        url = response.get("url", "")
        request_id = params.get("requestId", "")

        if not self._should_capture(url, response.get("mimeType", "")):
            return

        self._responses[request_id] = {
            "url": url,
            "status": response.get("status", 0),
            "headers": response.get("headers", {}),
            "mime_type": response.get("mimeType", ""),
            "request_id": request_id,
        }

    async def _on_loading_finished(self, params: dict) -> None:
        """Handle Network.loadingFinished event — fetch body."""
        request_id = params.get("requestId", "")
        if request_id not in self._responses:
            return

        info = self._responses[request_id]

        try:
            result = await self._send("Network.getResponseBody", {
                "requestId": request_id,
            })
            body_raw = result.get("body", "")
            is_base64 = result.get("base64Encoded", False)

            if is_base64:
                body_bytes = base64.b64decode(body_raw)
                body = body_bytes.decode("utf-8", errors="replace")
            else:
                body = body_raw

            # Try to parse JSON
            try:
                parsed = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                parsed = body

            info["body"] = parsed
            info["body_raw"] = body

        except Exception as e:
            logger.warning("Failed to get response body for %s: %s", info["url"], e)
            info["body"] = None

        # Notify listeners
        network_resp = self._to_network_response(info)
        for listener in self._listeners:
            try:
                await listener(network_resp)
            except Exception as e:
                logger.error("Listener error: %s", e)

        # Also put in queue
        await self._response_queue.put(network_resp)

    def _should_capture(self, url: str, mime_type: str) -> bool:
        """Check if URL matches capture criteria."""
        if self._config.capture_all:
            return True

        # Check content type
        if not any(ct in mime_type for ct in self._config.content_types):
            return False

        # Check URL patterns
        for pattern in self._config.url_patterns:
            if isinstance(pattern, str):
                if pattern in url:
                    return True
            elif isinstance(pattern, re.Pattern):
                if pattern.search(url):
                    return True

        return False

    def _matches(self, url: str, pattern: Union[str, Pattern[str]]) -> bool:
        """Check if URL matches pattern."""
        if isinstance(pattern, str):
            return pattern in url
        return bool(pattern.search(url))

    def _to_network_response(self, info: dict) -> NetworkResponse:
        """Convert internal dict to NetworkResponse."""
        return NetworkResponse(
            url=info.get("url", ""),
            status=info.get("status", 0),
            headers=info.get("headers", {}),
            body=info.get("body"),
            body_raw=info.get("body_raw", ""),
            mime_type=info.get("mime_type", ""),
            request_id=info.get("request_id", ""),
        )
