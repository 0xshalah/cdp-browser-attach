"""Cloudflare Turnstile handling via CDP.

Research findings:
- Non-interactive Turnstile auto-solves on good IPs within 3-8 seconds
- Token appears in hidden input[name="cf-turnstile-response"]
- window.turnstile.getResponse() returns token if widget already passed
- Fallback: coordinate-based click via Input.dispatchMouseEvent
- CDP clicks are faster but less trusted; OS-level clicks more realistic
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


class TurnstileHandler:
    """Handle Cloudflare Turnstile challenges via CDP."""

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._msg_id = 0
        self._pending_commands: Dict[int, asyncio.Future] = {}
        self._listen_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if "/devtools/browser/" in self._ws_url:
            self._ws_url = await self._resolve_page_ws()
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url)
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("TurnstileHandler connected")

    async def _resolve_page_ws(self) -> str:
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
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_commands[msg_id] = future
        await self._ws.send_json({"id": msg_id, "method": method, "params": params or {}})
        try:
            result = await future
            if "error" in result:
                raise RuntimeError(f"CDP error: {result['error']}")
            return result.get("result", {})
        finally:
            self._pending_commands.pop(msg_id, None)

    async def _listen_loop(self) -> None:
        try:
            async for msg in self._ws:
                data = msg.json()
                msg_id = data.get("id")
                if msg_id is not None and msg_id in self._pending_commands:
                    future = self._pending_commands[msg_id]
                    if not future.done():
                        future.set_result(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"TurnstileHandler listener error: {e}")

    async def _evaluate(self, expression: str) -> Any:
        result = await self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        val = result.get("result", {})
        if val.get("type") == "undefined":
            return None
        return val.get("value")

    async def get_token(self) -> Optional[str]:
        """Get Turnstile token from hidden input or widget API."""
        # Method 1: Check hidden input
        token = await self._evaluate("""
            (function() {
                const el = document.querySelector('[name="cf-turnstile-response"]');
                return el ? el.value : '';
            })()
        """)
        if token:
            return token

        # Method 2: Check widget API
        token = await self._evaluate("""
            (function() {
                if (window.turnstile && window.turnstile.getResponse) {
                    return window.turnstile.getResponse();
                }
                return '';
            })()
        """)
        if token:
            return token

        return None

    async def wait_for_token(self, timeout: float = 30.0) -> Optional[str]:
        """Wait for Turnstile token to appear (auto-solve or manual)."""
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            token = await self.get_token()
            if token:
                logger.info(f"Turnstile token acquired after {asyncio.get_event_loop().time() - start:.1f}s")
                return token
            await asyncio.sleep(0.5)
        logger.warning(f"Turnstile token not found within {timeout}s")
        return None

    async def get_iframe_rect(self) -> Optional[Dict[str, float]]:
        """Get bounding rect of Cloudflare Turnstile iframe."""
        return await self._evaluate("""
            (function() {
                for (const f of document.querySelectorAll('iframe')) {
                    if (f.src && f.src.includes('challenges.cloudflare.com')) {
                        const r = f.getBoundingClientRect();
                        return {x: r.left, y: r.top, width: r.width, height: r.height};
                    }
                }
                return null;
            })()
        """)

    async def click_checkbox(self, iframe_rect: Dict[str, float]) -> bool:
        """Click Turnstile checkbox using coordinate-based mouse events.

        Uses human-like movement with random jitter to avoid synthetic-click detection.
        """
        cx = iframe_rect["x"] + 24
        cy = iframe_rect["y"] + iframe_rect["height"] / 2

        # Human-like approach: move from above-left with jitter
        start_x = cx - 60 + random.uniform(-10, 10)
        start_y = cy - 40 + random.uniform(-10, 10)

        # Move to position in steps
        steps = 5
        for i in range(1, steps + 1):
            ix = start_x + (cx - start_x) * i / steps + random.uniform(-2, 2)
            iy = start_y + (cy - start_y) * i / steps + random.uniform(-2, 2)
            await self._send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": ix,
                "y": iy,
            })
            await asyncio.sleep(random.uniform(0.01, 0.05))

        # Click
        await self._send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": cx,
            "y": cy,
            "button": "left",
            "clickCount": 1,
        })
        await asyncio.sleep(0.05)
        await self._send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": cx,
            "y": cy,
            "button": "left",
            "clickCount": 1,
        })

        logger.info(f"Clicked Turnstile checkbox at ({cx}, {cy})")
        return True

    async def solve(self, timeout: float = 60.0) -> Optional[str]:
        """Full Turnstile solve flow: wait for auto-solve, fallback to click."""
        # Phase 1: Wait for auto-solve (non-interactive mode)
        logger.info("Phase 1: Waiting for Turnstile auto-solve...")
        token = await self.wait_for_token(timeout=min(timeout, 15.0))
        if token:
            return token

        # Phase 2: Try clicking the checkbox
        logger.info("Phase 2: Attempting checkbox click...")
        iframe_rect = await self.get_iframe_rect()
        if iframe_rect:
            await self.click_checkbox(iframe_rect)
            token = await self.wait_for_token(timeout=timeout - 15.0)
            if token:
                return token

        # Phase 3: Last resort — wait longer
        logger.info("Phase 3: Waiting for challenge to resolve...")
        token = await self.wait_for_token(timeout=10.0)
        return token

    async def inject_token(self, token: str) -> bool:
        """Inject a Turnstile token into the page (e.g., from external solver)."""
        result = await self._evaluate(f"""
            (function() {{
                let input = document.querySelector('[name="cf-turnstile-response"]');
                if (!input) {{
                    input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = 'cf-turnstile-response';
                    (document.querySelector('form') || document.body).appendChild(input);
                }}
                input.value = {repr(token)};
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                // Fire callback if exists
                const sitekey = document.querySelector('[data-sitekey]');
                if (sitekey) {{
                    const cbName = sitekey.getAttribute('data-callback');
                    if (cbName && typeof window[cbName] === 'function') {{
                        window[cbName]({repr(token)});
                    }}
                }}
                return true;
            }})()
        """)
        return bool(result)

    async def disconnect(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        for future in self._pending_commands.values():
            if not future.done():
                future.cancel()
        self._pending_commands.clear()
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("TurnstileHandler disconnected")
