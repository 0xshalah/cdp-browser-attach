"""Form submission with full monitoring — network, errors, DOM stability.

Research findings:
- Capture ALL network requests (not just responses) via Network.requestWillBeSent
- POST body available in params.request.postData
- Request headers in params.request.headers
- Cookies in Network.requestWillBeSentExtraInfo
- Detect errors via [role="alert"], [aria-invalid="true"], toast selectors
- Use MutationObserver for DOM stability detection (faster than polling)
- Never use fixed setTimeout waits — use MutationObserver-based detection
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class FormError:
    """Represents a form validation error."""
    type: str  # 'alert', 'field', 'class', 'toast'
    text: str
    field: str = ""
    selector: str = ""


@dataclass
class NetworkCapture:
    """Captured network request/response pair."""
    url: str
    method: str
    status: Optional[int] = None
    request_headers: Dict[str, str] = field(default_factory=dict)
    response_headers: Dict[str, str] = field(default_factory=dict)
    post_data: Optional[str] = None
    response_body: Optional[str] = None
    cookies: List[Dict] = field(default_factory=list)


@dataclass
class SubmissionResult:
    """Result of a form submission with full monitoring."""
    success: bool
    url_before: str
    url_after: str
    errors: List[FormError] = field(default_factory=list)
    network_captures: List[NetworkCapture] = field(default_factory=list)
    dom_stability: str = ""
    toasts: List[str] = field(default_factory=list)


class FormSubmitter:
    """Submit forms with full monitoring — network, errors, DOM stability."""

    def __init__(self, ws_url: str) -> None:
        self._ws_url = ws_url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._msg_id = 0
        self._pending_commands: Dict[int, asyncio.Future] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._requests: Dict[str, Dict[str, Any]] = {}

    async def connect(self) -> None:
        if "/devtools/browser/" in self._ws_url:
            self._ws_url = await self._resolve_page_ws()
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url)
        self._listen_task = asyncio.create_task(self._listen_loop())
        await self._send("Network.enable")
        logger.info("FormSubmitter connected")

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
                    continue

                method = data.get("method", "")
                params = data.get("params", {})

                if method == "Network.requestWillBeSent":
                    self._handle_request(params)
                elif method == "Network.requestWillBeSentExtraInfo":
                    self._handle_request_extra(params)
                elif method == "Network.responseReceived":
                    self._handle_response(params)
                elif method == "Network.loadingFinished":
                    asyncio.create_task(self._fetch_body(params["requestId"]))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"FormSubmitter listener error: {e}")

    def _handle_request(self, params: Dict) -> None:
        req_id = params.get("requestId")
        req = params.get("request", {})
        self._requests[req_id] = {
            "url": req.get("url", ""),
            "method": req.get("method", ""),
            "request_headers": req.get("headers", {}),
            "post_data": req.get("postData"),
            "status": None,
            "response_headers": {},
            "response_body": None,
            "cookies": [],
        }

    def _handle_request_extra(self, params: Dict) -> None:
        req_id = params.get("requestId")
        if req_id in self._requests:
            self._requests[req_id]["cookies"] = params.get("cookies", [])

    def _handle_response(self, params: Dict) -> None:
        req_id = params.get("requestId")
        if req_id in self._requests:
            resp = params.get("response", {})
            self._requests[req_id]["status"] = resp.get("status")
            self._requests[req_id]["response_headers"] = resp.get("headers", {})

    async def _fetch_body(self, request_id: str) -> None:
        if request_id not in self._requests:
            return
        try:
            body_result = await self._send("Network.getResponseBody", {"requestId": request_id})
            body = body_result.get("body", "")
            if body_result.get("base64Encoded"):
                import base64
                body = base64.b64decode(body).decode("utf-8", errors="replace")
            self._requests[request_id]["response_body"] = body
        except Exception as e:
            logger.debug(f"Failed to get body for {request_id}: {e}")

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

    async def detect_errors(self) -> List[FormError]:
        """Detect form validation errors — inline, alert, toast."""
        result = await self._evaluate("""
            (function() {
                var errors = [];

                // Method 1: role="alert" elements (React Hook Form, accessible forms)
                document.querySelectorAll('[role="alert"]').forEach(function(el) {
                    if (el.offsetParent !== null) {
                        errors.push({type: 'alert', text: el.textContent.trim()});
                    }
                });

                // Method 2: aria-invalid inputs
                document.querySelectorAll('[aria-invalid="true"]').forEach(function(input) {
                    var errorId = input.getAttribute('aria-describedby');
                    var errorEl = errorId ? document.getElementById(errorId) : null;
                    errors.push({
                        type: 'field',
                        field: input.name || input.id || '',
                        text: errorEl ? errorEl.textContent.trim() : 'Invalid'
                    });
                });

                // Method 3: Common error class patterns
                var errorSelectors = [
                    '.error', '.field-error', '.invalid-feedback',
                    '.form-error', '[data-error]', '.text-red-500', '.text-danger'
                ];
                errorSelectors.forEach(function(sel) {
                    document.querySelectorAll(sel).forEach(function(el) {
                        if (el.offsetParent !== null && el.textContent.trim()) {
                            errors.push({type: 'class', selector: sel, text: el.textContent.trim()});
                        }
                    });
                });

                return errors;
            })()
        """)
        errors = []
        for item in (result or []):
            errors.append(FormError(
                type=item.get("type", ""),
                text=item.get("text", ""),
                field=item.get("field", ""),
                selector=item.get("selector", ""),
            ))
        return errors

    async def detect_toasts(self) -> List[str]:
        """Detect toast/notification messages."""
        result = await self._evaluate("""
            (function() {
                var toasts = [];
                var selectors = [
                    '[role="status"]',
                    '[data-sonner-toast]',
                    '.Toastify__toast',
                    '.toast',
                    '.notification',
                ];
                selectors.forEach(function(sel) {
                    document.querySelectorAll(sel).forEach(function(el) {
                        if (el.offsetParent !== null) {
                            var text = el.textContent.trim();
                            if (text) toasts.push(text);
                        }
                    });
                });
                return toasts;
            })()
        """)
        return result or []

    async def wait_for_dom_stable(
        self,
        quiet_ms: int = 500,
        max_ms: int = 10000,
    ) -> str:
        """Wait until DOM stops changing."""
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
        return result or "capped"

    async def get_captures(self, filter_pattern: Optional[str] = None) -> List[NetworkCapture]:
        """Get all captured network requests, optionally filtered by URL pattern."""
        captures = []
        pattern = re.compile(filter_pattern) if filter_pattern else None

        for req_id, req in self._requests.items():
            if pattern and not pattern.search(req["url"]):
                continue
            # Skip tracking/analytics
            if any(x in req["url"] for x in [
                "analytics", "tracking", "pixel", "google", "facebook",
                "tiktok", "segment", "amplitude", "mixpanel", "hotjar",
                "clarity", "doubleclick", "ads", "telemetry", "metrics",
            ]):
                continue
            captures.append(NetworkCapture(
                url=req["url"],
                method=req["method"],
                status=req["status"],
                request_headers=req["request_headers"],
                response_headers=req["response_headers"],
                post_data=req["post_data"],
                response_body=req["response_body"],
                cookies=req["cookies"],
            ))
        return captures

    async def submit_with_monitoring(
        self,
        submit_action: Callable,
        url_pattern: Optional[str] = None,
        stable_quiet_ms: int = 500,
        stable_max_ms: int = 10000,
    ) -> SubmissionResult:
        """Submit form with full monitoring.

        Args:
            submit_action: Async callable that performs the submit (e.g., click button).
            url_pattern: Regex to filter captured network requests.
            stable_quiet_ms: DOM stability quiet period.
            stable_max_ms: DOM stability max wait.

        Returns:
            SubmissionResult with success status, errors, network captures.
        """
        url_before = await self._evaluate("window.location.href")

        # Clear previous captures
        self._requests.clear()

        # Execute submit action
        await submit_action()

        # Wait for DOM stability
        dom_stability = await self.wait_for_dom_stable(stable_quiet_ms, stable_max_ms)

        # Wait for network to settle
        await asyncio.sleep(2)

        # Detect errors
        errors = await self.detect_errors()
        toasts = await self.detect_toasts()

        # Get network captures
        captures = await self.get_captures(url_pattern)

        url_after = await self._evaluate("window.location.href")

        # Determine success
        success = (
            url_after != url_before  # Navigated away = success
            or len(errors) == 0  # No errors detected
            or any(c.status == 200 and c.response_body and "error" not in c.response_body.lower() for c in captures)
        )

        return SubmissionResult(
            success=success,
            url_before=url_before or "",
            url_after=url_after or "",
            errors=errors,
            network_captures=captures,
            dom_stability=dom_stability,
            toasts=toasts,
        )

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
        logger.info("FormSubmitter disconnected")
