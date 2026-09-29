"""DOM observation and stability detection via CDP.

Research findings:
- MutationObserver fires within ~1ms of DOM change vs polling's interval/2
- DOM stability = no mutations for quiet period (300-500ms)
- Inject observer BEFORE action to capture all changes synchronously
- Always disconnect observer after use to avoid memory leaks
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class DOMObserver:
    """Monitor DOM changes and detect stability via CDP."""

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
        logger.info("DOMObserver connected")

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
            logger.error(f"DOMObserver listener error: {e}")

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

    async def inject_mutation_observer(self) -> None:
        """Inject MutationObserver on document.body to track DOM changes."""
        await self._evaluate("""
            (function() {
                window.__cdp_mutations = [];
                window.__cdp_observer = new MutationObserver(function(mutations) {
                    for (const m of mutations) {
                        if (m.type === 'childList' && m.addedNodes.length > 0) {
                            window.__cdp_mutations.push({
                                type: 'added',
                                target: m.target.tagName || m.target.nodeName,
                                count: m.addedNodes.length,
                                timestamp: Date.now()
                            });
                        } else if (m.type === 'attributes') {
                            window.__cdp_mutations.push({
                                type: 'attr',
                                target: m.target.tagName || m.target.nodeName,
                                attr: m.attributeName,
                                timestamp: Date.now()
                            });
                        }
                    }
                });
                window.__cdp_observer.observe(document.body, {
                    childList: true,
                    subtree: true,
                    attributes: true,
                    attributeFilter: ['class', 'aria-invalid', 'role', 'style', 'hidden']
                });
                return true;
            })()
        """)
        logger.info("MutationObserver injected")

    async def get_mutations(self) -> List[Dict]:
        """Get captured mutations since last call."""
        result = await self._evaluate("window.__cdp_mutations || []")
        return result if result is not None else []

    async def clear_mutations(self) -> None:
        """Clear mutation buffer."""
        await self._evaluate("window.__cdp_mutations = []")

    async def disconnect_observer(self) -> None:
        """Disconnect MutationObserver to free resources."""
        await self._evaluate("""
            (function() {
                if (window.__cdp_observer) {
                    window.__cdp_observer.disconnect();
                    window.__cdp_observer = null;
                }
                return true;
            })()
        """)

    async def wait_for_dom_stable(
        self,
        quiet_ms: int = 500,
        max_ms: int = 10000,
    ) -> str:
        """Wait until DOM stops changing for quiet_ms period.

        Returns:
            'quiet' — DOM stabilized before max_ms
            'capped' — reached max_ms timeout
        """
        result = await self._evaluate(f"""
            (function() {{
                return new Promise(function(resolve) {{
                    var timer = null;
                    var obs = new MutationObserver(function() {{
                        clearTimeout(timer);
                        timer = setTimeout(function() {{
                            obs.disconnect();
                            resolve('quiet');
                        }}, {quiet_ms});
                    }});
                    obs.observe(document.body, {{
                        childList: true,
                        subtree: true,
                        attributes: true
                    }});
                    timer = setTimeout(function() {{
                        obs.disconnect();
                        resolve('quiet');
                    }}, {quiet_ms});
                    setTimeout(function() {{
                        obs.disconnect();
                        resolve('capped');
                    }}, {max_ms});
                }});
            }})()
        """)
        logger.info(f"DOM stability: {result} (quiet={quiet_ms}ms, max={max_ms}ms)")
        return result

    async def wait_for_selector(
        self,
        selector: str,
        timeout: int = 10000,
    ) -> bool:
        """Wait for a selector to appear in the DOM."""
        result = await self._evaluate(f"""
            (function() {{
                return new Promise(function(resolve) {{
                    var start = Date.now();
                    var check = function() {{
                        var el = document.querySelector({repr(selector)});
                        if (el) return resolve(true);
                        if (Date.now() - start > {timeout}) return resolve(false);
                        setTimeout(check, 100);
                    }};
                    check();
                }});
            }})()
        """)
        return bool(result)

    async def wait_for_selector_removed(
        self,
        selector: str,
        timeout: int = 10000,
    ) -> bool:
        """Wait for a selector to be removed from the DOM."""
        result = await self._evaluate(f"""
            (function() {{
                return new Promise(function(resolve) {{
                    var start = Date.now();
                    var check = function() {{
                        var el = document.querySelector({repr(selector)});
                        if (!el) return resolve(true);
                        if (Date.now() - start > {timeout}) return resolve(false);
                        setTimeout(check, 100);
                    }};
                    check();
                }});
            }})()
        """)
        return bool(result)

    async def capture_mutations_during(
        self,
        action: Callable,
        wait_after_ms: int = 2000,
    ) -> List[Dict]:
        """Inject observer, run action, wait, then collect mutations."""
        await self.inject_mutation_observer()
        await self.clear_mutations()

        await action()

        await asyncio.sleep(wait_after_ms / 1000)
        mutations = await self.get_mutations()
        await self.disconnect_observer()

        return mutations

    async def disconnect(self) -> None:
        await self.disconnect_observer()
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
        logger.info("DOMObserver disconnected")
