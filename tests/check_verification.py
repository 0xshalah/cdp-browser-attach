"""Check verification result on emergent.sh home page."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdp_orchestrator import FormFiller


async def main():
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get('http://127.0.0.1:9222/json') as resp:
            targets = await resp.json()
            home_ws = None
            for t in targets:
                if t.get('url', '') == 'https://app.emergent.sh/':
                    home_ws = t['webSocketDebuggerUrl']
                    break

    if not home_ws:
        print('No home page tab found')
        return

    ff = FormFiller(home_ws)
    await ff.connect()

    url = await ff._evaluate('window.location.href')
    print(f'URL: {url}')

    content = await ff._evaluate('document.body.innerText.substring(0, 2000)')
    print(f'\nPage content:\n{content}')

    # Check for success/error indicators
    status = await ff._evaluate("""
        (function() {
            const text = document.body.innerText.toLowerCase();
            if (text.includes('verified') || text.includes('success') || text.includes('welcome')) {
                return 'SUCCESS';
            }
            if (text.includes('error') || text.includes('invalid') || text.includes('expired')) {
                return 'ERROR';
            }
            return 'UNKNOWN';
        })()
    """)
    print(f'\nStatus: {status}')

    await ff.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
