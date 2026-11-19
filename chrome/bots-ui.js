// bots-ui.js — multi-agent UI for the Nexus Chrome side panel.
// Overlays: the bot/chat/team picker, the forward dialog and the new-group
// dialog. Pure DOM + callbacks — panel.js owns all state and networking;
// these helpers only render what they are handed.

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

// Server timestamps are unix seconds.
function fmtAgo(ts) {
  if (!ts) return "";
  const s = Math.max(1, Math.floor((Date.now() - ts * 1000) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function avatarEl(bot) {
  const a = el("span", "bot-avatar");
  if (bot && bot.color && /^#[0-9a-f]{3,8}$/i.test(bot.color)) {
    a.style.background = bot.color;
    a.style.boxShadow = "none";
  }
  if (bot && bot.icon) {
    a.textContent = String(bot.icon).slice(0, 2);
    a.classList.add("has-icon");
  }
  return a;
}

function botLabel(bot) {
  if (!bot) return "Nexus";
  const h = bot.kind === "coordinator" ? "★ " : "@";
  return `${h}${bot.handle}`;
}

// Shared overlay scaffold: backdrop + Escape/click-outside close.
function overlay(onClose) {
  const back = el("div", "nx-overlay");
  let closed = false;
  const onKey = (e) => {
    if (e.key === "Escape") close();
  };
  function close() {
    if (closed) return;
    closed = true;
    back.remove();
    document.removeEventListener("keydown", onKey);
    if (onClose) onClose();
  }
  back.addEventListener("click", (e) => {
    if (e.target === back) close();
  });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(back);
  return { back, close };
}

// ── Picker ───────────────────────────────────────────────────────────────
// One searchable list: bots (expandable to their recent chats), teams and
// the new-group action. `current` is {projectId, sessionId} for highlight.

export function openPicker(opts) {
  const o = overlay(opts.onClose);
  const panel = el("div", "nx-menu");
  const search = el("input", "nx-menu-search");
  search.type = "search";
  search.placeholder = "Search bots, chats, teams…";
  panel.appendChild(search);
  const list = el("div", "nx-menu-list");
  panel.appendChild(list);
  o.back.appendChild(panel);
  search.focus();

  const expanded = new Set();
  let q = "";
  const matches = (...parts) =>
    !q || parts.some((p) => String(p || "").toLowerCase().includes(q));

  function botChatRows(bot) {
    const frag = document.createDocumentFragment();
    const projectId = bot.kind === "coordinator" ? null : bot.id;
    const sessions = (opts.sessionsForBot(bot.id) || []).filter((s) =>
      matches(s.title)
    );
    const newChat = el("button", "nx-row nx-row-action", "＋ New chat");
    newChat.onclick = () => {
      o.close();
      opts.onNewChat(bot, projectId);
    };
    frag.appendChild(newChat);
    if (!sessions.length) {
      frag.appendChild(el("div", "nx-empty", "no chats yet"));
      return frag;
    }
    for (const s of sessions.slice(0, 6)) {
      const row = el("button", "nx-row nx-row-session");
      if (s.id === opts.currentSessionId) row.classList.add("active");
      row.appendChild(el("span", "nx-row-title", s.title || "Untitled chat"));
      row.appendChild(el("span", "nx-ago", fmtAgo(s.updated_at)));
      row.onclick = () => {
        o.close();
        opts.onPickSession(s);
      };
      frag.appendChild(row);
    }
    return frag;
  }

  function botRow(bot) {
    const isCoord = bot.kind === "coordinator";
    // Unprojected chats are coordinator-adjacent — highlight it then.
    const active = isCoord ? !opts.currentProjectId : bot.id === opts.currentProjectId;
    const row = el("button", "nx-row nx-row-bot" + (active ? " active" : ""));
    row.appendChild(avatarEl(bot));
    const main = el("span", "nx-row-main");
    main.appendChild(el("span", "nx-row-title", botLabel(bot)));
    main.appendChild(
      el("span", "nx-row-sub", [bot.name, bot.description].filter(Boolean).join(" — "))
    );
    row.appendChild(main);
    if (isCoord) row.appendChild(el("span", "nx-badge", "master"));
    row.appendChild(el("span", "nx-caret", expanded.has(bot.id) ? "▾" : "▸"));
    row.onclick = () => {
      const open = expanded.has(bot.id);
      if (open) expanded.delete(bot.id);
      else expanded.add(bot.id);
      render();
    };
    const frag = document.createDocumentFragment();
    frag.appendChild(row);
    if (expanded.has(bot.id)) frag.appendChild(botChatRows(bot));
    return frag;
  }

  function teamRow(t) {
    const row = el("button", "nx-row nx-row-team");
    if (t.session_id === opts.currentSessionId) row.classList.add("active");
    row.appendChild(el("span", "nx-flag", "⚑"));
    const main = el("span", "nx-row-main");
    main.appendChild(el("span", "nx-row-title", t.name || t.title || "Team"));
    main.appendChild(
      el(
        "span",
        "nx-row-sub",
        (t.members || []).map((m) => "@" + m.handle).join(" ") + ` · ${t.mode}`
      )
    );
    row.appendChild(main);
    row.appendChild(el("span", "nx-ago", fmtAgo(t.updated_at)));
    row.onclick = () => {
      o.close();
      opts.onPickTeam(t);
    };
    return row;
  }

  function render() {
    list.innerHTML = "";
    const bots = opts.bots || [];
    const botHits = bots.filter((b) => matches(b.handle, b.name, b.description));
    if (!bots.length) {
      list.appendChild(
        el("div", "nx-empty", "Could not load bots — is the Nexus server running?")
      );
    }
    for (const bot of botHits) list.appendChild(botRow(bot));

    const sec = el("div", "nx-sec");
    sec.appendChild(el("div", "nx-sec-title", "Group chats"));
    const teamHits = (opts.teams || []).filter((t) =>
      matches(t.name, t.title, (t.members || []).map((m) => m.handle).join(" "))
    );
    if (!teamHits.length && !q) {
      sec.appendChild(el("div", "nx-empty", "no group chats yet"));
    }
    for (const t of teamHits) sec.appendChild(teamRow(t));
    const newGroup = el("button", "nx-row nx-row-action", "＋ New group…");
    newGroup.onclick = () => {
      o.close();
      opts.onNewGroup();
    };
    sec.appendChild(newGroup);
    list.appendChild(sec);
  }

  search.addEventListener("input", () => {
    q = search.value.trim().toLowerCase();
    render();
  });
  render();
}

// ── Forward dialog ───────────────────────────────────────────────────────
// Pick a bot, target its latest chat (default) or a new one, edit the
// forwarded text. onSend({bot, sessionId, projectId, text}).

export function openForwardDialog({ bots, sessionsForBot, defaultText, onSend }) {
  const o = overlay();
  const panel = el("div", "nx-dlg");
  panel.appendChild(el("div", "nx-dlg-title", "Forward to a bot"));

  const sel = el("select", "nx-dlg-select");
  for (const b of bots || []) {
    const opt = el("option", null, `${botLabel(b)} · ${b.name || ""}`);
    opt.value = b.id;
    sel.appendChild(opt);
  }
  panel.appendChild(sel);

  const targets = el("div", "nx-dlg-targets");

  function renderTargets(botId) {
    targets.innerHTML = "";
    const sessions = sessionsForBot(botId) || [];
    const latest = sessions[0];
    const mkRadio = (val, label, checked) => {
      const lab = el("label", "nx-radio");
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "fwd-target";
      input.value = val;
      input.checked = checked;
      lab.appendChild(input);
      lab.appendChild(el("span", null, label));
      return lab;
    };
    if (latest) {
      targets.appendChild(
        mkRadio(
          "latest",
          `Latest chat: ${latest.title || "untitled"} (${fmtAgo(latest.updated_at)})`,
          true
        )
      );
    } else {
      targets.appendChild(el("div", "nx-empty", "no existing chats yet"));
    }
    targets.appendChild(mkRadio("new", "Start a new chat", !latest));
  }
  renderTargets(sel.value);
  sel.onchange = () => renderTargets(sel.value);
  panel.appendChild(targets);

  const ta = el("textarea", "nx-dlg-text");
  ta.rows = 6;
  ta.value = defaultText || "";
  ta.placeholder = "What should the bot pick up? Edit freely…";
  panel.appendChild(ta);

  const row = el("div", "nx-dlg-row");
  const cancel = el("button", null, "Cancel");
  cancel.onclick = () => o.close();
  const send = el("button", "nx-primary", "Forward");
  send.onclick = () => {
    const bot = (bots || []).find((b) => b.id === sel.value);
    const text = ta.value.trim();
    if (!bot || !text) {
      if (!text) ta.focus();
      return;
    }
    const mode =
      targets.querySelector("input[name=fwd-target]:checked")?.value ||
      (sessionsForBot(sel.value)[0] ? "latest" : "new");
    const sid = mode === "latest" ? sessionsForBot(sel.value)[0]?.id || null : null;
    o.close();
    onSend({
      bot,
      sessionId: sid,
      projectId: bot.kind === "coordinator" ? null : bot.id,
      text,
    });
  };
  row.appendChild(cancel);
  row.appendChild(send);
  panel.appendChild(row);
  o.back.appendChild(panel);
  ta.focus();
}

// ── New group dialog ─────────────────────────────────────────────────────
// name + member checkboxes (first checked = lead) + mode.
// onCreate({name, members, mode}).

export function openNewGroupDialog({ bots, onCreate }) {
  const o = overlay();
  const panel = el("div", "nx-dlg");
  panel.appendChild(el("div", "nx-dlg-title", "New group chat"));

  const name = el("input", "nx-dlg-input");
  name.type = "text";
  name.placeholder = "Group name";
  panel.appendChild(name);

  panel.appendChild(
    el(
      "div",
      "nx-dlg-hint",
      "The first member is the lead — it answers messages that @mention nobody."
    )
  );

  const memberList = el("div", "nx-dlg-members");
  const boxes = [];
  for (const b of bots || []) {
    const lab = el("label", "nx-check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = b.id;
    lab.appendChild(input);
    lab.appendChild(avatarEl(b));
    lab.appendChild(el("span", null, `${botLabel(b)} · ${b.name || ""}`));
    memberList.appendChild(lab);
    boxes.push(input);
  }
  panel.appendChild(memberList);

  const modeSel = el("select", "nx-dlg-select");
  const leadOpt = el("option", null, "lead — only the lead answers unless @mentioned");
  leadOpt.value = "lead";
  const roundOpt = el("option", null, "round — every member answers in turn");
  roundOpt.value = "round";
  modeSel.appendChild(leadOpt);
  modeSel.appendChild(roundOpt);
  panel.appendChild(modeSel);

  const err = el("div", "nx-dlg-err");
  err.hidden = true;
  panel.appendChild(err);

  const row = el("div", "nx-dlg-row");
  const cancel = el("button", null, "Cancel");
  cancel.onclick = () => o.close();
  const create = el("button", "nx-primary", "Create");
  create.onclick = () => {
    const gname = name.value.trim();
    const members = boxes.filter((b) => b.checked).map((b) => b.value);
    if (!gname || members.length < 2) {
      err.textContent = !gname
        ? "Give the group a name."
        : "Pick at least two bots.";
      err.hidden = false;
      return;
    }
    o.close();
    onCreate({ name: gname, members, mode: modeSel.value });
  };
  row.appendChild(cancel);
  row.appendChild(create);
  panel.appendChild(row);
  o.back.appendChild(panel);
  name.focus();
}
