"""Debug script: find correct signup form and submit with requestSubmit()."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdp_orchestrator import FormSubmitter, FormFiller, TurnstileHandler


async def main():
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get('http://127.0.0.1:9222/json') as resp:
            targets = await resp.json()
            signup_ws = None
            for t in targets:
                if 'emergent' in t.get('url', ''):
                    signup_ws = t['webSocketDebuggerUrl']
                    break

    if not signup_ws:
        print('No emergent.sh tab found')
        return

    fs = FormSubmitter(signup_ws)
    await fs.connect()
    ff = FormFiller(signup_ws)
    await ff.connect()

    # Step 1: Re-open signup modal
    print('=== RE-OPENING SIGNUP MODAL ===')
    await ff._evaluate("""
        (function() {
            const all = document.querySelectorAll('button, a, div[role=button]');
            for (const el of all) {
                if (el.textContent.trim() === 'Sign up') {
                    el.click();
                    return 'clicked';
                }
            }
            return 'not found';
        })()
    """)
    await asyncio.sleep(3)

    await ff._evaluate("""
        (function() {
            const all = document.querySelectorAll('button, a, div[role=button]');
            for (const el of all) {
                if (el.textContent.includes('Continue with Email')) {
                    el.click();
                    return 'clicked';
                }
            }
            return 'not found';
        })()
    """)
    await asyncio.sleep(2)

    # Step 2: Find ALL forms
    print('=== ALL FORMS ===')
    forms_info = await ff._evaluate("""
        (function() {
            const forms = document.querySelectorAll('form');
            return Array.from(forms).map((form, i) => ({
                index: i,
                action: form.action,
                method: form.method,
                className: form.className,
                fieldCount: form.elements.length,
                fields: Array.from(form.elements).map(e => ({
                    type: e.type,
                    name: e.name,
                    value: e.value ? e.value.substring(0, 30) : '',
                    required: e.required
                }))
            }));
        })()
    """)
    print(f'Forms found: {len(forms_info)}')
    for form in forms_info:
        print(f'  Form {form["index"]}: {form["method"]} {form["action"]}')
        print(f'    Fields: {form["fieldCount"]}')
        for field in form['fields']:
            print(f'      {field["type"]}:{field["name"]}:{field["value"]}')

    # Step 3: Find signup form (has email + password)
    print('\n=== FINDING SIGNUP FORM ===')
    signup_form = await ff._evaluate("""
        (function() {
            const forms = document.querySelectorAll('form');
            for (const form of forms) {
                const inputs = form.querySelectorAll('input');
                let hasEmail = false, hasPassword = false;
                for (const input of inputs) {
                    if (input.type === 'email' || input.name === 'email') hasEmail = true;
                    if (input.type === 'password' || input.name === 'password') hasPassword = true;
                }
                if (hasEmail && hasPassword) {
                    return {
                        action: form.action,
                        method: form.method,
                        fieldCount: form.elements.length,
                        fields: Array.from(form.elements).map(e => ({
                            type: e.type,
                            name: e.name,
                            value: e.value ? e.value.substring(0, 30) : '',
                            required: e.required
                        }))
                    };
                }
            }
            return null;
        })()
    """)
    print(f'Signup form: {signup_form}')

    if not signup_form:
        print('ERROR: Signup form not found')
        await ff.disconnect()
        await fs.disconnect()
        return

    # Step 4: Fill the correct form
    print('\n=== FILLING SIGNUP FORM ===')
    fill_result = await ff._evaluate("""
        (function() {
            const forms = document.querySelectorAll('form');
            for (const form of forms) {
                const inputs = form.querySelectorAll('input');
                let hasEmail = false, hasPassword = false;
                for (const input of inputs) {
                    if (input.type === 'email' || input.name === 'email') hasEmail = true;
                    if (input.type === 'password' || input.name === 'password') hasPassword = true;
                }
                if (hasEmail && hasPassword) {
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    const nameInput = form.querySelector("input[name='name']");
                    const emailInput = form.querySelector("input[name='email']");
                    const passInput = form.querySelector("input[name='password']");
                    if (nameInput) { nativeSetter.call(nameInput, 'Hermes Agent'); nameInput.dispatchEvent(new Event('input', {bubbles:true})); nameInput.dispatchEvent(new Event('change', {bubbles:true})); }
                    if (emailInput) { nativeSetter.call(emailInput, 'hermesagent2026@dunkos.xyz'); emailInput.dispatchEvent(new Event('input', {bubbles:true})); emailInput.dispatchEvent(new Event('change', {bubbles:true})); }
                    if (passInput) { nativeSetter.call(passInput, 'HermesAgent2026!'); passInput.dispatchEvent(new Event('input', {bubbles:true})); passInput.dispatchEvent(new Event('change', {bubbles:true})); }
                    return 'filled';
                }
            }
            return 'not found';
        })()
    """)
    print(f'Fill result: {fill_result}')

    await asyncio.sleep(1)

    # Step 5: Wait for Turnstile
    print('\n=== WAITING FOR TURNSTILE ===')
    th = TurnstileHandler(signup_ws)
    await th.connect()
    token = await th.wait_for_token(timeout=30)
    print(f'Turnstile: {"solved" if token else "NOT solved"}')
    await th.disconnect()

    # Step 6: Submit with form.requestSubmit()
    print('\n=== SUBMITTING WITH requestSubmit() ===')
    fs._requests.clear()

    submit_result = await ff._evaluate("""
        (function() {
            const forms = document.querySelectorAll('form');
            for (const form of forms) {
                const inputs = form.querySelectorAll('input');
                let hasEmail = false, hasPassword = false;
                for (const input of inputs) {
                    if (input.type === 'email' || input.name === 'email') hasEmail = true;
                    if (input.type === 'password' || input.name === 'password') hasPassword = true;
                }
                if (hasEmail && hasPassword) {
                    form.requestSubmit();
                    return 'submitted';
                }
            }
            return 'not found';
        })()
    """)
    print(f'Submit: {submit_result}')

    await asyncio.sleep(8)

    # Check captures
    captures = await fs.get_captures()
    print(f'\n=== NETWORK CAPTURES ({len(captures)}) ===')
    for cap in captures:
        print(f'  {cap.method} {cap.url[:80]} -> {cap.status}')
        if cap.post_data:
            print(f'    POST: {cap.post_data[:200]}')
        if cap.response_body:
            print(f'    Response: {cap.response_body[:300]}')

    # Check errors
    errors = await fs.detect_errors()
    print(f'\n=== ERRORS ({len(errors)}) ===')
    for err in errors:
        print(f'  - [{err.type}] {err.text}')

    # Check URL
    url = await ff._evaluate('window.location.href')
    print(f'\nURL: {url}')

    await ff.disconnect()
    await fs.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
