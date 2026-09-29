"""Hybrid API Sniffer & Executor — combine interceptor + session bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from .network_interceptor import NetworkInterceptor, InterceptorConfig, NetworkResponse
from .session_bridge import SessionBridge

logger = logging.getLogger(__name__)


@dataclass
class SniffedAPI:
    """Represents a sniffed API endpoint."""
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    body: Any = None
    response_example: Any = None


class APISniffer:
    """Sniff API endpoints from browser traffic and execute them natively.

    Workflow:
    1. Navigate to target URL via browser
    2. Capture API calls via NetworkInterceptor
    3. Extract auth headers/cookies via SessionBridge
    4. Execute API calls natively via aiohttp
    """

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._interceptor: Optional[NetworkInterceptor] = None
        self._bridge: Optional[SessionBridge] = None
        self._sniffed_apis: list[SniffedAPI] = []
        self._auth_headers: dict[str, str] = {}
        self._cookies: dict[str, str] = {}

    async def connect(self) -> None:
        """Connect all sub-modules."""
        self._interceptor = NetworkInterceptor(
            self._ws_url,
            config=InterceptorConfig(
                content_types=["application/json", "text/plain"],
                capture_all=True,
            ),
        )
        await self._interceptor.connect()

        self._bridge = SessionBridge(self._ws_url)
        await self._bridge.connect()

        logger.info("APISniffer connected")

    async def disconnect(self) -> None:
        """Disconnect all sub-modules."""
        if self._interceptor:
            await self._interceptor.disconnect()
        if self._bridge:
            await self._bridge.disconnect()
        logger.info("APISniffer disconnected")

    async def sniff_from_navigation(
        self,
        url: str,
        wait_seconds: float = 5.0,
    ) -> list[SniffedAPI]:
        """Navigate to URL and sniff API calls.

        Args:
            url: Target URL to navigate to
            wait_seconds: How long to wait for API calls

        Returns:
            List of sniffed API endpoints
        """
        self._sniffed_apis = []

        # Setup listener for API responses
        async def _on_response(resp: NetworkResponse) -> None:
            if self._is_api_call(resp.url):
                api = SniffedAPI(
                    url=resp.url,
                    headers=resp.headers,
                    body=resp.body,
                    response_example=resp.body,
                )
                self._sniffed_apis.append(api)
                logger.info("Sniffed API: %s", resp.url)

        if self._interceptor:
            self._interceptor.add_listener(_on_response)

        # Navigate via CDP
        await self._navigate(url)

        # Wait for API calls
        await asyncio.sleep(wait_seconds)

        # Extract auth
        await self._extract_auth()

        logger.info("Sniffed %d API endpoints", len(self._sniffed_apis))
        return self._sniffed_apis

    async def execute_api(
        self,
        url: str,
        method: str = "GET",
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        extra_headers: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Execute API call natively via aiohttp.

        Args:
            url: API endpoint URL
            method: HTTP method
            params: Query parameters
            json_body: JSON request body
            extra_headers: Additional headers

        Returns:
            API response as dictionary
        """
        headers = {}
        headers.update(self._auth_headers)
        if extra_headers:
            headers.update(extra_headers)

        cookies = dict(self._cookies)

        async with aiohttp.ClientSession() as session:
            async with session.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=headers,
                cookies=cookies,
            ) as resp:
                text = await resp.text()
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"status": resp.status, "body": text}

    async def _navigate(self, url: str) -> None:
        """Navigate browser to URL via CDP."""
        # Use Runtime.evaluate to navigate
        session = aiohttp.ClientSession()
        ws = await session.ws_connect(self._ws_url)
        try:
            await ws.send_json({
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": url},
            })
            # Wait for response
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("id") == 1:
                        break
        finally:
            await ws.close()
            await session.close()

    async def _extract_auth(self) -> None:
        """Extract auth headers and cookies from browser."""
        if self._bridge:
            self._auth_headers = await self._bridge.get_auth_headers()
            cookies_list = await self._bridge.export_cookies()
            self._cookies = {
                c["name"]: c["value"]
                for c in cookies_list
                if c.get("name") and c.get("value")
            }

    def _is_api_call(self, url: str) -> bool:
        """Check if URL is likely an API call."""
        parsed = urlparse(url)
        path = parsed.path.lower()

        # Common API patterns
        api_patterns = [
            r"/api/",
            r"/v\d+/",
            r"/graphql",
            r"/rest/",
            r"/json",
            r"\.json$",
        ]

        for pattern in api_patterns:
            if re.search(pattern, path):
                return True

        # Exclude static assets
        static_exts = [".js", ".css", ".png", ".jpg", ".svg", ".woff", ".ico"]
        if any(path.endswith(ext) for ext in static_exts):
            return False

        return False

    @property
    def sniffed_apis(self) -> list[SniffedAPI]:
        return self._sniffed_apis

    @property
    def auth_headers(self) -> dict[str, str]:
        return self._auth_headers

    @property
    def cookies(self) -> dict[str, str]:
        return self._cookies
