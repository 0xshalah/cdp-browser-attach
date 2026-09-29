"""Integration test for v4.1 capabilities.

Tests:
1. TurnstileHandler — detect token, iframe rect, inject token
2. DOMObserver — inject observer, capture mutations, wait for stability
3. FormSubmitter — submit with monitoring, detect errors, capture network
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

from cdp_orchestrator import (
    TurnstileHandler,
    DOMObserver,
    FormSubmitter,
)


async def get_page_ws_url() -> str:
    """Get WebSocket URL for the first page target."""
    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:9222/json") as resp:
            targets = await resp.json()
            for t in targets:
                if t.get("type") == "page" and "newtab" not in t.get("url", ""):
                    return t["webSocketDebuggerUrl"]
    raise RuntimeError("No page targets found")


async def test_turnstile_handler():
    """Test 1: TurnstileHandler — detect token, iframe rect, inject token."""
    print("\n=== Test 1: TurnstileHandler ===")
    ws_url = await get_page_ws_url()
    th = TurnstileHandler(ws_url)
    await th.connect()

    # Test get_token (should return None or empty on non-Turnstile page)
    token = await th.get_token()
    print(f"  Token: {token}")
    # Not asserting token presence — page may not have Turnstile

    # Test get_iframe_rect (should return None on non-Turnstile page)
    rect = await th.get_iframe_rect()
    print(f"  Iframe rect: {rect}")

    # Test inject_token (should succeed even without Turnstile)
    result = await th.inject_token("test_token_123")
    print(f"  Inject token: {result}")
    assert result is True, "inject_token should return True"

    # Verify token was injected
    token_after = await th.get_token()
    print(f"  Token after inject: {token_after}")
    assert token_after == "test_token_123", "Token should be injected"

    await th.disconnect()
    print("  ✅ TurnstileHandler PASSED")


async def test_dom_observer():
    """Test 2: DOMObserver — inject observer, capture mutations, wait for stability."""
    print("\n=== Test 2: DOMObserver ===")
    ws_url = await get_page_ws_url()
    obs = DOMObserver(ws_url)
    await obs.connect()

    # Inject observer
    await obs.inject_mutation_observer()
    print("  Observer injected")

    # Clear mutations
    await obs.clear_mutations()

    # Trigger a DOM change
    await obs._evaluate("""
        (function() {
            var div = document.createElement('div');
            div.id = 'test-mutation';
            div.textContent = 'test';
            document.body.appendChild(div);
            return true;
        })()
    """)

    await asyncio.sleep(0.5)

    # Get mutations
    mutations = await obs.get_mutations()
    print(f"  Mutations captured: {len(mutations)}")
    assert len(mutations) > 0, "Should capture at least one mutation"

    # Test wait_for_dom_stable
    stability = await obs.wait_for_dom_stable(quiet_ms=300, max_ms=5000)
    print(f"  DOM stability: {stability}")
    assert stability in ("quiet", "capped"), "Should return quiet or capped"

    # Test wait_for_selector
    found = await obs.wait_for_selector("#test-mutation", timeout=2000)
    print(f"  Selector found: {found}")
    assert found is True, "Should find the test element"

    # Cleanup
    await obs._evaluate("document.getElementById('test-mutation')?.remove()")
    await obs.disconnect_observer()

    await obs.disconnect()
    print("  ✅ DOMObserver PASSED")


async def test_form_submitter():
    """Test 3: FormSubmitter — submit with monitoring, detect errors, capture network."""
    print("\n=== Test 3: FormSubmitter ===")
    ws_url = await get_page_ws_url()
    fs = FormSubmitter(ws_url)
    await fs.connect()

    # Navigate to a simple page
    await fs._send("Page.navigate", {"url": "https://example.com"})
    await asyncio.sleep(3)

    # Define a submit action that triggers a network request
    async def navigate_action():
        await fs._send("Page.navigate", {"url": "https://httpbin.org/html"})

    # Submit with monitoring
    result = await fs.submit_with_monitoring(
        submit_action=navigate_action,
        url_pattern=r"httpbin\.org",
        stable_quiet_ms=300,
        stable_max_ms=5000,
    )

    print(f"  URL before: {result.url_before}")
    print(f"  URL after: {result.url_after}")
    print(f"  Success: {result.success}")
    print(f"  Errors: {len(result.errors)}")
    print(f"  Toasts: {len(result.toasts)}")
    print(f"  Network captures: {len(result.network_captures)}")
    print(f"  DOM stability: {result.dom_stability}")

    assert result.url_after != result.url_before, "URL should change after navigation"
    assert len(result.network_captures) > 0, "Should capture network requests"
    assert result.dom_stability in ("quiet", "capped"), "Should return stability status"

    # Test detect_errors (should be empty on httpbin)
    errors = await fs.detect_errors()
    print(f"  Errors detected: {len(errors)}")

    # Test detect_toasts (should be empty on httpbin)
    toasts = await fs.detect_toasts()
    print(f"  Toasts detected: {len(toasts)}")

    await fs.disconnect()
    print("  ✅ FormSubmitter PASSED")


async def main():
    print("=" * 60)
    print("CDP Orchestrator v4.1 — Integration Tests")
    print("=" * 60)

    tests = [
        ("TurnstileHandler", test_turnstile_handler),
        ("DOMObserver", test_dom_observer),
        ("FormSubmitter", test_form_submitter),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            await test_func()
            passed += 1
        except Exception as e:
            print(f"  ❌ {name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
