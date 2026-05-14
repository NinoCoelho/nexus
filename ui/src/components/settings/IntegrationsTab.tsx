/**
 * IntegrationsTab — MCP server connections in Settings.
 *
 * Shows connected MCP servers, their tools, and reconnect/refresh actions.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  listMcpServers,
  reconnectMcpServer,
  refreshMcpTools,
  type McpServerStatus,
} from "../../api/mcp";
import SettingsSection from "./SettingsSection";

export default function IntegrationsTab() {
  const { t } = useTranslation("settings");
  const [servers, setServers] = useState<McpServerStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState<string | null>(null);

  const fetchServers = useCallback(async () => {
    try {
      const list = await listMcpServers();
      setServers(list);
      setError(null);
    } catch {
      setServers([]);
      setError(t("integrations.loadFailed", { defaultValue: "Failed to load MCP servers" }));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    fetchServers();
  }, [fetchServers]);

  const handleReconnect = async (name: string) => {
    setReconnecting(name);
    try {
      await reconnectMcpServer(name);
      await fetchServers();
    } catch {
      // swallow — the UI will show stale state
    } finally {
      setReconnecting(null);
    }
  };

  const handleRefresh = async () => {
    try {
      await refreshMcpTools();
      await fetchServers();
    } catch {
      // swallow
    }
  };

  if (loading) return <p className="settings-loading">Loading…</p>;

  return (
    <SettingsSection
      title={t("integrations.title", { defaultValue: "MCP Servers" })}
      description={t("integrations.description", {
        defaultValue:
          "External tool servers connected via the Model Context Protocol. Configure servers in ~/.nexus/config.toml under [mcp.servers].",
      })}
    >
      {error && <p className="settings-error">{error}</p>}

      {servers.length === 0 && !error && (
        <p className="s-hint">
          {t("integrations.empty", {
            defaultValue: "No MCP servers configured. Add servers to your config.toml.",
          })}
        </p>
      )}

      {servers.map((s) => (
        <div key={s.name} className="s-card" style={{ marginBottom: "0.5rem" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div>
              <strong>{s.name}</strong>
              <span
                style={{
                  marginLeft: "0.5rem",
                  padding: "0.1rem 0.4rem",
                  borderRadius: "4px",
                  fontSize: "0.75rem",
                  background: s.connected ? "var(--color-success-bg, #e6f9e6)" : "var(--color-error-bg, #fde8e8)",
                  color: s.connected ? "var(--color-success, #1a7f1a)" : "var(--color-error, #c53030)",
                }}
              >
                {s.connected
                  ? t("integrations.connected", { defaultValue: "Connected" })
                  : t("integrations.disconnected", { defaultValue: "Disconnected" })}
              </span>
              {s.tool_count != null && s.tool_count > 0 && (
                <span style={{ marginLeft: "0.5rem", color: "var(--color-muted, #888)", fontSize: "0.8rem" }}>
                  {s.tool_count} tool{s.tool_count !== 1 ? "s" : ""}
                </span>
              )}
            </div>
            <button
              className="s-btn s-btn--sm"
              disabled={!s.connected || reconnecting === s.name}
              onClick={() => handleReconnect(s.name)}
            >
              {reconnecting === s.name
                ? t("integrations.reconnecting", { defaultValue: "Reconnecting…" })
                : t("integrations.reconnect", { defaultValue: "Reconnect" })}
            </button>
          </div>
          {s.tools && s.tools.length > 0 && (
            <div style={{ marginTop: "0.4rem", fontSize: "0.8rem", color: "var(--color-muted, #888)" }}>
              {s.tools.join(", ")}
            </div>
          )}
        </div>
      ))}

      {servers.length > 0 && (
        <button
          className="s-btn"
          style={{ marginTop: "0.5rem" }}
          onClick={handleRefresh}
        >
          {t("integrations.refreshAll", { defaultValue: "Refresh all tools" })}
        </button>
      )}
    </SettingsSection>
  );
}
