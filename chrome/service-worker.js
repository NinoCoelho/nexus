const SESSION_MAP_KEY = "tabSessions";
const ACTIVATED_KEY = "activatedTabs";
const OPEN_KEY = "openTabs";
const GROUP_TITLE = "Nexus";
const GROUP_COLOR = "purple";
const PANEL_PATH = "panel.html";

let openTabs = {};
let activatedTabs = {};

const EXT_VERSION = chrome.runtime.getManifest().version;

async function apiBase() {
  const data = await chrome.storage.local.get("nexusPort");
  const port = Number(data.nexusPort) || 18989;
  return `http://localhost:${port}`;
}

async function pingServer() {
  try {
    const res = await fetch(`${await apiBase()}/ext/ping`, {
      headers: { "X-Nexus-Extension": EXT_VERSION },
    });
    if (res.ok) {
      await chrome.storage.local.set({ lastPingAt: Date.now() });
    }
  } catch (_) {}
}

pingServer();
chrome.runtime.onStartup.addListener(pingServer);
chrome.runtime.onInstalled.addListener(pingServer);

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => {});

chrome.runtime.onInstalled.addListener(() => {
  const mk = (id, title, contexts) =>
    chrome.contextMenus.create({ id, title, contexts }, () => void chrome.runtime.lastError);
  mk("nexus-open", "Chat with Nexus on this tab", ["action"]);
  mk("nexus-page", "Open Nexus chat on this tab", ["page"]);
  mk("nexus-selection", "Ask Nexus about this selection", ["selection"]);
  mk("nexus-link", "Ask Nexus about this link", ["link"]);
});

async function getMap(key) {
  const data = await chrome.storage.session.get(key);
  return data[key] || {};
}

async function setMap(key, map) {
  await chrome.storage.session.set({ [key]: map });
}

function newSessionId() {
  return crypto.randomUUID().replace(/-/g, "");
}

async function applyPolicy(tab) {
  const want = activatedTabs[tab.id] != null;
  let opts = {};
  try {
    opts = await chrome.sidePanel.getOptions({ tabId: tab.id });
  } catch (_) {}
  if (want) {
    await chrome.sidePanel.setOptions({ tabId: tab.id, path: PANEL_PATH, enabled: true });
  } else if (opts.enabled !== false) {
    await chrome.sidePanel.setOptions({ tabId: tab.id, enabled: false });
  }
}

async function healAllTabs() {
  try {
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      try {
        await applyPolicy(tab);
      } catch (_) {}
    }
  } catch (_) {}
}

getMap(ACTIVATED_KEY).then((m) => {
  activatedTabs = m;
  healAllTabs();
}).catch(() => {});

chrome.storage.session.remove(OPEN_KEY).catch(() => {});

if (chrome.sidePanel.onOpened) {
  chrome.sidePanel.onOpened.addListener((info) => {
    if (info.tabId != null) {
      openTabs[info.tabId] = true;
      setMap(OPEN_KEY, openTabs).catch(() => {});
    }
  });
}

if (chrome.sidePanel.onClosed) {
  chrome.sidePanel.onClosed.addListener((info) => {
    if (info.tabId != null) {
      delete openTabs[info.tabId];
      setMap(OPEN_KEY, openTabs).catch(() => {});
    }
  });
}

async function activateTab(tab) {
  if (activatedTabs[tab.id] != null) return false;
  activatedTabs[tab.id] = tab.windowId;
  await setMap(ACTIVATED_KEY, activatedTabs);
  try {
    await chrome.sidePanel.setOptions({ tabId: tab.id, path: PANEL_PATH, enabled: true });
  } catch (_) {}
  handleGroupAdd({ tabId: tab.id }).catch(() => {});
  return true;
}

async function finishTab(tab) {
  try {
    await chrome.tabs.ungroup(tab.id);
  } catch (_) {}
  if (activatedTabs[tab.id] != null) {
    delete activatedTabs[tab.id];
    await setMap(ACTIVATED_KEY, activatedTabs);
  }
  await handleRemoveSession(tab.id);
  try {
    await chrome.sidePanel.setOptions({ tabId: tab.id, enabled: false });
  } catch (_) {}
  delete openTabs[tab.id];
  await setMap(OPEN_KEY, openTabs);
  if (chrome.sidePanel.close) {
    try {
      await chrome.sidePanel.close({ tabId: tab.id });
    } catch (_) {}
  }
}

function tryOpen(tabId) {
  chrome.sidePanel.open({ tabId }).catch((err) => {
    console.warn("[nexus] sidePanel.open failed:", err && err.message);
  });
}

