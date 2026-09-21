"""Side-panel extension install/verify endpoints.

``GET /ext/ping`` — heartbeat. The extension's service worker pings it on
boot/startup with an ``X-Nexus-Extension`` header (version); the route
records last-seen state in memory. Reads (no header) never overwrite —
the CLI doctor and the ``/ext`` page poll it to show "extension connected".

``GET /ext`` — guided install page (self-contained HTML, no UI build):
numbered load-unpacked steps with copy buttons, plus a live connection
check. Carries the ``nexus-server`` meta marker so the extension's
``port-capture.js`` content script learns the server port from the page
itself the moment the user opens it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import HTMLResponse

router = APIRouter()

_LAST_PING: dict[str, Any] = {"at": 0.0, "version": ""}


def extension_dir() -> Path:
    from ... import chrome_install

    return chrome_install.DEST_DIR


@router.get("/ext/ping")
async def ext_ping(x_nexus_extension: str = Header(default="")) -> dict[str, Any]:
    now = time.time()
    if x_nexus_extension:
        _LAST_PING["at"] = now
        _LAST_PING["version"] = x_nexus_extension
    seen = bool(_LAST_PING["at"])
    return {
        "ok": True,
        "extension_seen": seen,
        "age_seconds": round(now - _LAST_PING["at"], 1) if seen else None,
        "version": _LAST_PING["version"],
    }


def _files_state() -> dict[str, Any]:
    manifest = extension_dir() / "manifest.json"
    if not manifest.exists():
        return {"installed": False, "path": str(extension_dir()), "version": None}
    try:
        version = json.loads(manifest.read_text()).get("version", "?")
    except json.JSONDecodeError:
        version = "?"
    return {"installed": True, "path": str(extension_dir()), "version": version}


@router.get("/ext/files")
async def ext_files() -> dict[str, Any]:
    return {"ok": True, **_files_state()}


@router.post("/ext/prepare")
async def ext_prepare() -> dict[str, Any]:
    from ... import chrome_install

    if chrome_install._source_dir() is None:
        return {"ok": False, "error": "extension sources not found on this machine"}
    rc = chrome_install.install(open_browser=False)
    if rc != 0:
        return {"ok": False, "error": "install failed"}
    return {"ok": True, **_files_state()}


@router.get("/ext", response_class=HTMLResponse)
async def ext_install_page() -> HTMLResponse:
    ext_path = extension_dir()
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="nexus-server" content="1">
<title>Nexus in Chrome — install</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 640px;
         margin: 48px auto; padding: 0 20px; color: #e6e8ec; background: #16181d; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 16px; margin-top: 32px; }}
  ol li {{ margin: 12px 0; line-height: 1.5; }}
  code {{ background: #1d2026; border: 1px solid #2c303a; border-radius: 4px;
          padding: 2px 8px; font-family: ui-monospace, Menlo, monospace; font-size: 13px;
          word-break: break-all; }}
  button {{ background: #1d2026; color: #9aa3b2; border: 1px solid #2c303a; border-radius: 6px;
            padding: 4px 12px; cursor: pointer; margin-left: 8px; font-size: 13px; }}
  button:hover {{ border-color: #5b8cff; color: #e6e8ec; }}
  #status {{ margin-top: 24px; padding: 10px 14px; border-radius: 8px; font-size: 14px;
             border: 1px solid #2c303a; }}
  .ok {{ border-color: #2e7d4f !important; color: #7ad0a0 !important; }}
  .wait {{ color: #9aa3b2; }}
</style>
</head>
<body>
<h1>Nexus in Chrome</h1>
<h2>Install (one time)</h2>
<div id="prepare" class="wait" style="display:none; padding:10px 14px; border:1px solid #2c303a; border-radius:8px; margin-bottom:8px;">
  <button id="prepareBtn">Prepare extension files</button>
  <span id="prepareNote"> — copies the bundled extension to <code>~/.nexus/chrome-extension</code></span>
</div>
<ol>
  <li>Open <code>chrome://extensions</code> <button data-copy="chrome://extensions">copy</button></li>
  <li>Turn on <strong>Developer mode</strong> (top-right toggle)</li>
  <li>Click <strong>Load unpacked</strong> and select this folder:
    <br><code id="extpath">{ext_path}</code> <button data-copy-path="1">copy path</button></li>
  <li>Pin the Nexus icon (puzzle piece → pin) and click it on any tab</li>
</ol>
<h2>Connection</h2>
<div id="status" class="wait">waiting for the extension…</div>
<p class="wait">The extension detects the server port automatically from this page.</p>
<script>
  document.querySelectorAll("button[data-copy]").forEach((b) => {{
    b.onclick = () => navigator.clipboard.writeText(b.dataset.copy);
  }});
  const cp = document.querySelector("button[data-copy-path]");
  if (cp) cp.onclick = () => navigator.clipboard.writeText(document.getElementById("extpath").textContent.trim());
  const status = document.getElementById("status");
  const prepare = document.getElementById("prepare");
  const prepareBtn = document.getElementById("prepareBtn");
  const prepareNote = document.getElementById("prepareNote");
  const refreshFiles = async () => {{
    try {{
      const r = await fetch("/ext/files");
      const d = await r.json();
      if (!d.installed) {{
        prepare.style.display = "block";
      }} else {{
        prepare.style.display = "none";
      }}
    }} catch (_) {{}}
  }};
  prepareBtn.onclick = async () => {{
    prepareBtn.disabled = true;
    prepareNote.textContent = " preparing…";
    try {{
      const r = await fetch("/ext/prepare", {{ method: "POST" }});
      const d = await r.json();
      prepareNote.textContent = d.ok
        ? ` done — v${{d.version}} copied`
        : ` failed: ${{d.error}}`;
      if (d.ok) prepare.style.display = "none";
    }} catch (e) {{
      prepareNote.textContent = ` failed: ${{e}}`;
    }}
    prepareBtn.disabled = false;
    setTimeout(() => {{ prepareNote.textContent = " — copies the bundled extension to ~/.nexus/chrome-extension"; }}, 4000);
  }};
  const tick = async () => {{
    try {{
      const r = await fetch("/ext/ping");
      const d = await r.json();
      if (d.extension_seen) {{
        status.className = "ok";
        status.textContent = `Extension connected ✓ (v${{d.version}}, pinged ${{d.age_seconds}}s ago)`;
      }} else {{
        status.className = "wait";
        status.textContent = "waiting for the extension…";
      }}
    }} catch (_) {{
      status.className = "wait";
      status.textContent = "server error";
    }}
  }};
  refreshFiles();
  tick();
  setInterval(tick, 2000);
</script>
</body>
</html>"""
    return HTMLResponse(html)
