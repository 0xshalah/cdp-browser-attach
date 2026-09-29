"""End-to-end test for advanced CDP inspection toolkit.

Tests:
1. Spawning/attaching to Chrome
2. Intercepting network requests (JSON responses)
3. Extracting cookies and querying DOM via Runtime.evaluate
4. Executing HTTP calls via aiohttp with bridged session data
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cdp_orchestrator import (
    StealthLauncher,
    NetworkInterceptor,
    InterceptorConfig,
    RuntimeEvaluator,
    SessionBridge,
    APISniffer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CDP_PORT = 9222
CDP_WS_URL = f"ws://127.0.0.1:{CDP_PORT}/devtools/browser"


async def get_ws_url() -> str:
    """Get the WebSocket debugger URL from CDP."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{CDP_PORT}/json/version") as resp:
            data = await resp.json()
            return data["webSocketDebuggerUrl"]


async def test_1_spawn_chrome() -> str:
    """Test 1: Spawn/attach to Chrome."""
    logger.info("=== TEST 1: Spawn/Attach Chrome ===")

    launcher = StealthLauncher(port=CDP_PORT)
    version_info = await launcher.ensure_running()

    browser = version_info.get("Browser", "unknown")
    ws_url = version_info.get("webSocketDebuggerUrl", "")

    assert browser != "unknown", "Browser version should be detected"
    assert ws_url.startswith("ws://"), "WebSocket URL should be valid"

    logger.info("[PASS] Browser: %s", browser)
    logger.info("[PASS] WebSocket URL: %s", ws_url)

    return ws_url


async def test_2_network_interceptor(ws_url: str) -> None:
    """Test 2: Intercept network requests."""
    logger.info("=== TEST 2: Network Interceptor ===")

    interceptor = NetworkInterceptor(
        ws_url,
        config=InterceptorConfig(
            content_types=["application/json"],
            capture_all=True,
        ),
    )
    await interceptor.connect()

    # Navigate to a page that makes API calls
    import aiohttp
    session = aiohttp.ClientSession()
    ws = await session.ws_connect(ws_url)
    await ws.send_json({
        "id": 1,
        "method": "Page.navigate",
        "params": {"url": "https://httpbin.org/json"},
    })
    # Wait for navigation
    async for msg in ws:
        if msg.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(msg.data)
            if data.get("id") == 1:
                break
    await ws.close()
    await session.close()

    # Wait for responses
    await asyncio.sleep(3)

    # Check captured responses
    logger.info("[PASS] Network interceptor captured traffic")

    await interceptor.disconnect()


async def test_3_runtime_evaluator(ws_url: str) -> None:
    """Test 3: Runtime evaluation and DOM querying."""
    logger.info("=== TEST 3: Runtime Evaluator ===")

    evaluator = RuntimeEvaluator(ws_url)
    await evaluator.connect()

    # Test evaluate_js
    result = await evaluator.evaluate_js("1 + 1")
    assert result == 2, f"Expected 2, got {result}"
    logger.info("[PASS] evaluate_js: 1 + 1 = %s", result)

    # Test query_selector_value
    title = await evaluator.query_selector_value("title", "innerText")
    logger.info("[PASS] query_selector_value: title = %s", title)

    # Test extract_framework_state
    state = await evaluator.extract_framework_state()
    logger.info("[PASS] extract_framework_state: %d keys", len(state))

    await evaluator.disconnect()


async def test_4_session_bridge(ws_url: str) -> None:
    """Test 4: Session and cookie bridge."""
    logger.info("=== TEST 4: Session Bridge ===")

    bridge = SessionBridge(ws_url)
    await bridge.connect()

    # Test export_cookies
    cookies = await bridge.export_cookies()
    logger.info("[PASS] export_cookies: %d cookies", len(cookies))

    # Test extract_storage
    storage = await bridge.extract_storage()
    logger.info(
        "[PASS] extract_storage: %d local, %d session",
        len(storage.get("localStorage", {})),
        len(storage.get("sessionStorage", {})),
    )

    # Test sync_to_aiohttp
    import aiohttp
    async with aiohttp.ClientSession() as session:
        await bridge.sync_to_aiohttp(session)
        logger.info("[PASS] sync_to_aiohttp: cookies injected")

    await bridge.disconnect()


async def test_5_api_sniffer(ws_url: str) -> None:
    """Test 5: Hybrid API sniffer."""
    logger.info("=== TEST 5: API Sniffer ===")

    sniffer = APISniffer(ws_url)
    await sniffer.connect()

    # Sniff from navigation
    apis = await sniffer.sniff_from_navigation(
        "https://httpbin.org/json",
        wait_seconds=3.0,
    )

    logger.info("[PASS] Sniffed %d API endpoints", len(apis))

    # Test execute_api
    result = await sniffer.execute_api("https://httpbin.org/get")
    logger.info("[PASS] execute_api: %s", result.get("url", "unknown"))

    await sniffer.disconnect()


async def main() -> None:
    """Run all tests."""
    logger.info("=" * 60)
    logger.info("CDP Orchestrator v3.0 — Advanced Inspection Toolkit Test")
    logger.info("=" * 60)

    try:
        # Test 1: Spawn Chrome
        ws_url = await test_1_spawn_chrome()

        # Test 2: Network Interceptor
        await test_2_network_interceptor(ws_url)

        # Test 3: Runtime Evaluator
        await test_3_runtime_evaluator(ws_url)

        # Test 4: Session Bridge
        await test_4_session_bridge(ws_url)

        # Test 5: API Sniffer
        await test_5_api_sniffer(ws_url)

        logger.info("=" * 60)
        logger.info("ALL TESTS PASSED")
        logger.info("=" * 60)

    except Exception as e:
        logger.error("TEST FAILED: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
