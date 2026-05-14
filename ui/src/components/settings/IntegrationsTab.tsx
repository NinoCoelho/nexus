/**
 * IntegrationsTab — MCP server management with add wizard.
 *
 * Shows configured MCP servers with connection status, and provides a
 * step-by-step wizard to add new servers without editing config.toml.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  listMcpServers,
  reconnectMcpServer,
  refreshMcpTools,
  type McpServerStatus,
} from "../../api/mcp";
import {
  getConfig,
  patchConfig,
  type McpServerConfig,
  type McpConfig,
} from "../../api/config";
import Modal from "../Modal";
import { useToast } from "../../toast/ToastProvider";
import SettingsSection from "./SettingsSection";

type WizardStep = "choose-type" | "enter-details" | "confirm";

interface WizardState {
  step: WizardStep;
  name: string;
  transport: "stdio" | "sse" | "streamable-http";
  url: string;
  command: string;
  env: Record<string, string>;
  enabled: boolean;
}

const INITIAL_WIZARD: WizardState = {
  step: "choose-type",
  name: "",
  transport: "streamable-http",
  url: "",
  command: "",
  env: {},
  enabled: true,
};

const POPULAR_SERVERS: { label: string; transport: "stdio" | "streamable-http"; command?: string; url?: string }[] = [
  { label: "Filesystem", transport: "stdio", command: "npx -y @modelcontextprotocol/server-filesystem" },
  { label: "GitHub", transport: "stdio", command: "npx -y @modelcontextprotocol/server-github" },
  { label: "Postgres", transport: "stdio", command: "npx -y @modelcontextprotocol/server-postgres" },
  { label: "Brave Search", transport: "stdio", command: "npx -y @modelcontextprotocol/server-brave-search" },
  { label: "Memory", transport: "stdio", command: "npx -y @modelcontextprotocol/server-memory" },
  { label: "Custom URL…", transport: "streamable-http" },
  { label: "Custom command…", transport: "stdio" },
];

export default function IntegrationsTab() {
  const { t } = useTranslation("settings");
  const toast = useToast();

  const [servers, setServers] = useState<McpServerStatus[]>([]);
  const [configServers, setConfigServers] = useState<Record<string, McpServerConfig>>({});
  const [loading, setLoading] = useState(true);
  const [reconnecting, setReconnecting] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Wizard state
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizard, setWizard] = useState<WizardState>({ ...INITIAL_WIZARD });

  const refresh = useCallback(async () => {
    try {
      const [statusList, cfg] = await Promise.all([
        listMcpServers().catch(() => [] as McpServerStatus[]),
        getConfig(),
      ]);
      setServers(statusList);
      setConfigServers((cfg.mcp as McpConfig | undefined)?.servers ?? {});
    } catch {
      setServers([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // ── CRUD ──────────────────────────────────────────────────────────────

  async function saveServer(name: string, config: McpServerConfig) {
    try {
      await patchConfig({
        mcp: { servers: { [name]: config } },
      });
      toast.success(`MCP server "${name}" saved. Restart the server to apply.`);
      setWizardOpen(false);
      await refresh();
    } catch (e) {
      toast.error("Failed to save server", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function deleteServer(name: string) {
    setConfirmDelete(null);
    try {
      await patchConfig({ mcp: { servers: { [name]: null } } });
      toast.success(`MCP server "${name}" removed. Restart the server to apply.`);
      await refresh();
    } catch (e) {
      toast.error("Failed to delete server", {
        detail: e instanceof Error ? e.message : undefined,
      });
    }
  }

  async function toggleServer(name: string, enabled: boolean) {
    try {
      await patchConfig({ mcp: { servers: { [name]: { enabled } } } });
      toast.success(enabled ? `"${name}" enabled` : `"${name}" disabled`);
      await refresh();
    } catch (e) {
      toast.error("Failed to update server", {
        detail: e instanceof Error ? e.message : undefined,
      });
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

  async function handleRefresh() {
    try {
      await refreshMcpTools();
      await refresh();
    } catch {
      // swallow
    }
  }

  // ── Wizard handlers ───────────────────────────────────────────────────

  function startWizard() {
    setWizard({ ...INITIAL_WIZARD });
    setWizardOpen(true);
  }

  function pickTemplate(tpl: (typeof POPULAR_SERVERS)[number]) {
    setWizard({
      ...wizard,
      step: "enter-details",
      transport: tpl.transport,
      command: tpl.command ?? "",
      url: tpl.url ?? "",
      name: wizard.name || tpl.label.toLowerCase().replace(/[^a-z0-9]/g, "-"),
    });
  }

  function wizardNext() {
    if (wizard.step === "enter-details") {
      setWizard({ ...wizard, step: "confirm" });
    }
  }

  function wizardBack() {
    if (wizard.step === "confirm") {
      setWizard({ ...wizard, step: "enter-details" });
    } else {
      setWizard({ ...wizard, step: "choose-type" });
    }
  }

  function wizardSave() {
    const parts = wizard.command.trim().split(/\s+/);
    saveServer(wizard.name, {
      transport: wizard.transport,
      command: wizard.transport === "stdio" ? parts : [],
      url: wizard.transport !== "stdio" ? wizard.url : "",
      env: {},
      headers: {},
      enabled: wizard.enabled,
    });
  }

  // ── Render ────────────────────────────────────────────────────────────

  if (loading) return <p className="settings-loading">Loading…</p>;

  const allNames = new Set([...Object.keys(configServers), ...servers.map((s) => s.name)]);

  return (
    <>
      <SettingsSection
        title={t("integrations.title", { defaultValue: "MCP Servers" })}
        description={t("integrations.description", {
          defaultValue:
            "Connect external tool servers via the Model Context Protocol. This lets your agent use tools from services like GitHub, file systems, databases, and more.",
        })}
      >
        <button
          type="button"
          className="settings-btn settings-btn--primary"
          style={{ marginBottom: "0.75rem" }}
          onClick={startWizard}
        >
          + Add server
        </button>

        {allNames.size === 0 && (
          <p className="s-field__hint">
            No MCP servers configured yet. Click "Add server" to connect external tools and data sources to your agent.
          </p>
        )}

        {[...allNames].map((name) => {
          const cfg = configServers[name];
          const status = servers.find((s) => s.name === name);
          const connected = status?.connected ?? false;
          const isEnabled = cfg?.enabled ?? true;

          return (
            <div
              key={name}
              className="s-card"
              style={{ marginBottom: "0.5rem", opacity: isEnabled ? 1 : 0.5 }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.5rem" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                  <strong>{name}</strong>
                  <span
                    style={{
                      padding: "0.1rem 0.4rem",
                      borderRadius: "4px",
                      fontSize: "0.7rem",
                      background: connected ? "var(--color-success-bg, #e6f9e6)" : "var(--color-error-bg, #fde8e8)",
                      color: connected ? "var(--color-success, #1a7f1a)" : "var(--color-error, #c53030)",
                    }}
                  >
                    {connected ? "Connected" : isEnabled ? "Disconnected" : "Disabled"}
                  </span>
                  <span style={{ fontSize: "0.75rem", color: "var(--color-muted, #888)" }}>
                    {cfg?.transport === "stdio" ? "Local" : "Remote"}
                  </span>
                  {status?.tool_count != null && status.tool_count > 0 && (
                    <span style={{ fontSize: "0.75rem", color: "var(--color-muted, #888)" }}>
                      {status.tool_count} tool{status.tool_count !== 1 ? "s" : ""}
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", gap: "0.35rem", flexShrink: 0 }}>
                  {connected && (
                    <button
                      className="s-btn s-btn--sm"
                      disabled={reconnecting === name}
                      onClick={() => handleReconnect(name)}
                    >
                      {reconnecting === name ? "…" : "Reconnect"}
                    </button>
                  )}
                  <button
                    className="s-btn s-btn--sm"
                    onClick={() => toggleServer(name, !isEnabled)}
                  >
                    {isEnabled ? "Disable" : "Enable"}
                  </button>
                  <button
                    className="s-btn s-btn--sm"
                    style={{ color: "var(--color-error, #c53030)" }}
                    onClick={() => setConfirmDelete(name)}
                  >
                    Remove
                  </button>
                </div>
              </div>
              {cfg?.transport === "stdio" && cfg.command?.length > 0 && (
                <div style={{ marginTop: "0.3rem", fontSize: "0.75rem", color: "var(--color-muted, #888)", fontFamily: "monospace" }}>
                  {cfg.command.join(" ")}
                </div>
              )}
              {cfg?.transport !== "stdio" && cfg?.url && (
                <div style={{ marginTop: "0.3rem", fontSize: "0.75rem", color: "var(--color-muted, #888)", fontFamily: "monospace" }}>
                  {cfg.url}
                </div>
              )}
              {status?.tools && status.tools.length > 0 && (
                <div style={{ marginTop: "0.3rem", fontSize: "0.7rem", color: "var(--color-muted, #999)" }}>
                  {status.tools.join(", ")}
                </div>
              )}
            </div>
          );
        })}

        {allNames.size > 0 && (
          <button className="s-btn" style={{ marginTop: "0.5rem" }} onClick={() => void handleRefresh()}>
            Refresh all tools
          </button>
        )}

        <p className="s-field__hint" style={{ marginTop: "0.75rem" }}>
          Changes are saved to your config and take effect after restarting the server.
        </p>
      </SettingsSection>

      {/* ── Add Server Wizard ──────────────────────────────────────────── */}
      {wizardOpen && (
        <div className="modal-backdrop" onClick={() => setWizardOpen(false)}>
          <div
            className="modal-dialog"
            style={{ maxWidth: "520px" }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") setWizardOpen(false);
            }}
          >
            <div className="modal-title">
              {wizard.step === "choose-type" && "Add MCP Server"}
              {wizard.step === "enter-details" && "Configure Server"}
              {wizard.step === "confirm" && "Confirm & Save"}
            </div>

            {/* Step 1: Choose type */}
            {wizard.step === "choose-type" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                <p className="s-field__hint" style={{ marginBottom: "0.5rem" }}>
                  Choose a server type or start from scratch. MCP servers give your agent access to external tools and data.
                </p>
                {POPULAR_SERVERS.map((tpl) => (
                  <button
                    key={tpl.label}
                    className="s-btn"
                    style={{ textAlign: "left", width: "100%", justifyContent: "flex-start" }}
                    onClick={() => pickTemplate(tpl)}
                  >
                    {tpl.label}
                  </button>
                ))}
              </div>
            )}

            {/* Step 2: Enter details */}
            {wizard.step === "enter-details" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                <div className="s-field">
                  <label className="s-field__label">Server name</label>
                  <p className="s-field__hint">A short name to identify this server (lowercase, no spaces).</p>
                  <input
                    type="text"
                    className="settings-input"
                    value={wizard.name}
                    placeholder="my-server"
                    autoFocus
                    onChange={(e) => setWizard({ ...wizard, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })}
                  />
                </div>

                {wizard.transport === "stdio" ? (
                  <div className="s-field">
                    <label className="s-field__label">Command</label>
                    <p className="s-field__hint">
                      The command to start the MCP server. This usually looks like{" "}
                      <code>npx -y @modelcontextprotocol/server-*</code>.
                    </p>
                    <input
                      type="text"
                      className="settings-input"
                      value={wizard.command}
                      placeholder="npx -y @modelcontextprotocol/server-filesystem /path/to/dir"
                      onChange={(e) => setWizard({ ...wizard, command: e.target.value })}
                    />
                  </div>
                ) : (
                  <div className="s-field">
                    <label className="s-field__label">Server URL</label>
                    <p className="s-field__hint">The HTTP URL of the remote MCP server.</p>
                    <input
                      type="url"
                      className="settings-input"
                      value={wizard.url}
                      placeholder="http://localhost:3000/mcp"
                      onChange={(e) => setWizard({ ...wizard, url: e.target.value })}
                    />
                  </div>
                )}

                <div className="s-field">
                  <label className="s-field__label">Type</label>
                  <select
                    className="settings-input"
                    value={wizard.transport}
                    onChange={(e) => setWizard({ ...wizard, transport: e.target.value as WizardState["transport"] })}
                  >
                    <option value="stdio">Local (command)</option>
                    <option value="streamable-http">Remote (URL)</option>
                    <option value="sse">Remote (SSE)</option>
                  </select>
                </div>
              </div>
            )}

            {/* Step 3: Confirm */}
            {wizard.step === "confirm" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                <p style={{ fontSize: "0.85rem" }}>
                  This will add a new MCP server with the following settings:
                </p>
                <div
                  style={{
                    background: "var(--color-bg-subtle, #f5f5f5)",
                    borderRadius: "6px",
                    padding: "0.75rem",
                    fontSize: "0.85rem",
                    fontFamily: "monospace",
                  }}
                >
                  <div><strong>Name:</strong> {wizard.name}</div>
                  <div><strong>Transport:</strong> {wizard.transport}</div>
                  {wizard.transport === "stdio" ? (
                    <div><strong>Command:</strong> {wizard.command}</div>
                  ) : (
                    <div><strong>URL:</strong> {wizard.url}</div>
                  )}
                </div>
                <p className="s-field__hint">
                  You may need to set environment variables (API keys) for some servers. Add them in the Credentials tab if needed.
                </p>
              </div>
            )}

            <div className="modal-actions" style={{ marginTop: "1rem" }}>
              {wizard.step !== "choose-type" && (
                <button className="modal-btn" onClick={wizardBack}>
                  Back
                </button>
              )}
              <button className="modal-btn" onClick={() => setWizardOpen(false)}>
                Cancel
              </button>
              {wizard.step === "enter-details" && (
                <button
                  className="modal-btn modal-btn--primary"
                  disabled={!wizard.name || (wizard.transport === "stdio" ? !wizard.command : !wizard.url)}
                  onClick={wizardNext}
                >
                  Next
                </button>
              )}
              {wizard.step === "confirm" && (
                <button
                  className="modal-btn modal-btn--primary"
                  disabled={!wizard.name}
                  onClick={wizardSave}
                >
                  Save
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Delete Confirm ─────────────────────────────────────────────── */}
      {confirmDelete && (
        <Modal
          kind="confirm"
          danger
          title={`Remove "${confirmDelete}"?`}
          message="This will remove the MCP server from your configuration. You can always add it again later."
          confirmLabel="Remove"
          onCancel={() => setConfirmDelete(null)}
          onSubmit={() => void deleteServer(confirmDelete)}
        />
      )}
    </>
  );
}
