"""Deep Runtime Evaluator — direct JS execution and DOM querying via CDP."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class RuntimeEvaluator:
    """Execute JavaScript and query DOM via CDP Runtime.evaluate."""

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
        logger.info("RuntimeEvaluator connected to %s", self._ws_url)

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
        logger.info("RuntimeEvaluator disconnected")

    async def evaluate_js(
        self,
        expression: str,
        await_promise: bool = True,
        return_by_value: bool = True,
        timeout: float = 10.0,
    ) -> Any:
        """Execute JavaScript and return result.

        Args:
            expression: JavaScript expression to evaluate
            await_promise: Whether to await Promise results
            return_by_value: Return value by value (serialized)
            timeout: Timeout in seconds

        Returns:
            Evaluated result (parsed JSON or raw value)
        """
        result = await self._send("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": await_promise,
            "returnByValue": return_by_value,
        }, timeout=timeout)

        if result.get("exceptionDetails"):
            exc = result["exceptionDetails"]
            raise RuntimeError(
                f"JS Error: {exc.get('text', 'Unknown')} — "
                f"{exc.get('exception', {}).get('description', '')}"
            )

        remote_obj = result.get("result", {})
        if remote_obj.get("type") == "undefined":
            return None

        value = remote_obj.get("value")
        if value is not None:
            return value

        # Handle objects without value but with description
        if remote_obj.get("description"):
            return remote_obj["description"]

        return None

    async def query_selector_value(
        self,
        selector: str,
        attribute: str = "innerText",
        timeout: float = 5.0,
    ) -> str:
        """Query DOM element and extract attribute value.

        Args:
            selector: CSS selector
            attribute: Attribute to extract (innerText, innerHTML, value, etc.)
            timeout: Timeout in seconds

        Returns:
            Extracted attribute value as string
        """
        expression = f"""
        (function() {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) return null;
            return el[{json.dumps(attribute)}];
        }})()
        """
        result = await self.evaluate_js(expression, timeout=timeout)
        return str(result) if result is not None else ""

    async def extract_framework_state(self) -> dict[str, Any]:
        """Extract common SPA framework states.

        Returns:
            Dictionary with detected framework states
        """
        expression = """
        (function() {
            const state = {};

            // Next.js
            if (window.__NEXT_DATA__) {
                state.__NEXT_DATA__ = window.__NEXT_DATA__;
            }

            // Nuxt.js
            if (window.__NUXT__) {
                state.__NUXT__ = window.__NUXT__;
            }

            // Redux store
            if (window.__REDUX_DEVTOOLS_EXTENSION__ || window.store) {
                try {
                    const store = window.store || window.__store__;
                    if (store && store.getState) {
                        state.redux = store.getState();
                    }
                } catch (e) {}
            }

            // Vue
            if (window.__VUE__ || window.$store) {
                try {
                    const app = document.querySelector('#app');
                    if (app && app.__vue_app__) {
                        state.vue = app.__vue_app__.$data || {};
                    }
                } catch (e) {}
            }

            // Angular
            if (window.getAllAngularRootElements) {
                try {
                    const root = window.getAllAngularRootElements()[0];
                    if (root && root.injector) {
                        state.angular = {};
                    }
                } catch (e) {}
            }

            // Generic initial state
            if (window.__INITIAL_STATE__) {
                state.__INITIAL_STATE__ = window.__INITIAL_STATE__;
            }

            return state;
        })()
        """
        result = await self.evaluate_js(expression)
        return result if isinstance(result, dict) else {}

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
