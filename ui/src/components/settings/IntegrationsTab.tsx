/**
 * IntegrationsTab — MCP server management with one-box paste wizard.
 *
 * User pastes whatever the MCP server docs give them (JSON, URL, npx command),
 * Nexus parses it, asks for missing credentials, tests, and saves.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  listMcpServers,
  reconnectMcpServer,
  reloadMcpServers,
  testMcpServer,
  type McpServerStatus,
  type McpTestResult,
} from "../../api/mcp";
import { getConfig, patchConfig, type McpConfig } from "../../api/config";
import { setCredential } from "../../api/credentials";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import SettingsSection from "./SettingsSection";
import { invalidateToolMetaCache } from "../StepDetailModal/ResultRenderers";
import {
  parseMcpConfig,
  looksLikeAuthError,
  detectUrlCredParams,
  type ParsedServer,
} from "../../lib/mcpParser";
import { useCredentials } from "../../hooks/useCredentials";

// ── Component ─────────────────────────────────────────────────────────────

type Phase = "idle" | "parsed" | "testing" | "tested" | "saving";

export default function IntegrationsTab() {
  const { t } = useTranslation("settings");
  const toast = useToast();
  const { reload: reloadCreds } = useCredentials();

  const [servers, setServers] = useState<McpServerStatus[]>([]);
  const [configServers, setConfigServers] = useState<Record<string, Record<string, unknown>>>({});
  const [loading, setLoading] = useState(true);
  const [reconnecting, setReconnecting] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Wizard
  const [wizardOpen, setWizardOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [parsed, setParsed] = useState<ParsedServer[]>([]);
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [phase, setPhase] = useState<Phase>("idle");
  const [testResult, setTestResult] = useState<McpTestResult | null>(null);
  const [testIdx, setTestIdx] = useState(0);
  const [existingCreds, setExistingCreds] = useState<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const [statusList, cfg] = await Promise.all([
        listMcpServers().catch((): McpServerStatus[] => []),
        getConfig(),
      ]);
      setServers(statusList);
      const mcfg = (cfg.mcp as McpConfig | undefined)?.servers;
      setConfigServers(mcfg ? JSON.parse(JSON.stringify(mcfg)) : {});
    } catch {
      setServers([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // ── Wizard logic ────────────────────────────────────────────────────

  async function startWizard() {
    setPasteText("");
    setParsed([]);
    setCreds({});
    setPhase("idle");
    setTestResult(null);
    setTestIdx(0);
    setWizardOpen(true);
    try {
      const all = await reloadCreds();
      setExistingCreds(new Set(all.map((c) => c.name)));
    } catch {
      setExistingCreds(new Set());
    }
  }

  function handlePaste(text: string) {
    setPasteText(text);
    if (!text.trim()) {
      setParsed([]);
      setPhase("idle");
      return;
    }
    const results = parseMcpConfig(text);
    if (results.length > 0) {
      setParsed(results);
      setTestIdx(0);
      const allMissing: Record<string, string> = {};
      for (let i = 0; i < results.length; i++) {
        const s = results[i];
        for (const k of s.missing) {
          allMissing[credKey(i, k)] = creds[credKey(i, k)] ?? "";
        }
        for (const um of s.urlMissing) {
          allMissing[credKey(i, um.credName)] = creds[credKey(i, um.credName)] ?? "";
        }
      }
      setCreds(allMissing);
      setPhase("parsed");
      setTestResult(null);
    } else {
      setParsed([]);
      setPhase("idle");
    }
  }

  function credKey(idx: number, key: string) {
    return `${idx}:${key}`;
  }

  function updateCred(key: string, value: string) {
    setCreds((prev) => ({ ...prev, [key]: value }));
  }

  async function handleTest() {
    setPhase("testing");
    const server = parsed[0];
    if (!server) { setPhase("parsed"); return; }

    let testUrl = server.url;
    for (const um of server.urlMissing) {
      if (existingCreds.has(um.credName)) continue;
      const value = creds[credKey(0, um.credName)] || "";
      testUrl = testUrl.replace(`$${um.credName}`, value);
    }

    const testEnv: Record<string, string> = { ...server.env };
    for (const k of server.missing) {
      if (existingCreds.has(k)) {
        testEnv[k] = `$${k}`;
      } else {
        testEnv[k] = creds[credKey(0, k)] || "";
      }
    }

    const testConfig: Record<string, unknown> = {
      transport: server.transport,
      command: server.command,
      url: testUrl,
      headers: server.headers,
      env: testEnv,
    };
    try {
      const result = await testMcpServer(testConfig);
      setTestResult(result);
      setPhase("tested");

      if (
        !result.ok
        && looksLikeAuthError(result.error)
        && server.urlMissing.length === 0
        && server.missing.length === 0
      ) {
        const detected = detectUrlCredParams(server.url, server.name);
        if (detected.length > 0) {
          const updated = { ...server };
          try {
            const u = new URL(updated.url);
            for (const d of detected) {
              u.searchParams.set(d.param, `$${d.credName}`);
            }
            updated.url = u.toString();
          } catch { /* keep url as-is */ }
          updated.urlMissing = detected;
          setParsed([updated]);
          setCreds((prev) => {
            const next = { ...prev };
            for (const d of detected) {
              next[credKey(0, d.credName)] = prev[credKey(0, d.credName)] ?? "";
            }
            return next;
          });
        }
      }
    } catch (e) {
      setTestResult({ ok: false, tool_count: 0, tools: [], error: e instanceof Error ? e.message : String(e) });
      setPhase("tested");
    }
  }

  async function handleSave() {
    setPhase("saving");
    try {
      for (let i = 0; i < parsed.length; i++) {
        const s = parsed[i];
        for (const k of s.missing) {
          if (existingCreds.has(k)) continue;
          const value = creds[credKey(i, k)] || "";
          if (value) {
            await setCredential(k, value, { kind: "generic" });
          }
        }
        for (const um of s.urlMissing) {
          if (existingCreds.has(um.credName)) continue;
          const value = creds[credKey(i, um.credName)] || "";
          if (value) {
            await setCredential(um.credName, value, { kind: "generic" });
          }
        }
      }

      const serversPatch: Record<string, Record<string, unknown> | null> = {};
      for (let i = 0; i < parsed.length; i++) {
        const s = parsed[i];
        const env = { ...s.env };
        for (const k of s.missing) {
          env[k] = `$${k}`;
        }
        serversPatch[s.name] = {
          transport: s.transport,
          command: s.command,
          url: s.url,
          env,
          headers: s.headers,
          enabled: true,
        };
      }
      await patchConfig({ mcp: { servers: serversPatch } });
      invalidateToolMetaCache();
      try {
        const reloadResult = await reloadMcpServers();
        toast.success(
          parsed.length === 1
            ? `"${parsed[0].name}" saved and connected (${reloadResult.tool_count} tools).`
            : `${parsed.length} servers saved and connected (${reloadResult.tool_count} tools).`,
        );
      } catch {
        toast.success(
          parsed.length === 1
            ? `"${parsed[0].name}" saved. Reopen to connect.`
            : `${parsed.length} servers saved. Reopen to connect.`,
        );
      }
      setWizardOpen(false);
      await refresh();
    } catch (e) {
      toast.error("Failed to save", { detail: e instanceof Error ? e.message : undefined });
      setPhase("tested");
    }
  }

  // ── Server list CRUD ────────────────────────────────────────────────

  async function deleteServer(name: string) {
    setConfirmDelete(null);
    try {
      await patchConfig({ mcp: { servers: { [name]: null } } });
      toast.success(`"${name}" removed. Restart to apply.`);
      await refresh();
    } catch (e) {
      toast.error("Failed to delete", { detail: e instanceof Error ? e.message : undefined });
    }
  }

  async function toggleServer(name: string, enabled: boolean) {
    try {
      await patchConfig({ mcp: { servers: { [name]: { enabled } } } });
      toast.success(enabled ? `"${name}" enabled` : `"${name}" disabled`);
      await refresh();
    } catch (e) {
      toast.error("Failed to update", { detail: e instanceof Error ? e.message : undefined });
    }
  }

  async function handleReconnect(name: string) {
    setReconnecting(name);
    try {
      await reconnectMcpServer(name);
      await refresh();
    } catch {
      // swallow
    } finally {
      setReconnecting(null);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────

  if (loading) return <p className="settings-loading">Loading…</p>;

  const allNames = new Set([...Object.keys(configServers), ...servers.map((s) => s.name)]);
  const currentServer = parsed[testIdx];
  const currentMissing = (currentServer?.missing ?? []).filter((k) => !existingCreds.has(k));
  const currentExistingEnv = (currentServer?.missing ?? []).filter((k) => existingCreds.has(k));
  const currentUrlMissing = (currentServer?.urlMissing ?? []).filter((um) => !existingCreds.has(um.credName));
  const currentExistingUrl = (currentServer?.urlMissing ?? []).filter((um) => existingCreds.has(um.credName));
  const hasAnyCreds = currentMissing.length > 0 || currentUrlMissing.length > 0;
  const allCredsFilled =
    currentMissing.every((k) => !!creds[credKey(testIdx, k)])
    && currentUrlMissing.every((um) => !!creds[credKey(testIdx, um.credName)]);

  return (
    <>
      <SettingsSection
        title={t("integrations.title", { defaultValue: "MCP Servers" })}
        description={t("integrations.description", {
          defaultValue:
            "Connect external tool servers via the Model Context Protocol. Paste a config from any MCP server's docs and Nexus handles the rest.",
        })}
      >
        <button
          type="button"
          className="settings-btn settings-btn--primary"
          style={{ marginBottom: "0.5rem" }}
          onClick={startWizard}
        >
          + Add server
        </button>

        {allNames.size === 0 && (
          <p className="s-field__hint">
            No servers yet. Add one to give your agent access to external tools like GitHub, databases, file systems, and more.
          </p>
        )}

        {[...allNames].map((name) => {
          const cfg = configServers[name];
          const status = servers.find((s) => s.name === name);
          const connected = status?.connected ?? false;
          const isEnabled = (cfg as Record<string, unknown> | undefined)?.enabled !== false;

          return (
            <div key={name} className="mcp-server-card" style={{ opacity: isEnabled ? 1 : 0.5 }}>
              <div className="mcp-server-card__row">
                <div className="mcp-server-card__info">
                  <strong className="mcp-server-card__name">{name}</strong>
                  <span className={`mcp-status-dot mcp-status-dot--${connected ? "ok" : isEnabled ? "off" : "dim"}`} />
                  <span className="mcp-server-card__label">
                    {connected ? "Connected" : isEnabled ? "Not connected" : "Disabled"}
                  </span>
                  <span className="mcp-server-card__transport">
                    {(cfg as Record<string, unknown> | undefined)?.transport === "stdio" ? "Local" : "Remote"}
                  </span>
                </div>
                <div className="mcp-server-card__actions">
                  {connected && (
                    <button
                      className="settings-btn settings-btn--ghost"
                      disabled={reconnecting === name}
                      onClick={() => void handleReconnect(name)}
                    >
                      {reconnecting === name ? "…" : "Reconnect"}
                    </button>
                  )}
                  <button
                    className="settings-btn settings-btn--ghost"
                    onClick={() => void toggleServer(name, !isEnabled)}
                  >
                    {isEnabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    className="settings-btn settings-btn--ghost settings-btn--danger"
                    onClick={() => setConfirmDelete(name)}
                  >
                    Remove
                  </button>
                </div>
              </div>
              {(status?.tool_count ?? 0) > 0 && (
                <div className="mcp-server-card__tools">
                  {status!.tools!.slice(0, 8).join(", ")}
                  {status!.tools!.length > 8 ? `… +${status!.tools!.length - 8} more` : ""}
                </div>
              )}
            </div>
          );
        })}
      </SettingsSection>

      {/* ── Add Server Wizard ────────────────────────────────────────── */}
      {wizardOpen && (
        <div className="modal-backdrop" onClick={() => setWizardOpen(false)}>
          <div
            className="modal-dialog"
            style={{ maxWidth: "560px" }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => { if (e.key === "Escape") setWizardOpen(false); }}
          >
            <div className="modal-title">Add MCP Server</div>

            {/* Paste box — always visible */}
            <div className="s-field">
              <label className="s-field__label">Paste server config</label>
              <p className="s-field__hint">
                Paste the JSON block from the server's docs, or just the URL or command.
              </p>
              <textarea
                className="settings-input"
                style={{ fontFamily: "monospace", fontSize: "12px", minHeight: "80px", resize: "vertical" }}
                value={pasteText}
                placeholder={'Paste here — e.g.:\n{\n  "mcpServers": {\n    "github": {\n      "command": "npx",\n      "args": ["-y", "@modelcontextprotocol/server-github"],\n      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "..." }\n    }\n  }\n}\n\nOr just: https://mcp.example.com/mcp\nOr: npx -y @modelcontextprotocol/server-filesystem /tmp'}
                autoFocus
                onChange={(e) => handlePaste(e.target.value)}
              />
            </div>

            {/* Parsed result */}
            {parsed.length > 0 && currentServer && (
              <>
                <div className="mcp-parsed-summary">
                  <span className="mcp-parsed-badge">
                    {parsed.length > 1 ? `${parsed.length} servers detected` : "Detected"}
                  </span>
                  {" "}
                  <strong>{currentServer.name}</strong>
                  {" — "}
                  {currentServer.transport === "stdio"
                    ? `Local (${currentServer.command.slice(0, 2).join(" ")})`
                    : `Remote (${currentServer.url})`}
                </div>

                {/* Credential inputs — env vars */}
                {currentMissing.length > 0 && (
                  <div style={{ marginTop: "0.5rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                    <p className="s-field__hint" style={{ margin: 0 }}>
                      This server needs the following credentials:
                    </p>
                    {currentMissing.map((envVar) => (
                      <div key={envVar} className="s-field">
                        <label className="s-field__label" style={{ fontSize: "12px" }}>
                          {envVar}
                        </label>
                        <input
                          type="password"
                          className="settings-input"
                          value={creds[credKey(testIdx, envVar)] ?? ""}
                          autoComplete="new-password"
                          spellCheck={false}
                          placeholder="Enter value…"
                          onChange={(e) => updateCred(credKey(testIdx, envVar), e.target.value)}
                        />
                      </div>
                    ))}
                  </div>
                )}

                {/* Already-stored env creds */}
                {currentExistingEnv.length > 0 && (
                  <div style={{ marginTop: "0.25rem", display: "flex", flexWrap: "wrap", gap: "0.25rem 0.75rem" }}>
                    {currentExistingEnv.map((envVar) => (
                      <span key={envVar} className="s-field__hint" style={{ margin: 0 }}>
                        {envVar}: <em>already stored</em>
                      </span>
                    ))}
                  </div>
                )}

                {/* Credential inputs — URL query params */}
                {currentUrlMissing.length > 0 && (
                  <div style={{ marginTop: "0.5rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                    <p className="s-field__hint" style={{ margin: 0 }}>
                      {currentMissing.length > 0
                        ? "And credentials detected in the URL:"
                        : "This server needs credentials detected in the URL:"}
                    </p>
                    {currentUrlMissing.map((um) => (
                      <div key={um.credName} className="s-field">
                        <label className="s-field__label" style={{ fontSize: "12px" }}>
                          {um.param}
                          <span style={{ opacity: 0.5, marginLeft: "0.5rem" }}>
                            → saved as {um.credName}
                          </span>
                        </label>
                        <input
                          type="password"
                          className="settings-input"
                          value={creds[credKey(testIdx, um.credName)] ?? ""}
                          autoComplete="new-password"
                          spellCheck={false}
                          placeholder="Enter secret…"
                          onChange={(e) => updateCred(credKey(testIdx, um.credName), e.target.value)}
                        />
                      </div>
                    ))}
                  </div>
                )}

                {/* Already-stored URL creds */}
                {currentExistingUrl.length > 0 && (
                  <div style={{ marginTop: "0.25rem", display: "flex", flexWrap: "wrap", gap: "0.25rem 0.75rem" }}>
                    {currentExistingUrl.map((um) => (
                      <span key={um.credName} className="s-field__hint" style={{ margin: 0 }}>
                        {um.param}: <em>already stored</em>
                      </span>
                    ))}
                  </div>
                )}

                {/* Test result */}
                {testResult && (
                  <div className={`mcp-test-result mcp-test-result--${testResult.ok ? "ok" : "bad"}`}>
                    {testResult.ok
                      ? `Connected! ${testResult.tool_count} tool${testResult.tool_count !== 1 ? "s" : ""} found.`
                      : `Connection failed: ${testResult.error}`}
                  </div>
                )}

                {/* Actions */}
                <div className="modal-actions" style={{ marginTop: "0.75rem" }}>
                  <button className="modal-btn" onClick={() => setWizardOpen(false)}>
                    Cancel
                  </button>
                  {phase !== "saving" && (
                    <button
                      className="modal-btn"
                      disabled={phase === "testing" || (hasAnyCreds && !allCredsFilled)}
                      onClick={() => void handleTest()}
                    >
                      {phase === "testing" ? "Testing…" : "Test connection"}
                    </button>
                  )}
                  {(phase === "tested" || (phase === "parsed" && !hasAnyCreds)) && (
                    <button
                      className="modal-btn modal-btn--primary"
                      onClick={() => void handleSave()}
                    >
                      Save
                    </button>
                  )}
                </div>
              </>
            )}

            {parsed.length === 0 && pasteText.trim() && (
              <p className="s-field__hint" style={{ marginTop: "0.5rem" }}>
                Couldn't parse that. Try pasting the JSON config from the MCP server's documentation, a URL, or an npx command.
              </p>
            )}
          </div>
        </div>
      )}

      {/* ── Delete confirm ───────────────────────────────────────────── */}
      {confirmDelete && (
        <Modal
          kind="confirm"
          danger
          title={`Remove "${confirmDelete}"?`}
          message="This removes the server from your config. You can always add it again later."
          confirmLabel="Remove"
          onCancel={() => setConfirmDelete(null)}
          onSubmit={() => void deleteServer(confirmDelete)}
        />
      )}
    </>
  );
}
