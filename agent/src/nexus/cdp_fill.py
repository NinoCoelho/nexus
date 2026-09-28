"""Server-side CDP login fill for the chrome-devtools debug Chrome.

Companion to the ``site_credentials`` tool's ``surface="cdp"`` path: the
agent passes only the *site name*; this module resolves the stored
credential, connects to the debug Chrome's DevTools endpoint (default
port 9223, see the ``chrome-devtools`` skill), and fills the login form
via ``Runtime.evaluate``. The password never appears in LLM context,
tool arguments, or transcripts.

The fill JS mirrors what the Chrome side panel executes for the same
feature (``chrome/service-worker.js`` → ``pageFillLogin``): native value
setter + input/change events so React/Angular-controlled inputs notice,
username auto-detection (last visible text-like input before the
password field), optional form submit.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

DEFAULT_CDP_PORT = 9223

_FILL_JS = r"""
((argsJson) => {
  const a = JSON.parse(argsJson);
  const vis = (el) => {
    if (!el || el.disabled || el.readOnly) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none";
  };
  const pass = a.passSelector
    ? document.querySelector(a.passSelector)
    : [...document.querySelectorAll("input[type=password]")].filter(vis).pop();
  if (!pass) {
    return { ok: false, error: "no visible password input found on this page" };
  }
  const setVal = (el, val) => {
    const desc = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value");
    if (desc && desc.set) desc.set.call(el, val);
    else el.value = val;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  let user = a.userSelector ? document.querySelector(a.userSelector) : null;
  if (!user && a.username) {
    const inputs = [...document.querySelectorAll("input")].filter(vis);
    const idx = inputs.indexOf(pass);
    for (let i = idx - 1; i >= 0; i--) {
      const t = (inputs[i].type || "text").toLowerCase();
      if (t === "text" || t === "email" || t === "tel" || t === "") {
        user = inputs[i];
        break;
      }
    }
  }
  const filled = [];
  if (user && a.username) {
    setVal(user, a.username);
    filled.push("username");
  }
  setVal(pass, a.password);
  filled.push("password");
  let submitted = false;
  if (a.submit) {
    const form = pass.closest("form");
    if (form) {
      const btn = form.querySelector(
        'button[type="submit"], input[type="submit"], button:not([type])'
      );
      if (btn) {
        btn.click();
        submitted = true;
      } else if (form.requestSubmit) {
        form.requestSubmit();
        submitted = true;
      }
    }
    if (!submitted) {
      for (const type of ["keydown", "keyup"]) {
        pass.dispatchEvent(
          new KeyboardEvent(type, {
            key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true,
          })
        );
      }
      submitted = true;
    }
  }
  return { ok: true, site: a.site, filled, submitted, url: location.href };
})
"""


async def _pick_target(port: int, tab: str | None) -> dict[str, Any]:
    import httpx

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"http://127.0.0.1:{port}/json", timeout=5.0)
            r.raise_for_status()
            targets = r.json()
    except Exception as exc:
        raise RuntimeError(
            f"cannot reach the debug Chrome CDP endpoint on port {port} "
            f"({exc}). Launch it first (see the chrome-devtools skill: "
            "ensure_running), or use surface='page' to fill in the user's "
            "real tab instead."
        ) from exc

    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        raise RuntimeError(f"the debug Chrome on port {port} has no page targets")
    if tab:
        needle = tab.lower()
        match = next(
            (
                t
                for t in pages
                if needle in (t.get("url") or "").lower()
                or needle in (t.get("title") or "").lower()
            ),
            None,
        )
        if match is None:
            known = "; ".join((t.get("title") or t.get("url") or "?")[:60] for t in pages)
            raise RuntimeError(f"no CDP tab matches {tab!r} (open tabs: {known})")
        return match
    return pages[0]


async def _evaluate(ws: Any, expression: str, *, timeout: float) -> dict[str, Any]:
    msg_id = 1
    await ws.send(
        json.dumps(
            {
                "id": msg_id,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True},
            }
        )
    )
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        data = json.loads(raw)
        if data.get("id") != msg_id:
            continue  # CDP interleaves events with responses; filter by id
        if "error" in data:
            return {"ok": False, "error": str(data["error"].get("message") or data["error"])}
        result = (data.get("result") or {}).get("result") or {}
        exc = (data.get("result") or {}).get("exceptionDetails")
        if exc:
            return {"ok": False, "error": str(exc.get("exception") or exc.get("text") or "JS error")}
        value = result.get("value")
        if isinstance(value, dict):
            return value
        return {"ok": False, "error": "fill returned an unexpected result shape"}


async def cdp_fill_login(
    *,
    site: str,
    username: str,
    password: str,
    user_selector: str | None = None,
    pass_selector: str | None = None,
    submit: bool = True,
    port: int = DEFAULT_CDP_PORT,
    tab: str | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Fill a stored site login into the debug Chrome's current page.

    Returns the fill JS result dict (``{ok, site, filled, submitted, url}``
    on success) — never includes the password.
    """
    import websockets

    target = await _pick_target(port, tab)
    payload = json.dumps(
        {
            "site": site,
            "username": username,
            "password": password,
            "userSelector": user_selector,
            "passSelector": pass_selector,
            "submit": bool(submit),
        }
    )
    async with websockets.connect(
        target["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024
    ) as ws:
        return await _evaluate(ws, f"({_FILL_JS})({json.dumps(payload)})", timeout=timeout)
