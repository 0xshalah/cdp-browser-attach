"""Extract verification link from email iframe content."""
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

    # Check iframe content (email body is likely in srcdoc iframe)
    iframe_content = await ff._evaluate("""
        (function() {
            const iframes = document.querySelectorAll('iframe');
            const results = [];
            for (const iframe of iframes) {
                try {
                    const doc = iframe.contentDocument || iframe.contentWindow.document;
                    if (doc) {
                        const text = doc.body ? doc.body.innerText : '';
                        const links = doc.querySelectorAll('a');
                        const linkHrefs = Array.from(links).map(a => a.href).filter(h => h && h.startsWith('http'));
                        results.push({
                            src: iframe.src,
                            className: iframe.className,
                            textLength: text.length,
                            textPreview: text.substring(0, 500),
                            links: linkHrefs
                        });
                    }
                } catch(e) {
                    results.push({src: iframe.src, error: e.message});
                }
            }
            return results;
        })()
    """)
    print(f'Iframe content: {len(iframe_content)} iframes')
    for item in iframe_content:
        print(f'\n  iframe: {item.get("className", "")[:40]}')
        if 'error' in item:
            print(f'    Error: {item["error"]}')
        else:
            print(f'    Text length: {item["textLength"]}')
            print(f'    Text preview: {item["textPreview"][:200]}')
            if item['links']:
                print(f'    Links: {item["links"]}')

    # Also check for shadow DOMs
    shadow_content = await ff._evaluate("""
        (function() {
            const results = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.shadowRoot) {
                    const text = el.shadowRoot.textContent || '';
                    if (text.includes('verify') || text.includes('confirm') || text.includes('token')) {
                        results.push({
                            tag: el.tagName,
                            className: el.className,
                            text: text.substring(0, 500)
                        });
                    }
                }
            }
            return results;
        })()
    """)
    print(f'\nShadow DOMs with verification content: {len(shadow_content)}')
    for item in shadow_content:
        print(f'  {item["tag"]} | {item["className"]} | {item["text"][:200]}')

    # Try to find any element containing verification text
    verify_elements = await ff._evaluate("""
        (function() {
            const results = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const text = el.textContent || '';
                if ((text.includes('verify') || text.includes('Verify') || text.includes('confirm') || text.includes('Confirm')) 
                    && el.children.length === 0) {
                    results.push({
                        tag: el.tagName,
                        text: text.trim().substring(0, 100),
                        href: el.href || '',
                        className: el.className
                    });
                }
            }
            return results;
        })()
    """)
    print(f'\nElements with verify/confirm text: {len(verify_elements)}')
    for item in verify_elements:
        print(f'  {item["tag"]} | {item["text"][:60]} | href={item["href"]}')

    await ff.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
