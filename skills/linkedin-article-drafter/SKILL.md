---
name: linkedin-article-drafter
description: Use this whenever you need to draft a LinkedIn article with title, body text, and cover image. Opens the LinkedIn article editor via CDP, fills all fields, and saves as draft — never auto-publishes.
---

## When to use
- User asks to "write a LinkedIn article", "draft an article on LinkedIn", "post an article", or wants to turn vault content into a LinkedIn piece.
- After running editorial/coordinator skills when the output destination is LinkedIn.
- Prefer over manual copy-paste for any LinkedIn article creation.

## Prerequisites
- Chrome must be running with remote debugging enabled (use `cdp_helper.py` — `ensure_running()`).
- User must be logged into LinkedIn in that Chrome instance.
- For cover images: either a vault image path or generate one via `generate_image` / Pillow.

## Steps

### 1. Prepare content
- Write or receive the article title and body text.
- Body should be plain text or simple markdown (LinkedIn's ProseMirror editor handles basic paragraphs).
- If no cover image provided, create one:
  - Try `generate_image` (OpenAI or Gemini).
  - Fallback: create a simple branded image with Pillow.

### 2. Open LinkedIn article editor
```
await cdp.navigate('https://www.linkedin.com/article/new/')
await asyncio.sleep(4)
```

### 3. Set the title
Use the textarea directly:
```python
await cdp.js("""
(() => {
    const ta = document.getElementById('article-editor-headline__textarea');
    ta.focus();
    ta.value = 'YOUR TITLE HERE';
    ta.dispatchEvent(new Event('input', {bubbles: true}));
    ta.dispatchEvent(new Event('change', {bubbles: true}));
})()
""")
```

### 4. Set the body text
Focus the ProseMirror editor and use CDP `Input.insertText`:
```python
await cdp.js("document.querySelector('.ProseMirror')?.focus()")
await asyncio.sleep(0.3)
await cdp.send('Input.insertText', {'text': 'YOUR BODY TEXT HERE'})
```
**Important:** Do NOT use `innerHTML` or `appendChild` — ProseMirror has its own internal state that won't sync. `Input.insertText` simulates real typing.

### 5. Upload cover image
```python
# 5a. Click "Upload from computer" to reveal the hidden file input
await cdp.js("""
(() => {
    const all = document.querySelectorAll('button, [role="button"]');
    for (const el of all) {
        if ((el.textContent||'').includes('Upload from computer')) {
            el.click(); return;
        }
    }
})()
""")
await asyncio.sleep(2)

# 5b. Set the file via CDP DOM.setFileInputFiles
resp = await cdp.send('DOM.getDocument', {'depth': 0})
root_id = resp['result']['root']['nodeId']
resp2 = await cdp.send('DOM.querySelector', {
    'nodeId': root_id,
    'selector': '#media-editor-file-selector__file-input'
})
node_id = resp2['result']['nodeId']
await cdp.send('DOM.setFileInputFiles', {
    'files': ['/absolute/path/to/image.png'],
    'nodeId': node_id
})
await asyncio.sleep(4)

# 5c. Click "Next" in the cover image dialog to apply
await cdp.js("""
(() => {
    const btns = document.querySelectorAll('button');
    for (const btn of btns) {
        if ((btn.textContent||'').trim() === 'Next') { btn.click(); return; }
    }
})()
""")
```

Accepted image formats: `image/jpeg, image/jpg, image/png, image/webp`. Recommended size: 1536×1024 or 1200×627.

### 6. Verify draft auto-save
LinkedIn auto-saves drafts. Confirm via:
```python
text = await cdp.dom_text(max_length=500)
assert 'Draft - saved' in text or 'Draft' in text
```

### 7. Stop here — never auto-publish
The article is now a draft. Tell the user to review and publish manually. Optionally take a screenshot so they can preview:
```python
resp = await cdp.send('Page.captureScreenshot', {'format': 'png'})
import base64
data = base64.b64decode(resp['result']['data'])
# save to vault for preview
```

## Gotchas
- **ProseMirror body:** Must use `Input.insertText` via CDP. Setting `.textContent` or `.innerHTML` won't trigger ProseMirror's state sync and content will be lost on save.
- **File input hidden:** The `<input type="file">` is hidden until "Upload from computer" is clicked. Must click first, then use `DOM.setFileInputFiles`.
- **CDP response wrapping:** `cdp.send()` returns `{'id': N, 'result': {...}}` — access data via `resp['result']`, not `resp` directly.
- **Gmail-style undo toasts:** Not applicable here, but if you see "Undo" prompts, ignore them — LinkedIn auto-saves drafts harmlessly.
- **Session auth:** Requires active LinkedIn session in the Chrome instance. If expired, the user needs to re-login manually.
- **Long body text:** `Input.insertText` handles multi-line text fine, but for very long articles consider splitting into multiple insert calls with paragraph breaks.
- **Never publish without user confirmation.** Always save as draft and let the user review first.
