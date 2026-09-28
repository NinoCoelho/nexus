---
name: chrome-devtools
description: Control a dedicated Chrome instance via Chrome DevTools Protocol (CDP). Use for browser automation, page interaction, screenshots, form filling, and JS execution. Prefer over web_scrape when you need authenticated sessions, JS-heavy pages, or real browser interaction.
---

## When to use
- You need to interact with a website as a real browser (login, click, scroll, fill forms)
- The target requires cookies/auth — save a site login via the `site_credentials` tool and fill it (see "Logging in")
- JS-rendered content that http_call or web_scrape can't handle
- Taking screenshots of pages for the user
- Any task that needs a full browser session with state persistence

## Prerequisites
- Chrome installed at `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
- Profile directory: `~/.nexus/chrome-profile/` (persists cookies/logins across sessions)
- Default debugging port: **9223** (avoids conflict with user's main Chrome on 9222)

## Helper Library

A full CDP helper is at `vault://scripts/cdp_helper.py`. Import it in all scripts:

```python
sys.path.insert(0, os.path.expanduser('~/.nexus/vault/scripts'))
from cdp_helper import CDP, ensure_running, get_tabs, launch
```

The `CDP` class provides: `navigate(url)`, `js(expression)`, `click(selector)`, `fill(selector, value)`, `type_text(selector, text)`, `screenshot(path)`, `dom_text(selector)`, `html(selector)`, `wait_for(event)`, `send(method, params)`.

## Steps

### 1. Ensure Chrome is running

```python
sys.path.insert(0, os.path.expanduser('~/.nexus/vault/scripts'))
from cdp_helper import ensure_running
ensure_running()  # launches Chrome if not running
```

### 2. Navigate and interact

```python
python3 << 'PYEOF'
import sys, os, asyncio
sys.path.insert(0, os.path.expanduser('~/.nexus/vault/scripts'))
from cdp_helper import CDP, ensure_running

ensure_running()

async def main():
    async with CDP() as cdp:
        await cdp.navigate('https://example.com')
        print(await cdp.js('document.title'))
        print(await cdp.dom_text())
        await cdp.screenshot('/tmp/page.png')

asyncio.run(main())
PYEOF
```

### 3. Click / Fill forms

```python
async with CDP() as cdp:
    await cdp.navigate('https://example.com/login')
    await cdp.fill('#email', 'user@example.com')
    await cdp.fill('#password', 'secret')
    await cdp.click('button[type="submit"]')
```

For React/Angular sites that don't react to `.value =`, use `type_text()`:
```python
    await cdp.type_text('#email', 'user@example.com')
```

### 4. Take a screenshot

```python
    await cdp.screenshot('/tmp/page.png')
```

### 5. Create a new tab

```python
from cdp_helper import CDP, get_browser_ws
browser_ws = get_browser_ws()
async with CDP(browser_ws) as cdp:
    r = await cdp.send('Target.createTarget', {'url': 'https://example.com'})
    print(r)
```

### 6. List and switch tabs

```python
from cdp_helper import get_tabs
for i, t in enumerate(get_tabs()):
    if t['type'] == 'page':
        print(f"  [{i}] {t['title']} — {t['url'][:80]}")
```

To switch focus:
```python
async with CDP(tab_ws_url) as cdp:
    await cdp.send('Page.bringToFront')
```

## Logging in

Do NOT ask the user to type a password into the chat and do NOT hardcode credentials in scripts. Use the `site_credentials` tool instead:

1. `site_credentials(action="list")` — check whether a login is already saved for the site.
2. If not: `site_credentials(action="save", site="example.com", reason="…")` — opens a masked form for the user; the answer is stored Fernet-encrypted and never shown to you.
3. `site_credentials(action="fill", site="example.com", surface="cdp")` — the server fills the username/password into the debug Chrome's current login page and optionally submits. Pass `selectors={"user": "...", "pass": "..."}` when the form is not auto-detected, and `tab="…"` to target another open tab.

The password never appears in tool arguments, results, or the transcript. Manual login in the visible Chrome window remains a fallback when a site needs 2FA or a captcha.

## Gotchas
- **Profile persistence**: The Chrome profile at `~/.nexus/chrome-profile/` persists cookies, logins, and localStorage across sessions. This is intentional — once you log in, you stay logged in.
- **Port 9223**: Fixed to avoid collision with the user's main Chrome on 9222. If 9223 is also taken, set `CDP_PORT` env var.
- **websockets library**: This skill has an isolated Python environment managed by Nexus. After calling `skill_view(name="chrome-devtools")`, use the `python.path` from the response — it includes `websockets>=15`.
- **Headless mode**: Not used — the user needs to see the browser to log in. If headless is needed, add `--headless=new` to `launch()`.
- **Only one CDP connection per target**: If you get "target closed" errors, the previous websocket session didn't close cleanly. Retry.
- **Large pages**: The DOM snapshot can produce huge output. Always limit with `max_length` param.
- **Extensions**: Extensions from the user's main Chrome profile won't load. Install them manually in this Chrome instance.
- **Event ordering**: CDP sends events AND responses on the same WebSocket. The helper's `send()` method filters by `id` to avoid confusion. Don't bypass it.
