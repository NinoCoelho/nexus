(async () => {
  const statusEl = document.getElementById("status");
  const domainsEl = document.getElementById("domains");
  const exportBtn = document.getElementById("exportBtn");
  const resultEl = document.getElementById("result");

  let nexusPort = null;

  function showStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = `status ${cls}`;
  }

  function showResult(text, cls) {
    resultEl.textContent = text;
    resultEl.className = `result ${cls}`;
  }

  function clearResult() {
    resultEl.textContent = "";
    resultEl.className = "result";
  }

  async function getPort() {
    try {
      const response = await chrome.runtime.sendNativeMessage(
        "com.nexus.cookies",
        { command: "get-port" }
      );
      if (response && response.port) {
        return response.port;
      }
    } catch (e) {
      console.warn("Native messaging failed:", e);
    }
    return null;
  }

  async function probeHealth(port) {
    try {
      const resp = await fetch(`http://localhost:${port}/health`, {
        signal: AbortSignal.timeout(2000),
      });
      return resp.ok;
    } catch {
      return false;
    }
  }

  async function discoverPort() {
    const port = await getPort();
    if (port && (await probeHealth(port))) {
      return port;
    }
    for (let p = 18989; p <= 18999; p++) {
      if (await probeHealth(p)) {
        return p;
      }
    }
    return null;
  }

  async function getCookiesForDomain(domain) {
    const details = { domain };
    return new Promise((resolve) => {
      chrome.cookies.getAll(details, (cookies) => resolve(cookies || []));
    });
  }

  function renderDomains(domainMap, currentDomain) {
    domainsEl.innerHTML = "";
    const entries = [...domainMap.entries()].sort((a, b) => {
      if (a[0] === currentDomain) return -1;
      if (b[0] === currentDomain) return 1;
      return b[1].length - a[1].length;
    });

    if (entries.length === 0) {
      domainsEl.innerHTML = '<div class="empty">No cookies found</div>';
      return;
    }

    const labelRow = document.createElement("div");
    labelRow.className = "label-row";
    labelRow.innerHTML = `
      <label id="selectedLabel">0 selected</label>
      <a id="toggleAll">Select all</a>
    `;
    domainsEl.appendChild(labelRow);

    const selectedLabel = labelRow.querySelector("#selectedLabel");
    const toggleAll = labelRow.querySelector("#toggleAll");

    for (const [domain, cookies] of entries) {
      const item = document.createElement("div");
      item.className = "domain-item";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.dataset.domain = domain;
      if (domain === currentDomain) {
        checkbox.checked = true;
      }
      const nameSpan = document.createElement("span");
      nameSpan.className = "domain-name";
      nameSpan.textContent = domain;
      nameSpan.title = domain;
      const countSpan = document.createElement("span");
      countSpan.className = "domain-count";
      countSpan.textContent = `${cookies.length}`;
      item.appendChild(checkbox);
      item.appendChild(nameSpan);
      item.appendChild(countSpan);
      domainsEl.appendChild(item);

      checkbox.addEventListener("change", updateSelection);
    }

    toggleAll.addEventListener("click", () => {
      const checkboxes = domainsEl.querySelectorAll('input[type="checkbox"]');
      const allChecked = [...checkboxes].every((cb) => cb.checked);
      checkboxes.forEach((cb) => (cb.checked = !allChecked));
      toggleAll.textContent = allChecked ? "Select all" : "Deselect all";
      updateSelection();
    });

    updateSelection();
  }

  function updateSelection() {
    const checked = domainsEl.querySelectorAll(
      'input[type="checkbox"]:checked'
    );
    exportBtn.disabled = checked.length === 0;
    const label = domainsEl.querySelector("#selectedLabel");
    if (label) {
      label.textContent = `${checked.length} selected`;
    }
  }

  async function exportCookies() {
    clearResult();
    exportBtn.disabled = true;
    exportBtn.textContent = "Exporting...";

    const checked = domainsEl.querySelectorAll(
      'input[type="checkbox"]:checked'
    );
    const allCookies = await chrome.cookies.getAll({});

    const domainMap = new Map();
    for (const cb of checked) {
      const domain = cb.dataset.domain;
      const cookies = allCookies.filter((c) => {
        return c.domain === domain || c.domain === `.${domain}`;
      });
      if (cookies.length > 0) {
        domainMap.set(domain, cookies);
      }
    }

    if (domainMap.size === 0) {
      showResult("No cookies to export", "error");
      exportBtn.disabled = false;
      exportBtn.textContent = "Export selected";
      return;
    }

    const payload = {
      domains: [...domainMap.entries()].map(([domain, cookies]) => ({
        domain,
        cookies: cookies.map((c) => ({
          name: c.name,
          value: c.value,
          domain: c.domain,
          path: c.path || "/",
          secure: c.secure,
          httpOnly: c.httpOnly,
          expirationDate: c.expirationDate || 0,
        })),
      })),
    };

    try {
      const resp = await fetch(`http://localhost:${nexusPort}/cookies/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const data = await resp.json();
      showResult(
        `Exported ${data.imported} cookies for ${data.domains.length} domain(s)`,
        "success"
      );
    } catch (e) {
      showResult(`Export failed: ${e.message}`, "error");
    } finally {
      exportBtn.disabled = false;
      exportBtn.textContent = "Export selected";
      updateSelection();
    }
  }

  exportBtn.addEventListener("click", exportCookies);

  showStatus("Connecting to Nexus...", "disconnected");

  nexusPort = await discoverPort();
  if (!nexusPort) {
    showStatus(
      "Cannot reach Nexus. Is the server running?",
      "disconnected"
    );
    domainsEl.innerHTML =
      '<div class="empty">Start Nexus and reload this popup.</div>';
    return;
  }

  showStatus(`Connected (port ${nexusPort})`, "connected");

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  let currentHost = "";
  try {
    currentHost = new URL(tab.url).hostname;
  } catch {}

  const allCookies = await chrome.cookies.getAll({});
  const domainMap = new Map();
  for (const cookie of allCookies) {
    const d = cookie.domain.startsWith(".")
      ? cookie.domain.slice(1)
      : cookie.domain;
    if (!domainMap.has(d)) {
      domainMap.set(d, []);
    }
    domainMap.get(d).push(cookie);
  }

  renderDomains(domainMap, currentHost);
})();
