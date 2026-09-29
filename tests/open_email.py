"""Open verification email and extract link."""
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

    # Click the email row (DIV with hover:bg-gray-20 that contains "Confirm Your Signup")
    print('Clicking verification email row...')
    result = await ff._evaluate("""
        (function() {
            const rows = document.querySelectorAll('.flex.items-center.gap-3');
            for (const row of rows) {
                if (row.textContent.includes('Confirm Your Signup')) {
                    row.click();
                    return 'clicked: ' + row.textContent.trim().substring(0, 50);
                }
            }
            return 'not found';
        })()
    """)
    print(f'Click result: {result}')

    await asyncio.sleep(3)

    # Check if email opened - look for email body content
    body = await ff._evaluate('document.body.innerText.substring(0, 3000)')
    print(f'\nPage content after click:\n{body[:1500]}')

    # Look for verification link in the page
    link = await ff._evaluate("""
        (function() {
            // Check all links
            const links = document.querySelectorAll('a');
            for (const a of links) {
                if (a.href && (a.href.includes('verify') || a.href.includes('confirm') || a.href.includes('token'))) {
                    return {href: a.href, text: a.textContent.trim()};
                }
            }
            // Check for any URL in the page text
            const text = document.body.innerText;
            const matches = text.match(/https?:\\/\\/[^\\s<>"{}|\\\\^`\\[\\]]+/g);
            if (matches) {
                return matches.filter(u => u.includes('verify') || u.includes('confirm') || u.includes('token') || u.includes('emergent'));
            }
            return null;
        })()
    """)
    print(f'\nVerification link: {link}')

    # Also check for iframe (email content might be in iframe)
    iframes = await ff._evaluate("""
        (function() {
            const frames = document.querySelectorAll('iframe');
            return Array.from(frames).map(f => ({src: f.src, id: f.id, className: f.className}));
        })()
    """)
    print(f'\nIframes: {iframes}')

    await ff.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
