# CDP Browser Attach — Bypass Bot Detection for Hermes Agent

Attach Hermes browser automation to your **real Chrome browser** via Chrome DevTools Protocol (CDP). Eliminates CAPTCHA, Cloudflare, PerimeterX, and all bot detection — because all fingerprints are genuinely human.

## Why This Works

| Layer | Headless/Playwright | CDP Attach to Real Chrome |
|-------|---------------------|---------------------------|
| `navigator.webdriver` | `true` (bot giveaway) | `false` (real browser) |
| TLS/JA3 Fingerprint | Library-specific pattern | Genuine Chrome TLS handshake |
| WebGL/Font/Audio | Generic/empty | Real GPU, fonts, audio |
| IP Reputation | Datacenter (suspicious) | Your home/ISP IP |
| Cookies/History | Empty (bot indicator) | Real session data |

## Quick Start (3 Steps)

### 1. Kill Existing Chrome

```bash
# Windows
taskkill //F //IM chrome.exe

# macOS
killall "Google Chrome"

# Linux
pkill -f chrome
```

### 2. Launch Chrome with Remote Debugging

```bash
# Windows
"C:/Program Files/Google/Chrome/Application/chrome.exe" \
  --remote-debugging-port=9222 \
  --user-data-dir="C:/chrome-dev-profile" \
  --no-first-run --no-default-browser-check &

# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="/tmp/chrome-dev-profile" \
  --no-first-run --no-default-browser-check &

# Linux
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="/tmp/chrome-dev-profile" \
  --no-first-run --no-default-browser-check &
```

### 3. Configure Hermes

```bash
hermes config set browser.cdp_url "http://127.0.0.1:9222"
hermes config set browser.headed true
```

## Action Gating (Human-in-the-Loop)

```bash
hermes config set approvals.mode smart
hermes config set approvals.destructive_slash_confirm true
```

| Action Type | Behavior |
|-------------|----------|
| Navigate, scroll, read DOM | Auto-execute |
| Click navigation buttons | Auto-execute |
| Fill forms (non-destructive) | Auto-execute |
| Submit forms | **Require confirmation** |
| Login to accounts | **Require confirmation** |
| Delete/modify data | **Require confirmation** |
| Financial transactions | **Require confirmation** |

## Tested Sites (All Pass)

| Site | Protection | Result |
|------|-----------|--------|
| Google | reCAPTCHA v3 | ✅ |
| Logam Mulia | Cloudflare | ✅ |
| Pegadaian | Cloudflare | ✅ |
| Airbnb | PerimeterX | ✅ |
| TikTok | ByteDance anti-bot | ✅ |
| Nike | PerimeterX/Human | ✅ |
| ChatGPT | Cloudflare Turnstile | ✅ |

## Verification

```bash
curl -s http://127.0.0.1:9222/json/version
```

In Hermes, navigate to any site — `stealth_features` should show `["cdp_override"]`.

## Security Notes

- CDP endpoint (`127.0.0.1:9222`) is **local only** — no external exposure
- Use a separate `--user-data-dir` to isolate from your main profile
- Close Chrome with debugging port when not in use
- Do **not** expose port 9222 to network

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `cdp_url` not working | Ensure Chrome runs with `--remote-debugging-port=9222` |
| Chrome won't start | Kill all Chrome processes first, use separate `--user-data-dir` |
| Still blocked | Clear site cookies in dev profile, retry |
| CDP connection refused | Check firewall, ensure port 9222 is not blocked |

## Production Module (Python)

For advanced use cases, a production-grade Python module is included:

```
cdp_orchestrator/
├── __init__.py          # Package exports
├── browser_spawner.py   # Auto-detect, launch, verify CDP
├── healthcheck.py       # Background heartbeat monitor
└── reconnect.py         # Failover state machine with backoff
```

### Features

| Feature | Description |
|---------|-------------|
| **Auto-Spawn** | Detects Chrome path, launches with CDP flags, polls until ready |
| **Profile Lock Handling** | Detects and removes stale SingletonLock files |
| **Healthcheck** | Background heartbeat every 4s, emits disconnect events |
| **Auto-Reconnect** | Exponential backoff + jitter, max 5 attempts, graceful abort |
| **Zero Hanging** | All async tasks support graceful cancellation via asyncio.Event |

### Quick Start

```bash
pip install -r requirements.txt
python -m cdp_orchestrator.reconnect
```

### Library Usage

```python
import asyncio
from cdp_orchestrator import ReconnectManager

async def main():
    manager = ReconnectManager(
        max_attempts=5,
        initial_delay=1.0,
        max_delay=10.0,
        on_reconnected=lambda s: print(f"Reconnected: {s}"),
    )
    session = await manager.start()
    print(f"Browser: {session['Browser']}")
    # ... do work ...
    await manager.stop()

asyncio.run(main())
```

## License

MIT