function onActivateGesture(tab, { toggle = true } = {}) {
  if (toggle && activatedTabs[tab.id] != null && openTabs[tab.id] != null) {
    finishTab(tab).catch((err) => {
      console.warn("[nexus] finishTab failed:", err && err.message);
    });
    return;
  }
  if (openTabs[tab.id] == null) {
    if (activatedTabs[tab.id] == null) {
      chrome.sidePanel
        .setOptions({ tabId: tab.id, path: PANEL_PATH, enabled: true })
        .catch(() => {});
    }
    tryOpen(tab.id);
  }
  activateTab(tab)
    .then((fresh) => {
      if (fresh && openTabs[tab.id] == null) tryOpen(tab.id);
    })
    .catch((err) => {
      console.warn("[nexus] activateTab failed:", err && err.message);
    });
}

function seedComposer(text) {
  chrome.storage.session
    .set({ composerSeed: text })
    .then(() => chrome.runtime.sendMessage({ type: "SEED_COMPOSER", text }))
    .catch(() => {});
}

chrome.action.onClicked.addListener((tab) => {
  onActivateGesture(tab);
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab) return;
  if (info.menuItemId === "nexus-open" || info.menuItemId === "nexus-page") {
    onActivateGesture(tab, { toggle: false });
    return;
  }
  if (info.menuItemId === "nexus-selection") {
    const sel = (info.selectionText || "").trim();
    onActivateGesture(tab, { toggle: false });
    if (sel) {
      seedComposer(`About this selection:\n\n> ${sel.replace(/\n/g, "\n> ")}\n\n`);
    }
    return;
  }
  if (info.menuItemId === "nexus-link") {
    const url = info.linkUrl || "";
    onActivateGesture(tab, { toggle: false });
    if (url) seedComposer(`About this link: ${url}\n\n`);
  }
});

chrome.tabs.onCreated.addListener((tab) => {
  applyPolicy(tab).catch(() => {});
});

chrome.tabs.onActivated.addListener(({ tabId }) => {
  chrome.tabs
    .get(tabId)
    .then((tab) => applyPolicy(tab))
    .catch(() => {});
});

async function handleGetState(msg) {
  const tabs = await chrome.tabs.query({ active: true, windowId: msg.windowId });
  const tab = tabs[0];
  if (!tab) throw new Error("no active tab");
  const map = await getMap(SESSION_MAP_KEY);
  if (!map[tab.id]) {
    map[tab.id] = { sessionId: newSessionId() };
    await setMap(SESSION_MAP_KEY, map);
  }
  return {
    tabId: tab.id,
    windowId: tab.windowId,
    sessionId: map[tab.id].sessionId,
    url: tab.url || "",
    title: tab.title || "",
  };
}

async function handleNewSession(msg) {
  const map = await getMap(SESSION_MAP_KEY);
  map[msg.tabId] = { sessionId: newSessionId() };
  await setMap(SESSION_MAP_KEY, map);
  return { ok: true, sessionId: map[msg.tabId].sessionId };
}

async function handleRemoveSession(tabId) {
  const map = await getMap(SESSION_MAP_KEY);
  if (map[tabId]) {
    delete map[tabId];
    await setMap(SESSION_MAP_KEY, map);
  }
}

function extractPageText(maxChars) {
  const noise =
    "nav, header, footer, aside, script, style, noscript, svg, iframe, form, " +
    "[aria-hidden='true'], [hidden], [role='navigation'], [role='banner'], " +
    "[role='contentinfo'], [role='complementary']";
  const blocks = new Set(["P", "DIV", "LI", "H1", "H2", "H3", "H4", "H5", "H6", "TR", "BLOCKQUOTE", "PRE", "SECTION", "ARTICLE", "TD", "SPAN"]);
  let root =
    document.querySelector("article") ||
    document.querySelector("main") ||
    document.querySelector("[role='main']") ||
    document.querySelector("#content") ||
    document.body;
  if (!root || !root.innerText || !root.innerText.trim()) root = document.body;
  if (!root) return { url: location.href, title: document.title, text: "" };
  const parts = [];
  let lastBlock = "";
  let total = 0;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const p = node.parentElement;
      if (!p) return NodeFilter.FILTER_REJECT;
      const tag = p.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT" || tag === "SVG" || tag === "IFRAME") {
        return NodeFilter.FILTER_REJECT;
      }
      if (p.closest(noise)) return NodeFilter.FILTER_REJECT;
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  while (walker.nextNode()) {
    const node = walker.currentNode;
    const val = node.nodeValue.trim();
    if (!val) continue;
    let block = node.parentElement;
    while (block && block !== root && !blocks.has(block.tagName)) block = block.parentElement;
    const btag = block ? block.tagName : "";
    if (btag && btag !== lastBlock) {
      parts.push("\n");
      lastBlock = btag;
    } else if (parts.length && total > 0) {
      parts.push(" ");
    }
    parts.push(val);
    total += val.length + 1;
    if (total > maxChars + 200) break;
  }
  let text = parts.join("").replace(/[ \t]+/g, " ").replace(/ ?\n ?/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  if (text.length > maxChars) text = text.slice(0, maxChars) + "\n…[truncated]";
  return { url: location.href, title: document.title, text };
}

