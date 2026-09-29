"""Framework-aware form filling for React/Vue/Svelte SPAs."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class FormFiller:
    """Fill form fields using native value setters for framework compatibility."""

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._msg_id = 0

    async def connect(self) -> None:
        if "/devtools/browser/" in self._ws_url:
            self._ws_url = await self._resolve_page_ws()
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url)
        logger.info("FormFiller connected")

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

    async def _evaluate(self, expression: str) -> Any:
        if not self._ws:
            raise RuntimeError("Not connected")
        self._msg_id += 1
        msg_id = self._msg_id
        await self._ws.send_json({
            "id": msg_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        })
        async for msg in self._ws:
            data = msg.json()
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"CDP error: {data['error']}")
                result = data.get("result", {}).get("result", {})
                if result.get("type") == "undefined":
                    return None
                return result.get("value")
        raise RuntimeError("WebSocket closed")

    async def fill_input(self, selector: str, value: str) -> bool:
        """Fill a text input or textarea using native value setter."""
        js = f"""
        (function() {{
            const input = document.querySelector('{selector}');
            if (!input) return false;
            const tag = input.tagName.toLowerCase();
            const nativeSetter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            )?.set || Object.getOwnPropertyDescriptor(
                window.HTMLTextAreaElement.prototype, 'value'
            )?.set;
            if (nativeSetter) {{
                nativeSetter.call(input, {repr(value)});
            }} else {{
                input.value = {repr(value)};
            }}
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            input.dispatchEvent(new Event('blur', {{ bubbles: true }}));
            return true;
        }})()
        """
        result = await self._evaluate(js)
        if result:
            logger.info(f"Filled {selector}")
        else:
            logger.warning(f"Input not found: {selector}")
        return bool(result)

    async def select_option(self, selector: str, value: str) -> bool:
        """Select an option in a dropdown by value."""
        js = f"""
        (function() {{
            const select = document.querySelector('{selector}');
            if (!select) return false;
            select.value = {repr(value)};
            select.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return true;
        }})()
        """
        result = await self._evaluate(js)
        return bool(result)

    async def click(self, selector: str) -> bool:
        """Click an element."""
        js = f"""
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return false;
            el.click();
            return true;
        }})()
        """
        result = await self._evaluate(js)
        return bool(result)

    async def fill_form(self, fields: Dict[str, str]) -> Dict[str, bool]:
        """Fill multiple form fields at once.
        Keys are CSS selectors, values are input values.
        """
        results = {}
        for selector, value in fields.items():
            results[selector] = await self.fill_input(selector, value)
        return results

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("FormFiller disconnected")
