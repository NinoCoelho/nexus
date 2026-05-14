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
  testMcpServer,
  type McpServerStatus,
  type McpTestResult,
} from "../../api/mcp";
import { getConfig, patchConfig, type McpConfig } from "../../api/config";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import SettingsSection from "./SettingsSection";

// ── Smart parser ──────────────────────────────────────────────────────────

interface ParsedServer {
  name: string;
  transport: "stdio" | "sse" | "streamable-http";
  command: string[];
  url: string;
  env: Record<string, string>;
  headers: Record<string, string>;
  /** Env vars whose values look like placeholders (empty, <YOUR_TOKEN>, etc.) */
  missing: string[];
}

const PLACEHOLDER_RE = /<[^>]+>|YOUR_|PLACEHOLDER|_HERE$|insert_|\[.*\]|^$/i;

function serverNameFromCommand(cmd: string[]): string {
  const joined = cmd.join(" ");
  const m = joined.match(/@modelcontextprotocol\/server-([a-z0-9-]+)/);
  if (m) return m[1];
  const m2 = joined.match(/@[^/]+\/server-([a-z0-9-]+)/);
  if (m2) return m2[1];
  const m3 = joined.match(/\/([a-z0-9-]+)(?:\s|$)/);
  if (m3) return m3[1];
  return "server";
}

function serverNameFromUrl(url: string): string {
  try {
    const u = new URL(url);
    const parts = u.hostname.split(".");
    if (parts[0] === "mcp" || parts[0] === "api") return parts[1] || parts[0];
    return parts[0];
  } catch {
    return "server";
  }
}

function parseEnvBlock(env: Record<string, string>): { env: Record<string, string>; missing: string[] } {
  const missing: string[] = [];
  const resolved: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) {
    if (!v || PLACEHOLDER_RE.test(v)) {
      missing.push(k);
      resolved[k] = "";
    } else {
      resolved[k] = v;
    }
  }
  return { env: resolved, missing };
}

export function parseMcpConfig(input: string): ParsedServer[] {
  const trimmed = input.trim();

  // 1. Try as JSON
  try {
    const obj = JSON.parse(trimmed);
    return parseJsonObj(obj);
  } catch {
    // not JSON, try other formats
  }

  // 2. Bare URL
  if (/^https?:\/\//i.test(trimmed)) {
    return [{
      name: serverNameFromUrl(trimmed),
      transport: "streamable-http",
      command: [],
      url: trimmed,
      env: {},
      headers: {},
      missing: [],
    }];
  }

  // 3. npx/uvx/docker command string
  if (/^(npx|uvx|docker|node|python|bun)\s/i.test(trimmed)) {
    const parts = trimmed.split(/\s+/);
    return [{
      name: serverNameFromCommand(parts),
      transport: "stdio",
      command: parts,
      url: "",
      env: {},
      headers: {},
      missing: [],
    }];
  }

  // 4. Give up
  return [];
}

function parseJsonObj(obj: Record<string, unknown>): ParsedServer[] {
  const results: ParsedServer[] = [];

  // Unwrap common wrappers: { mcpServers: ... } or { servers: ... } or { mcp: { servers: ... } }
  let serversObj: Record<string, unknown> | undefined;
  if (obj.mcpServers && typeof obj.mcpServers === "object") {
    serversObj = obj.mcpServers as Record<string, unknown>;
  } else if (obj.servers && typeof obj.servers === "object") {
    serversObj = obj.servers as Record<string, unknown>;
  } else if (obj.mcp && typeof obj.mcp === "object" && (obj.mcp as Record<string, unknown>).servers) {
    serversObj = (obj.mcp as Record<string, unknown>).servers as Record<string, unknown>;
  }

  if (serversObj) {
    for (const [name, val] of Object.entries(serversObj)) {
      if (val && typeof val === "object") {
        results.push(parseSingleServer(name, val as Record<string, unknown>));
      }
    }
    return results;
  }

  // Might be a single server object (has command or url or type)
  if (obj.command || obj.url || obj.type) {
    return [parseSingleServer("server", obj)];
  }

  return results;
}

function parseSingleServer(name: string, obj: Record<string, unknown>): ParsedServer {
  const command = obj.command
    ? [String(obj.command), ...(Array.isArray(obj.args) ? obj.args.map(String) : [])]
    : [];
  const url = String(obj.url || "");
  const rawEnv = (obj.env && typeof obj.env === "object") ? obj.env as Record<string, string> : {};
  const headers = (obj.headers && typeof obj.headers === "object") ? obj.headers as Record<string, string> : {};
  const { env, missing } = parseEnvBlock(rawEnv);

  let transport: ParsedServer["transport"] = "stdio";
  if (obj.type === "http" || obj.type === "sse" || obj.type === "streamable-http") {
    transport = obj.type as ParsedServer["transport"];
  } else if (url && !command.length) {
    transport = "streamable-http";
  }

  const resolvedName = name === "server" && command.length
    ? serverNameFromCommand(command)
    : name === "server" && url
    ? serverNameFromUrl(url)
    : name;

  return { name: resolvedName, transport, command, url, env, headers, missing };
}

// ── Component ─────────────────────────────────────────────────────────────

type Phase = "idle" | "parsed" | "testing" | "tested" | "saving";

export default function IntegrationsTab() {
  const { t } = useTranslation("settings");
  const toast = useToast();

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

  function startWizard() {
    setPasteText("");
    setParsed([]);
    setCreds({});
    setPhase("idle");
    setTestResult(null);
    setTestIdx(0);
    setWizardOpen(true);
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
      // Collect all missing creds across servers
      const allMissing: Record<string, string> = {};
      for (const s of results) {
        for (const k of s.missing) {
          allMissing[k] = creds[k] ?? "";
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

  function credKey(idx: number, envVar: string) {
    return `${idx}:${envVar}`;
  }

  function updateCred(key: string, value: string) {
    setCreds((prev) => ({ ...prev, [key]: value }));
  }

  async function handleTest() {
    setPhase("testing");
    // Test first server only
    const server = parsed[0];
    if (!server) { setPhase("parsed"); return; }

    const testConfig: Record<string, unknown> = {
      transport: server.transport,
      command: server.command,
      url: server.url,
      headers: server.headers,
      env: { ...server.env },
    };
    // Fill in creds
    for (const k of server.missing) {
      (testConfig.env as Record<string, string>)[k] = creds[credKey(0, k)] || "";
    }
    const result = await testMcpServer(testConfig);
    setTestResult(result);
    setPhase("tested");
  }

  async function handleSave() {
    setPhase("saving");
    try {
      const serversPatch: Record<string, Record<string, unknown> | null> = {};
      for (let i = 0; i < parsed.length; i++) {
        const s = parsed[i];
        const env = { ...s.env };
        for (const k of s.missing) {
          env[k] = creds[credKey(i, k)] || "";
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
      toast.success(
        parsed.length === 1
          ? `"${parsed[0].name}" saved. Restart to connect.`
          : `${parsed.length} servers saved. Restart to connect.`,
      );
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
  const currentMissing = currentServer?.missing ?? [];

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

                {/* Credential inputs */}
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
                  {(phase === "parsed" || phase === "testing") && (
                    <button
                      className="modal-btn"
                      disabled={phase === "testing" || currentMissing.some((k) => !creds[credKey(testIdx, k)])}
                      onClick={() => void handleTest()}
                    >
                      {phase === "testing" ? "Testing…" : "Test connection"}
                    </button>
                  )}
                  {(phase === "tested" || (phase === "parsed" && currentMissing.length === 0)) && (
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