function pageRead(selector, full, mode) {
  if (mode === "outline") {
    const out = [];
    for (const h of document.querySelectorAll("h1,h2,h3,h4")) {
      const t = (h.innerText || "").trim();
      if (t) out.push(`${"#".repeat(Number(h.tagName[1]))} ${t}`);
      if (out.length >= 120) break;
    }
    return { ok: true, url: location.href, title: document.title, outline: out };
  }
  if (mode === "links") {
    const seen = new Set();
    const out = [];
    for (const a of document.querySelectorAll("a[href]")) {
      const t = (a.innerText || "").trim();
      if (!t || !/^https?:/.test(a.href)) continue;
      const key = `${t}|${a.href}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(`[${t.slice(0, 80)}] ${a.href}`);
      if (out.length >= 60) break;
    }
    return { ok: true, url: location.href, title: document.title, links: out };
  }
  if (!selector) return { ok: false, error: "selector required for raw reads" };
  const root = document.querySelector(selector);
  if (!root) return { ok: false, error: `selector not found: ${selector}` };
  const cap = full ? 40000 : 12000;
  const raw = (root.innerText || "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  const text = raw.length > cap ? raw.slice(0, cap) + "\n…[truncated]" : raw;
  return { ok: true, url: location.href, title: document.title, text };
}

function pageClick(selector, text) {
  let el = selector ? document.querySelector(selector) : null;
  if (!el && text) {
    const nodes = document.querySelectorAll(
      "button, a, input, [role='button'], summary, li, div, span"
    );
    for (const n of nodes) {
      const label = (n.innerText || n.value || "").trim();
      if (label && label.includes(text)) {
        el = n;
        break;
      }
    }
  }
  if (!el) {
    return { ok: false, error: `element not found (${selector || `text: ${text}`})` };
  }
  el.scrollIntoView({ block: "center" });
  el.click();
  return { ok: true, clicked: selector || text || el.tagName.toLowerCase() };
}

function pageType(selector, value, submit) {
  const el = document.querySelector(selector || "input:focus, textarea:focus");
  if (!el) return { ok: false, error: `element not found: ${selector}` };
  el.focus();
  const desc = Object.getOwnPropertyDescriptor(el.constructor.prototype, "value");
  if (desc && desc.set) desc.set.call(el, value);
  else el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  if (submit) {
    for (const type of ["keydown", "keyup"]) {
      el.dispatchEvent(
        new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true })
      );
    }
  }
  return { ok: true, typed: value.length, submitted: !!submit };
}

function pageScroll(dy) {
  window.scrollBy({ top: dy || 600 });
  return { ok: true, y: Math.round(window.scrollY) };
}

function pageNavigate(url) {
  location.href = url;
  return { ok: true, navigating: url };
}

function pageJs(code) {
  try {
    const r = eval(code);
    if (r === undefined) return { ok: true };
    try {
      return { ok: true, result: JSON.parse(JSON.stringify(r)) };
    } catch (_) {
      return { ok: true, result: String(r) };
    }
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

function pageTranscript(lang) {
  const player = document.getElementById("movie_player");
  const pr =
    (player && player.getPlayerResponse && player.getPlayerResponse()) ||
    window.ytInitialPlayerResponse;
  const tracks =
    pr && pr.captions && pr.captions.playerCaptionsTracklistRenderer &&
    pr.captions.playerCaptionsTracklistRenderer.captionTracks;
  const title = (pr && pr.videoDetails && pr.videoDetails.title) || document.title;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const pickTrack = () => {
    if (!tracks || !tracks.length) return null;
    if (lang) {
      const t = tracks.find((x) =>
        (x.languageCode || "").toLowerCase().startsWith(String(lang).toLowerCase())
      );
      if (t) return t;
    }
    return tracks[0];
  };
  const viaFetch = async () => {
    const track = pickTrack();
    if (!track) return { fail: "no caption tracks in player data" };
    let r;
    try {
      r = await fetch(track.baseUrl + "&fmt=json3", { credentials: "include" });
    } catch (e) {
      return { fail: `timedtext fetch error: ${e}` };
    }
    const body = await r.text().catch(() => "");
    if (!body) return { fail: `timedtext returned HTTP ${r.status} with empty body` };
    let data;
    try {
      data = JSON.parse(body);
    } catch (e) {
      return { fail: `timedtext returned non-JSON (HTTP ${r.status})` };
    }
    const lines = (data.events || [])
      .map((e) => (e.segs || []).map((s) => s.utf8).join(""))
      .filter(Boolean);
    if (!lines.length) return { fail: "json3 response had no events" };
    return { lang: track.languageCode || "", via: "fetch", text: lines.join("\n") };
  };
  const viaDom = async () => {
    const exp =
      document.querySelector("#description-inline-expander #expand") ||
      document.querySelector("#expand");
    if (exp) {
      try {
        exp.click();
        await wait(300);
      } catch (e) {}
    }
    let clicked = false;
    for (let i = 0; i < 20; i++) {
      const segs = document.querySelectorAll("ytd-transcript-segment-renderer .segment-text");
      if (segs.length) {
        const lines = Array.from(segs)
          .map((s) => (s.textContent || "").trim())
          .filter(Boolean);
        if (lines.length) return { lang: "", via: "dom", text: lines.join("\n") };
      }
      if (!clicked) {
        const btn = document.querySelector(
          "ytd-video-description-transcript-section-renderer button"
        );
        if (btn) {
          try {
            btn.click();
            clicked = true;
          } catch (e) {}
        }
      }
      await wait(500);
    }
    return { fail: "transcript panel did not open" };
  };
  return (async () => {
    const a = await viaFetch();
    if (a.text) return a;
    const b = await viaDom();
    if (b.text) return b;
    return { fail: `${a.fail}; ${b.fail}` };
  })().then((res) => {
    if (!res.text) return { ok: false, error: res.fail };
    const text = res.text.length > 60000 ? res.text.slice(0, 60000) + "\n…[truncated]" : res.text;
    return { ok: true, url: location.href, title, lang: res.lang || "", via: res.via, text };
  });
}

const PAGE_ACTIONS = {
  read: { func: pageRead, args: ["selector", "full", "mode"] },
  click: { func: pageClick, args: ["selector", "text"] },
  type: { func: pageType, args: ["selector", "value", "submit"] },
  scroll: { func: pageScroll, args: ["dy"] },
};

const readCache = new Map();
const READ_TTL_MS = 15000;

function invalidateReadCache(tabId) {
  const prefix = `${tabId}|`;
  for (const key of readCache.keys()) {
    if (key.startsWith(prefix)) readCache.delete(key);
  }
}

async function resolveTargetTabId(msg) {
  if (!msg.tab) return msg.tabId;
  const base = await chrome.tabs.get(msg.tabId);
  const info = await groupInfo(base);
  const needle = String(msg.tab).toLowerCase();
  const candidates = info.others;
  const match =
    candidates.find((t) => (t.url || "").toLowerCase().includes(needle)) ||
    candidates.find((t) => (t.title || "").toLowerCase().includes(needle));
  if (!match) {
    const known = candidates.map((t) => t.title || t.url).join("; ");
    throw new Error(`no grouped tab matches "${msg.tab}"${known ? ` (grouped: ${known})` : " (group is empty)"}`);
  }
  return match.tabId;
}

async function handlePageAct(msg) {
  const params = msg.params || {};
  const tabId = await resolveTargetTabId(msg);
  if (msg.action === "navigate") {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: pageNavigate,
        args: [params.url || ""],
      });
    } catch (_) {}
    return { ok: true, navigating: params.url };
  }
  if (msg.action === "js") {
    try {
      const [res] = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: pageJs,
        args: [params.code || ""],
      });
      return res && res.result ? res.result : { ok: false, error: "no result" };
    } catch (err) {
      return { ok: false, error: String(err) };
    }
  }
  if (msg.action === "transcript") {
    try {
      const [res] = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: pageTranscript,
        args: [params.lang || ""],
      });
      return res && res.result ? res.result : { ok: false, error: "no result" };
    } catch (err) {
      return { ok: false, error: String(err) };
    }
  }
  const spec = PAGE_ACTIONS[msg.action];
  if (!spec) return { ok: false, error: `unknown action: ${msg.action}` };
  if (
    msg.action === "read" &&
    !params.selector &&
    (!params.mode || params.mode === "text")
  ) {
    const cached = await extractTab(tabId, params.full ? 40000 : 12000);
    return cached ? { ok: true, ...cached } : { ok: false, error: "extraction failed" };
  }
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      func: spec.func,
      args: spec.args.map((k) => (params[k] === undefined ? null : params[k])),
    });
    return res && res.result ? res.result : { ok: false, error: "no result" };
  } catch (err) {
    return { ok: false, error: String(err) };
  }
}

async function extractTab(tabId, maxChars) {
  let url = "";
  try {
    const t = await chrome.tabs.get(tabId);
    url = t.url || "";
  } catch (_) {
    return null;
  }
  const key = `${tabId}|${url}|${maxChars}`;
  const hit = readCache.get(key);
  if (hit && Date.now() - hit.at < READ_TTL_MS) return hit.val;
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId },
      func: extractPageText,
      args: [maxChars],
    });
    if (res && res.result) {
      readCache.set(key, { at: Date.now(), val: res.result });
      return res.result;
    }
  } catch (err) {
    console.warn("[nexus] extractTab failed:", err && err.message);
  }
  return null;
}

async function findNexusGroup(windowId) {
  const groups = await chrome.tabGroups.query({ windowId });
  return groups.find((g) => g.title === GROUP_TITLE) || null;
}

async function groupInfo(tab) {
  const group = await findNexusGroup(tab.windowId);
  const others = group
    ? (await chrome.tabs.query({ windowId: tab.windowId, groupId: group.id }))
        .filter((t) => t.id !== tab.id)
        .map((t) => ({ tabId: t.id, url: t.url || "", title: t.title || "" }))
    : [];
  return { inGroup: !!group && tab.groupId === group.id, others, groupId: group ? group.id : -1 };
}

async function handleGroupInfo(msg) {
  const tab = await chrome.tabs.get(msg.tabId);
  const info = await groupInfo(tab);
  return { ok: true, ...info };
}

async function handleGroupAdd(msg) {
  const tab = await chrome.tabs.get(msg.tabId);
  const existing = await findNexusGroup(tab.windowId);
  if (existing && tab.groupId === existing.id) {
    return { ok: true, groupId: existing.id };
  }
  let groupId;
  if (existing) {
    groupId = await chrome.tabs.group({ tabIds: [tab.id], groupId: existing.id });
  } else {
    groupId = await chrome.tabs.group({ tabIds: [tab.id], createProperties: { windowId: tab.windowId } });
    await chrome.tabGroups.update(groupId, { title: GROUP_TITLE, color: GROUP_COLOR });
  }
  return { ok: true, groupId };
}

async function handleExtractContext(msg) {
  const tab = await chrome.tabs.get(msg.tabId);
  const page = (await extractTab(msg.tabId, msg.maxChars || 800)) || {
    url: tab.url || "",
    title: tab.title || "",
    text: null,
  };
  const info = await groupInfo(tab);
  const group = info.others.slice(0, 8).map((t) => ({ url: t.url, title: t.title }));
  return { ok: true, page, group, inGroup: info.inGroup, groupCount: info.others.length };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "GET_STATE") {
        sendResponse(await handleGetState(msg));
      } else if (msg.type === "EXTRACT_CONTEXT") {
        sendResponse(await handleExtractContext(msg));
      } else if (msg.type === "GROUP_INFO") {
        sendResponse(await handleGroupInfo(msg));
      } else if (msg.type === "GROUP_ADD") {
        sendResponse(await handleGroupAdd(msg));
      } else if (msg.type === "PAGE_ACT") {
        sendResponse(await handlePageAct(msg));
      } else if (msg.type === "NEW_SESSION") {
        sendResponse(await handleNewSession(msg));
      } else {
        sendResponse({ error: "unknown message type" });
      }
    } catch (err) {
      sendResponse({ error: String(err && err.message ? err.message : err) });
    }
  })();
  return true;
});

function broadcast(payload) {
  chrome.runtime.sendMessage(payload).catch(() => {});
}

chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.url || change.title || change.status === "complete") {
    invalidateReadCache(tabId);
    broadcast({ type: "TAB_UPDATED", tabId, windowId: tab.windowId, url: tab.url || "", title: tab.title || "" });
  }
});

chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  (async () => {
    await handleRemoveSession(tabId);
    if (activatedTabs[tabId] != null) {
      delete activatedTabs[tabId];
      await setMap(ACTIVATED_KEY, activatedTabs);
    }
    delete openTabs[tabId];
    await setMap(OPEN_KEY, openTabs);
    broadcast({ type: "TAB_REMOVED", tabId, windowId: removeInfo.windowId });
  })().catch(() => {});
});
