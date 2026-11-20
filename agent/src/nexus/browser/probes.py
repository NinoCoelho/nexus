"""Shared text-first perception probes (NBIE).

Single canonical copy of the JS probe library. The same source runs on both
surfaces:

* CDP (``browser`` tool, managed Chrome): ``Runtime.evaluate`` with
  ``returnByValue``.
* Chrome extension (``page`` tool, user's real tab): bundled as
  ``chrome/probes.js`` and loaded by the service worker via
  ``importScripts``; invoked through ``chrome.scripting.executeScript`` with
  a function reference.

The two copies must stay byte-identical -- :func:`test_probes_sync` guards
the drift. Probes are pure DOM, stateless, and run in the page context (they
see ``document``/``location``/``window``). Secrets are redacted *inside* the
probes: password values never leave the page on either surface.

The dispatcher ``__nexus_probes(name, args)`` is self-contained (it closes
over nothing but page globals) so it survives ``chrome.scripting.executeScript``
function serialization, which drops closures.
"""

from __future__ import annotations

import json as _json

# Canonical JS probe library. Kept byte-identical to chrome/probes.js.
_PROBES_JS = r"""self.__nexus_probes = function __nexus_probes(name, args) {
  "use strict";

  // ---------- small DOM helpers (run in page context) ----------

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const NOISE =
    "nav, header, footer, aside, script, style, noscript, svg, iframe, " +
    "[aria-hidden='true'], [hidden], [role='navigation'], [role='banner'], " +
    "[role='contentinfo'], [role='complementary'], [role='search']";

  const TEXT_NODES = (root) => {
    const out = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const p = node.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        const tag = p.tagName;
        if (/^(SCRIPT|STYLE|NOSCRIPT|SVG|IFRAME)$/i.test(tag)) {
          return NodeFilter.FILTER_REJECT;
        }
        try {
          if (p.closest(NOISE)) return NodeFilter.FILTER_REJECT;
        } catch (_) {
          return NodeFilter.FILTER_REJECT;
        }
        if (!node.nodeValue || !node.nodeValue.trim()) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let node;
    while ((node = walker.nextNode())) out.push(node);
    return out;
  };

  const trim = (s) => (s == null ? "" : String(s).trim());
  const cap = (s, n) => {
    s = s == null ? "" : String(s);
    return s.length > n ? s.slice(0, n) + "\u2026" : s;
  };

  // FNV-1a 32-bit hash of the visible text -- cheap page fingerprint.
  const textHash = (text) => {
    let h = 2166136261 >>> 0;
    const s = text == null ? "" : String(text);
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return String(h >>> 0);
  };

  const boxOf = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 && r.height <= 0) return null;
    return {
      x: Math.round(r.x),
      y: Math.round(r.y),
      w: Math.round(r.width),
      h: Math.round(r.height),
    };
  };

  const visible = (el) => {
    if (!el || el.disabled || el.readOnly) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  };

  const roleOf = (el) => {
    if (!el) return "";
    const aria = el.getAttribute("role");
    if (aria) return aria;
    const tag = el.tagName;
    const type = el.getAttribute("type");
    if (tag === "BUTTON") return "button";
    if (tag === "A") return "link";
    if (tag === "INPUT") {
      if (type === "submit" || type === "button" || type === "reset") return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (
        type === "text" ||
        type === "email" ||
        type === "tel" ||
        type === "password" ||
        type === "search" ||
        type === "url" ||
        !type
      )
        return "textbox";
      if (type === "number" || type === "range") return "spinbutton";
      return "textbox";
    }
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "SELECT") return "combobox";
    if (tag === "SUMMARY") return "button";
    if (tag === "IMG") return "img";
    return "";
  };

  const nameOf = (el) => {
    if (!el) return "";
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel) return ariaLabel;
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const target = document.getElementById(labelledby);
      if (target) return trim(target.innerText || target.textContent || "");
    }
    const placeholder = el.getAttribute("placeholder");
    if (placeholder) return placeholder;
    const title = el.getAttribute("title");
    if (title) return title;
    const tag = el.tagName;
    const type = el.getAttribute("type");
    if (tag === "INPUT") {
      if (type === "submit" || type === "button" || type === "reset")
        return trim(el.value || "");
      if (type === "image") return trim(el.alt || "");
      return ""; // text inputs: label association not computed here
    }
    if (tag === "BUTTON")
      return trim(el.innerText || el.textContent || el.value || "");
    if (tag === "A")
      return trim(el.innerText || el.textContent || el.alt || "");
    if (tag === "IMG") return trim(el.alt || "");
    return trim(el.innerText || el.textContent || "");
  };

  const escapeCss = (ident) => {
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(ident);
    return String(ident).replace(/[\"'':;, .#\[\]()>+~=!@$%^&*{|`/?-]/g, "\\$&");
  };

  const nthPath = (el) => {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      let tag = node.tagName.toLowerCase();
      let nth = 1;
      let sib = node.previousElementSibling;
      while (sib) {
        if (sib.tagName.toLowerCase() === tag) nth++;
        sib = sib.previousElementSibling;
      }
      if (nth > 1) tag += ":nth-of-type(" + nth + ")";
      parts.unshift(tag);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };

  const bestSelector = (el) => {
    if (!el || el.nodeType !== 1) return "";
    const id = el.getAttribute("id");
    if (id && /^[A-Za-z][\w-]*$/.test(id)) return "#" + escapeCss(id);
    const testid = el.getAttribute("data-testid");
    if (testid) return '[data-testid="' + escapeCss(testid) + '"]';
    const aria = el.getAttribute("aria-label");
    if (aria) return '[aria-label="' + escapeCss(aria) + '"]';
    return nthPath(el);
  };

  // ---------- readPage ----------
  const readPage = ({
    maxChars = 8000,
    maxLinks = 80,
    maxFields = 40,
  } = {}) => {
    let root =
      document.querySelector("article") ||
      document.querySelector("main") ||
      document.querySelector("[role='main']") ||
      document.querySelector("#content") ||
      document.body;
    if (!root || !root.innerText || !root.innerText.trim()) root = document.body;
    const empty = {
      ok: true,
      url: location.href,
      title: document.title,
      text: "",
      links: [],
      fields: [],
      contentHash: "",
      complete: true,
      readyState: document.readyState,
    };
    if (!root) return empty;

    const texts = [];
    let total = 0;
    for (const node of TEXT_NODES(root)) {
      const val = node.nodeValue.trim();
      if (!val) continue;
      texts.push(val);
      total += val.length + 1;
      if (total > maxChars + 200) break;
    }
    let text = texts
      .join(" ")
      .replace(/[ \t]+/g, " ")
      .replace(/ ?\n ?/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    const complete = text.length <= maxChars;
    if (!complete) text = text.slice(0, maxChars) + "\n\u2026[truncated]";

    const links = $$("a[href]")
      .filter((a) => /^https?:/.test(a.href))
      .map((a) => ({ t: trim(a.innerText || a.textContent || "").slice(0, 60), h: a.href }))
      .filter((l) => l.h)
      .slice(0, maxLinks);

    const fields = $$("input, select, textarea")
      .filter(visible)
      .slice(0, maxFields)
      .map((el) => {
        const type =
          el.getAttribute("type") ||
          (el.tagName === "INPUT" ? "text" : el.tagName.toLowerCase());
        const secret = type === "password";
        return {
          selector: bestSelector(el),
          tag: el.tagName.toLowerCase(),
          type,
          name: nameOf(el),
          required: el.required === true,
          secret,
        };
      });

    return {
      ok: true,
      url: location.href,
      title: document.title,
      text,
      links,
      fields,
      contentHash: textHash(text),
      complete,
      readyState: document.readyState,
    };
  };

  // ---------- findElement ----------
  const findElement = ({
    role,
    text,
    tag,
    aria_label,
    visible = true,
    limit = 10,
  } = {}) => {
    const match = (el) => {
      if (visible && !visible(el)) return false;
      if (tag && el.tagName.toLowerCase() !== String(tag).toLowerCase())
        return false;
      if (aria_label) {
        const a = trim(el.getAttribute("aria-label") || "").toLowerCase();
        if (!a.includes(String(aria_label).toLowerCase())) return false;
      }
      if (role) {
        const r = roleOf(el).toLowerCase();
        if (!r.includes(String(role).toLowerCase())) return false;
      }
      if (text) {
        const t = trim(el.innerText || el.textContent || "").toLowerCase();
        if (!t.includes(String(text).toLowerCase())) return false;
      }
      return true;
    };
    const elements = $$("button, a, input, select, textarea, summary, [role]")
      .filter(match)
      .slice(0, limit)
      .map((el) => ({
        selector: bestSelector(el),
        tag: el.tagName.toLowerCase(),
        role: roleOf(el),
        name: nameOf(el),
        text: cap(trim(el.innerText || el.textContent || ""), 60),
        box: boxOf(el),
        enabled: !el.disabled && visible(el),
      }));
    return { ok: true, elements, count: elements.length };
  };

  // ---------- inspectElement ----------
  const SAFE_ATTRS = new Set([
    "id",
    "class",
    "name",
    "type",
    "placeholder",
    "title",
    "aria-label",
    "aria-labelledby",
    "aria-describedby",
    "role",
    "href",
    "src",
    "alt",
    "disabled",
    "readonly",
    "required",
    "checked",
    "selected",
    "data-testid",
  ]);

  const inspectElement = ({ selector } = {}) => {
    const el = document.querySelector(selector);
    if (!el) return { ok: false, error: "selector not found: " + selector };
    const attrs = {};
    const isPassword = (el.getAttribute("type") || "").toLowerCase() === "password";
    for (const a of el.attributes) {
      if (SAFE_ATTRS.has(a.name)) {
        attrs[a.name] =
          isPassword && a.name === "value" ? "\u2022\u2022\u2022\u2022\u2022" : a.value;
      }
    }
    return {
      ok: true,
      selector,
      tag: el.tagName.toLowerCase(),
      role: roleOf(el),
      attrs,
      box: boxOf(el),
      visible: visible(el),
      enabled: !el.disabled && visible(el),
      name: nameOf(el),
      text: cap(trim(el.innerText || el.textContent || ""), 200),
    };
  };

  // ---------- interactiveMap (JS accessibility-tree approximation) ----------
  const interactiveMap = ({ limit = 200 } = {}) => {
    const nodes = $$("button, a, input, select, textarea, summary, [role]")
      .filter(visible)
      .slice(0, limit)
      .map((el) => {
        const type = (el.getAttribute("type") || "").toLowerCase();
        const isPassword = type === "password";
        return {
          selector: bestSelector(el),
          tag: el.tagName.toLowerCase(),
          role: roleOf(el),
          name: nameOf(el),
          text: cap(trim(el.innerText || el.textContent || ""), 80),
          box: boxOf(el),
          checked:
            el.type === "checkbox" || el.type === "radio" ? el.checked === true : undefined,
          selected: el.selected === true,
          disabled: el.disabled === true,
          expanded: el.getAttribute("aria-expanded"),
          pressed: el.getAttribute("aria-pressed"),
          currentValue: isPassword
            ? "\u2022\u2022\u2022\u2022\u2022"
            : el.value || "",
        };
      });
    return { ok: true, nodes, count: nodes.length };
  };

  // ---------- pageSignature (cheap post-action verification fingerprint) ----------
  const pageSignature = () => {
    const dialogs = $$(
      "[role='dialog'], [role='alert'], [role='alertdialog'], .toast, [class*='toast'], [aria-modal='true']"
    )
      .filter(visible)
      .slice(0, 5)
      .map((el) => ({
        role: roleOf(el),
        name: cap(trim(el.innerText || el.textContent || ""), 120),
        selector: bestSelector(el),
      }));
    return {
      ok: true,
      url: location.href,
      title: document.title,
      textHash: textHash(document.body ? document.body.innerText : ""),
      dialogs,
      readyState: document.readyState,
    };
  };

  // ---------- viewportInfo ----------
  const viewportInfo = () => ({
    ok: true,
    width: window.innerWidth,
    height: window.innerHeight,
    scrollX: window.scrollX,
    scrollY: window.scrollY,
    devicePixelRatio: window.devicePixelRatio || 1,
  });

  // ---------- dispatcher (self-contained; no external closure) ----------
  const probes = {
    readPage,
    findElement,
    inspectElement,
    interactiveMap,
    pageSignature,
    viewportInfo,
  };

  const probe = probes[name];
  if (!probe) return { ok: false, error: "unknown probe: " + String(name) };
  try {
    return probe(args || {});
  } catch (e) {
    return { ok: false, error: String(e) };
  }
};
"""


def load_probes() -> str:
    """Return the canonical probe-JS source."""
    return _PROBES_JS


PROBES_JS = load_probes()


def probe_expression(name: str, args: dict | None = None) -> str:
    """Build a CDP ``Runtime.evaluate`` expression that runs probe ``name``.

    ``self.__nexus_probes = <library>; self.__nexus_probes("name", {...args})``
    -- defines + invokes the dispatcher in one round-trip. The library source
    assigns ``self.__nexus_probes`` so the extension's service worker can
    ``importScripts`` the same file and pass the function to
    ``chrome.scripting.executeScript``.
    """
    return (
        PROBES_JS
        + "; self.__nexus_probes("
        + _json.dumps(name)
        + ", "
        + _json.dumps(args or {})
        + ")"
    )
