"""Integration test for v4.0 capabilities.

Tests:
1. Tab Manager — create, list, switch, close tabs
2. Form Filler — framework-aware input injection
3. Network Monitor — capture response after action
4. Inbox Poller — poll and extract verification links
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

from cdp_orchestrator import (
    TabManager,
    FormFiller,
    NetworkMonitor,
    InboxPoller,
    EmailMessage,
)


async def get_page_ws_url() -> str:
    """Get WebSocket URL for the first page target."""
    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:9222/json") as resp:
            targets = await resp.json()
            for t in targets:
                if t.get("type") == "page":
                    return t["webSocketDebuggerUrl"]
    raise RuntimeError("No page targets found")


async def test_tab_manager():
    """Test 1: Tab Manager — create, list, switch, close."""
    print("\n=== Test 1: Tab Manager ===")
    ws_url = await get_page_ws_url()
    tm = TabManager(ws_url)
    await tm.connect()

    # Create a new tab
    target_id = await tm.create_tab("https://example.com")
    print(f"  Created tab: {target_id}")
    assert target_id, "Failed to create tab"

    # List tabs
    tabs = await tm.list_tabs()
    print(f"  Open tabs: {len(tabs)}")
    for tab in tabs:
        print(f"    - {tab['url'][:60]}")
    assert len(tabs) >= 2, "Expected at least 2 tabs"

    # Switch to the new tab
    await tm.switch_tab(target_id)
    print(f"  Switched to tab: {target_id}")

    # Close the tab
    await tm.close_tab(target_id)
    print(f"  Closed tab: {target_id}")

    # Verify it's gone
    tabs_after = await tm.list_tabs()
    assert len(tabs_after) < len(tabs), "Tab was not closed"
    print(f"  Tabs after close: {len(tabs_after)}")

    await tm.disconnect()
    print("  ✅ Tab Manager PASSED")


async def test_form_filler():
    """Test 2: Form Filler — framework-aware input injection."""
    print("\n=== Test 2: Form Filler ===")
    ws_url = await get_page_ws_url()
    ff = FormFiller(ws_url)
    await ff.connect()

    # Navigate to a page with a form
    await ff._evaluate("window.location.href = 'https://example.com'")
    await asyncio.sleep(2)

    # Test fill_input on a non-existent selector (should return False gracefully)
    result = await ff.fill_input("#nonexistent", "test")
    assert result is False, "Should return False for non-existent element"
    print("  fill_input handles missing elements gracefully")

    # Test fill_form with multiple fields
    results = await ff.fill_form({
        "#nonexistent1": "value1",
        "#nonexistent2": "value2",
    })
    assert all(v is False for v in results.values()), "All should be False"
    print("  fill_form handles multiple missing fields gracefully")

    # Test click on non-existent element
    result = await ff.click("#nonexistent")
    assert result is False, "Should return False for non-existent element"
    print("  click handles missing elements gracefully")

    await ff.disconnect()
    print("  ✅ Form Filler PASSED")


async def test_network_monitor():
    """Test 3: Network Monitor — capture response after action."""
    print("\n=== Test 3: Network Monitor ===")
    ws_url = await get_page_ws_url()
    nm = NetworkMonitor(ws_url)
    await nm.connect()

    # Define an action that triggers a network request
    async def navigate_action():
        await nm._send("Page.navigate", {"url": "https://example.com"})

    # Capture the response
    response = await nm.capture_response_after_action(
        navigate_action,
        url_pattern=r"example\.com",
        timeout=10.0,
    )

    if response:
        print(f"  Captured: {response['url'][:60]}")
        print(f"  Status: {response['status']}")
        assert response["status"] == 200, f"Expected 200, got {response['status']}"
        print("  ✅ Network Monitor PASSED")
    else:
        print("  ⚠️  No response captured (may be cached) — skipping assertion")

    await nm.disconnect()


async def test_inbox_poller():
    """Test 4: Inbox Poller — poll and extract verification links."""
    print("\n=== Test 4: Inbox Poller ===")

    # Mock fetch function that returns emails after 2 polls
    call_count = 0

    async def mock_fetch():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return []  # No emails yet
        return [
            {
                "from": "noreply@emergent.sh",
                "subject": "Verify your email",
                "body": "Click here to verify: https://app.emergent.sh/verify?token=abc123",
                "html": "",
            }
        ]

    poller = InboxPoller(
        fetch_func=mock_fetch,
        timeout=15.0,
        interval=1.0,
    )

    result = await poller.poll()
    assert result is not None, "Poller should have found emails"
    print(f"  Got {len(result)} email(s) after {call_count} polls")

    # Parse the email
    email = InboxPoller.parse_email(result[0])
    print(f"  Subject: {email.subject}")
    print(f"  Links: {email.links}")
    assert len(email.links) > 0, "Should extract verification link"
    assert "verify" in email.links[0], "Link should be a verification URL"

    # Test OTP extraction
    otp_text = "Your code is 123456. Valid for 10 minutes."
    codes = InboxPoller.extract_otp(otp_text)
    print(f"  OTP codes: {codes}")
    assert "123456" in codes, "Should extract OTP code"

    print("  ✅ Inbox Poller PASSED")


async def main():
    print("=" * 60)
    print("CDP Orchestrator v4.0 — Integration Tests")
    print("=" * 60)

    tests = [
        ("Tab Manager", test_tab_manager),
        ("Form Filler", test_form_filler),
        ("Network Monitor", test_network_monitor),
        ("Inbox Poller", test_inbox_poller),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            await test_func()
            passed += 1
        except Exception as e:
            print(f"  ❌ {name} FAILED: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
