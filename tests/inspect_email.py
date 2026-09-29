"""Inspect etempmail email row structure to find correct click target."""
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

    # Inspect clickable elements near the email
    info = await ff._evaluate("""
        (function() {
            const results = [];
            const all = document.querySelectorAll('button, a, [role=button], tr, td, div[class*="mail"], div[class*="row"], div[class*="item"]');
            for (const el of all) {
                const text = el.textContent.trim();
                if (text.includes('Confirm') || text.includes('Open') || text.includes('Emergent')) {
                    results.push({
                        tag: el.tagName,
                        text: text.substring(0, 80),
                        className: el.className,
                        id: el.id,
                        href: el.href || '',
                        childCount: el.children.length
                    });
                }
            }
            return results;
        })()
    """)
    print(f'Clickable elements near email: {len(info)}')
    for item in info:
        print(f'  {item["tag"]} | {item["text"][:60]} | class={item["className"][:40]} | children={item["childCount"]}')

    # Also check for Vue/React event handlers
    handlers = await ff._evaluate("""
        (function() {
            const rows = document.querySelectorAll('tr, [class*="mail"], [class*="row"], [class*="item"]');
            const results = [];
            for (const row of rows) {
                if (row.textContent.includes('Confirm Your Signup')) {
                    // Check for Vue/React event handlers
                    const vueKeys = Object.keys(row).filter(k => k.startsWith('__vue') || k.startsWith('__v'));
                    const reactKeys = Object.keys(row).filter(k => k.startsWith('__react'));
                    results.push({
                        tag: row.tagName,
                        className: row.className,
                        vueKeys: vueKeys,
                        reactKeys: reactKeys,
                        parentTag: row.parentElement ? row.parentElement.tagName : '',
                        parentClass: row.parentElement ? row.parentElement.className : ''
                    });
                }
            }
            return results;
        })()
    """)
    print(f'\nFramework handlers: {len(handlers)}')
    for h in handlers:
        print(f'  {h["tag"]} | class={h["className"][:40]} | vue={h["vueKeys"]} | react={h["reactKeys"]}')
        print(f'    parent: {h["parentTag"]} | class={h["parentClass"][:40]}')

    await ff.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
