"""Check etempmail inbox and extract verification link."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdp_orchestrator import FormFiller, InboxPoller


async def main():
    import aiohttp
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

    # Get email address
    email = await ff._evaluate("""
        (function() {
            const allText = document.body.innerText;
            const match = allText.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/);
            return match ? match[0] : 'not found';
        })()
    """)
    print(f'Email: {email}')

    # Click the verification email
    print('Clicking verification email...')
    result = await ff._evaluate("""
        (function() {
            const rows = document.querySelectorAll('tr, div, li');
            for (const row of rows) {
                if (row.textContent.includes('Confirm Your Signup')) {
                    row.click();
                    return 'clicked';
                }
            }
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.textContent.trim() === 'Confirm Your Signup') {
                    el.click();
                    return 'clicked subject';
                }
            }
            return 'not found';
        })()
    """)
    print(f'Click result: {result}')

    await asyncio.sleep(3)

    # Get email body
    body = await ff._evaluate('document.body.innerText.substring(0, 3000)')
    print(f'\nEmail body:\n{body}')

    # Extract verification link
    link = await ff._evaluate("""
        (function() {
            const links = document.querySelectorAll('a');
            for (const a of links) {
                if (a.href && (a.href.includes('verify') || a.href.includes('confirm') || a.href.includes('token'))) {
                    return a.href;
                }
            }
            const text = document.body.innerText;
            const match = text.match(/https?:\\/\\/[^\\s<>"{}|\\\\^`\\[\\]]+/);
            return match ? match[0] : null;
        })()
    """)
    print(f'\nVerification link: {link}')

    # Also try to get all links on the page
    all_links = await ff._evaluate("""
        (function() {
            const links = document.querySelectorAll('a');
            return Array.from(links).map(a => a.href).filter(h => h && h.startsWith('http'));
        })()
    """)
    print(f'\nAll links on page:')
    for l in (all_links or []):
        print(f'  {l}')

    await ff.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
