"""Extract verification link from email iframe and navigate to it."""
import asyncio
import sys
import os
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdp_orchestrator import FormFiller, TabManager


async def main():
    import aiohttp

    # Get browser WS URL
    async with aiohttp.ClientSession() as session:
        async with session.get('http://127.0.0.1:9222/json/version') as resp:
            data = await resp.json()
            browser_ws = data['webSocketDebuggerUrl']

    # Get etempmail tab WS
    async with aiohttp.ClientSession() as session:
        async with session.get('http://127.0.0.1:9222/json') as resp:
            targets = await resp.json()
            email_ws = None
            for t in targets:
                if 'etempmail' in t.get('url', ''):
                    email_ws = t['webSocketDebuggerUrl']
                    break

    if not email_ws:
        print('No etempmail tab found')
        return

    ff = FormFiller(email_ws)
    await ff.connect()

    # Extract verification link from iframe
    print('Extracting verification link from email iframe...')
    links = await ff._evaluate("""
        (function() {
            const iframes = document.querySelectorAll('iframe');
            for (const iframe of iframes) {
                try {
                    const doc = iframe.contentDocument || iframe.contentWindow.document;
                    if (doc) {
                        const anchors = doc.querySelectorAll('a');
                        for (const a of anchors) {
                            if (a.href && a.href.includes('confirm-email')) {
                                return a.href;
                            }
                        }
                    }
                } catch(e) {}
            }
            return null;
        })()
    """)

    if not links:
        print('ERROR: Verification link not found in iframe')
        await ff.disconnect()
        return

    print(f'Tracking link: {links[:100]}...')

    # Decode the tracking URL to get the real verification URL
    decoded = unquote(links)
    # Extract the real URL from the tracking link
    import re
    match = re.search(r'https://app\.emergent\.sh/confirm-email\?token=[^&]+&return_url=[^/]+', decoded)
    if match:
        verify_url = match.group(0)
    else:
        # Fallback: construct from token
        token_match = re.search(r'token=([a-f0-9-]+)', decoded)
        if token_match:
            token = token_match.group(1)
            verify_url = f'https://app.emergent.sh/confirm-email?token={token}&return_url=https%3A%2F%2Fapp.emergent.sh%2Fauth%2Fcallback'
        else:
            print('ERROR: Could not extract verification URL')
            await ff.disconnect()
            return

    print(f'\nVerification URL: {verify_url}')

    # Open verification URL in a new tab
    tm = TabManager(browser_ws)
    await tm.connect()

    verify_tab_id = await tm.create_tab(verify_url)
    print(f'Verification tab: {verify_tab_id}')

    await asyncio.sleep(5)

    # Check verification result
    async with aiohttp.ClientSession() as session:
        async with session.get('http://127.0.0.1:9222/json') as resp:
            targets = await resp.json()
            verify_ws = None
            for t in targets:
                if 'emergent' in t.get('url', '') and 'confirm' not in t.get('url', ''):
                    verify_ws = t['webSocketDebuggerUrl']
                    break
                elif 'emergent' in t.get('url', ''):
                    verify_ws = t['webSocketDebuggerUrl']

    if verify_ws:
        ff2 = FormFiller(verify_ws)
        await ff2.connect()

        url = await ff2._evaluate('window.location.href')
        print(f'\nCurrent URL: {url}')

        content = await ff2._evaluate('document.body.innerText.substring(0, 1000)')
        print(f'\nPage content:\n{content}')

        await ff2.disconnect()

    await tm.disconnect()
    await ff.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
