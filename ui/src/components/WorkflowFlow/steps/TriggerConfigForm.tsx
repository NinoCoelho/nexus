import { useState, useEffect, useRef } from "react";
import type { TriggerConfig, TriggerType, EventType, VaultFolder } from "../../../types/workflow";
import { listEventTypes, listVaultFolders, getWebhookUrl } from "../../../api/workflows";
import WebhookManager from "../../WebhookManager";
import { TRIGGER_TYPES, TRIGGER_ICONS } from "./constants";
import { CopyBtn } from "./shared";

export default function TriggerConfigForm({
  trigger,
  onChangeTrigger,
  onDelete,
  onClose,
  wfPath,
}: {
  trigger: TriggerConfig;
  onChangeTrigger: (patch: Partial<TriggerConfig>) => void;
  onDelete: () => void;
  onClose: () => void;
  wfPath?: string;
}) {
  const [eventTypes, setEventTypes] = useState<EventType[]>([]);
  const [showEventDropdown, setShowEventDropdown] = useState(false);
  const [vaultFolders, setVaultFolders] = useState<VaultFolder[]>([]);
  const [showFolderPicker, setShowFolderPicker] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState<string | null>(null);
  const [hasBroker, setHasBroker] = useState(false);
  const [brokerConnected, setBrokerConnected] = useState(false);
  const [signedIn, setSignedIn] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmTypeChange, setConfirmTypeChange] = useState<TriggerType | null>(null);
  const [webhookManagerOpen, setWebhookManagerOpen] = useState(false);
  const eventDropdownRef = useRef<HTMLDivElement>(null);
  const folderPickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (trigger.type === "fs_watch") {
      listVaultFolders().then(setVaultFolders).catch(() => {});
    }
  }, [trigger.type]);

  useEffect(() => {
    listEventTypes().then(setEventTypes).catch(() => {});
  }, []);

  useEffect(() => {
    if (!showEventDropdown && !showFolderPicker) return;
    const handler = (e: MouseEvent) => {
      if (
        showEventDropdown &&
        eventDropdownRef.current &&
        !eventDropdownRef.current.contains(e.target as Node)
      ) {
        setShowEventDropdown(false);
      }
      if (
        showFolderPicker &&
        folderPickerRef.current &&
        !folderPickerRef.current.contains(e.target as Node)
      ) {
        setShowFolderPicker(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showEventDropdown, showFolderPicker]);

  useEffect(() => {
    if (trigger.type !== "webhook" || !trigger.token || !wfPath) {
      setWebhookUrl(null);
      setHasBroker(false);
      return;
    }
    let cancelled = false;
    getWebhookUrl(wfPath)
      .then((res) => {
        if (cancelled) return;
        const hook = res.webhooks.find((w) => w.trigger_id === trigger.id);
        setWebhookUrl(hook?.url ?? null);
        setHasBroker(hook?.has_broker ?? false);
        setBrokerConnected(res.broker_connected ?? false);
        setSignedIn(res.signed_in ?? false);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [trigger.type, trigger.token, trigger.id, wfPath]);

  const hadWebhookBroker = trigger.type === "webhook" && hasBroker;

  function handleTypeChange(newType: TriggerType) {
    if (hadWebhookBroker && newType !== "webhook") {
      setConfirmTypeChange(newType);
      return;
    }
    onChangeTrigger({ type: newType });
  }

  function confirmTypeChangeConfirm() {
    if (confirmTypeChange) {
      onChangeTrigger({ type: confirmTypeChange, broker_id: undefined, broker_slug: undefined });
    }
    setConfirmTypeChange(null);
  }

  function handleDeleteClick() {
    if (hadWebhookBroker) {
      setConfirmDelete(true);
      return;
    }
    onDelete();
  }

  return (
    <>
    <div className="wf-config-panel">
      <div className="wf-config-panel-header">
        <span className="icon">{TRIGGER_ICONS[trigger.type] || "⚡"}</span>
        <span className="title">Trigger Config</span>
        <button className="close-btn" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="wf-config-panel-body">
        <div className="wf-field">
          <label>Type</label>
          <select
            value={trigger.type}
            onChange={(e) => handleTypeChange(e.target.value as TriggerType)}
          >
            {TRIGGER_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        {confirmTypeChange !== null && (
          <div style={{
            padding: "8px 10px",
            border: "1px solid var(--danger, #e53935)",
            borderRadius: 4,
            background: "rgba(229,57,53,0.06)",
            marginBottom: 8,
          }}>
            <div style={{ fontSize: 11, color: "var(--danger, #e53935)", marginBottom: 8 }}>
              This webhook relay will be permanently deleted along with all queued messages.
              External services using this URL will stop working. This cannot be undone.
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={confirmTypeChangeConfirm}
                style={{
                  background: "var(--danger, #e53935)",
                  color: "white",
                  border: "none",
                  borderRadius: 4,
                  padding: "4px 12px",
                  fontSize: 11,
                  cursor: "pointer",
                }}
              >
                Yes, Change
              </button>
              <button
                type="button"
                onClick={() => setConfirmTypeChange(null)}
                style={{
                  background: "none",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  padding: "4px 12px",
                  fontSize: 11,
                  cursor: "pointer",
                  color: "var(--fg-dim)",
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {trigger.type === "webhook" && (
          <>
            {trigger.token && (
              <div className="wf-field">
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <label style={{ flex: 1 }}>Webhook URL</label>
                  <button
                    type="button"
                    onClick={() => setWebhookManagerOpen(true)}
                    title="Manage webhooks"
                    style={{
                      background: "none",
                      border: "1px solid var(--border)",
                      borderRadius: 4,
                      padding: "2px 6px",
                      fontSize: 12,
                      cursor: "pointer",
                      color: "var(--fg-dim)",
                    }}
                  >
                    ...
                  </button>
                </div>
                {webhookUrl ? (
                  <div className="wf-webhook-url-row">
                    <code className="wf-webhook-url">{webhookUrl}</code>
                    <CopyBtn text={webhookUrl} />
                  </div>
                ) : (
                  <div style={{ fontSize: 11, color: "var(--fg-dim)", padding: "4px 0" }}>
                    {!signedIn
                      ? "Sign in to your Nexus account to activate webhooks."
                      : !brokerConnected
                        ? "Webhook relay is being provisioned. The URL will appear here shortly."
                        : "Connecting to broker..."}
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {trigger.type === "schedule" && (
          <div className="wf-field">
            <label>Cron Expression</label>
            <input
              value={trigger.cron || ""}
              onChange={(e) => onChangeTrigger({ cron: e.target.value })}
              placeholder="0 9 * * 1-5"
            />
          </div>
        )}

        {trigger.type === "fs_watch" && (
          <>
            <div className="wf-field">
              <label>Folder Path</label>
              <div style={{ position: "relative" }} ref={folderPickerRef}>
                <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  <input
                    value={trigger.path || ""}
                    onChange={(e) => onChangeTrigger({ path: e.target.value })}
                    placeholder="~/Downloads"
                    style={{ flex: 1 }}
                  />
                  <span className="wf-path-valid">
                    {trigger.path && /^[/~]/.test(trigger.path) ? "✓" : ""}
                  </span>
                  <button
                    className="wf-dropdown-toggle"
                    onClick={() => setShowFolderPicker((v) => !v)}
                    title="Browse vault folders"
                  >
                    📂
                  </button>
                </div>
                {showFolderPicker && vaultFolders.length > 0 && (
                  <div className="wf-dropdown-list">
                    {vaultFolders.map((f) => (
                      <button
                        key={f.path}
                        className="wf-dropdown-item"
                        onClick={() => {
                          onChangeTrigger({ path: f.path });
                          setShowFolderPicker(false);
                        }}
                      >
                        {f.name}
                        <span className="wf-dropdown-item-desc">{f.path}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="wf-field">
              <label>Glob Pattern</label>
              <input
                value={trigger.pattern || "*"}
                onChange={(e) => onChangeTrigger({ pattern: e.target.value })}
                placeholder="*.pdf"
              />
            </div>
            <div className="wf-field">
              <label>Events</label>
              <div className="wf-toggle-group">
                {(["created", "modified", "deleted", "moved"] as const).map(
                  (evt) => {
                    const active = (trigger.events || ["created"]).includes(evt);
                    return (
                      <button
                        key={evt}
                        className={`wf-toggle-chip${active ? " active" : ""}`}
                        onClick={() => {
                          const current = trigger.events || ["created"];
                          const next = active
                            ? current.filter((e) => e !== evt)
                            : [...current, evt];
                          onChangeTrigger({
                            events: next.length > 0 ? next : [evt],
                          });
                        }}
                      >
                        {evt}
                      </button>
                    );
                  },
                )}
              </div>
            </div>
            <div className="wf-field">
              <label>Debounce (ms)</label>
              <input
                type="number"
                min={0}
                value={trigger.debounce_ms ?? 0}
                onChange={(e) =>
                  onChangeTrigger({
                    debounce_ms: parseInt(e.target.value) || 0,
                  })
                }
                placeholder="0"
              />
            </div>
          </>
        )}

        {trigger.type === "event" && (
          <div className="wf-field">
            <label>Event Pattern</label>
            <div style={{ position: "relative" }} ref={eventDropdownRef}>
              <div style={{ display: "flex", gap: 4 }}>
                <input
                  value={trigger.event || ""}
                  onChange={(e) => onChangeTrigger({ event: e.target.value })}
                  placeholder="vault.*"
                  style={{ flex: 1 }}
                />
                <button
                  className="wf-dropdown-toggle"
                  onClick={() => setShowEventDropdown((v) => !v)}
                  title="Browse event types"
                >
                  ▾
                </button>
              </div>
              {showEventDropdown && eventTypes.length > 0 && (
                <div className="wf-dropdown-list">
                  {Object.entries(
                    eventTypes.reduce<Record<string, EventType[]>>(
                      (acc, et) => {
                        (acc[et.category] ??= []).push(et);
                        return acc;
                      },
                      {},
                    ),
                  ).map(([category, items]) => (
                    <div key={category}>
                      <div className="wf-dropdown-group">{category}</div>
                      {items.map((et) => (
                        <button
                          key={et.pattern}
                          className="wf-dropdown-item"
                          onClick={() => {
                            onChangeTrigger({ event: et.pattern });
                            setShowEventDropdown(false);
                          }}
                        >
                          <span className="wf-dropdown-item-pattern">
                            {et.pattern}
                          </span>
                          <span className="wf-dropdown-item-desc">
                            {et.description}
                          </span>
                        </button>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {confirmDelete ? (
          <div style={{
            padding: "8px 10px",
            border: "1px solid var(--danger, #e53935)",
            borderRadius: 4,
            background: "rgba(229,57,53,0.06)",
          }}>
            <div style={{ fontSize: 11, color: "var(--danger, #e53935)", marginBottom: 8 }}>
              This webhook relay will be permanently deleted along with all queued messages.
              External services using this URL will stop working. This cannot be undone.
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={() => { setConfirmDelete(false); onDelete(); }}
                style={{
                  background: "var(--danger, #e53935)",
                  color: "white",
                  border: "none",
                  borderRadius: 4,
                  padding: "4px 12px",
                  fontSize: 11,
                  cursor: "pointer",
                }}
              >
                Yes, Delete
              </button>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                style={{
                  background: "none",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  padding: "4px 12px",
                  fontSize: 11,
                  cursor: "pointer",
                  color: "var(--fg-dim)",
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <button className="wf-delete-btn" onClick={handleDeleteClick}>
            Remove Trigger
          </button>
        )}
      </div>
      </div>
      {webhookManagerOpen && wfPath && (
        <WebhookManager
          onClose={() => setWebhookManagerOpen(false)}
          selectMode={trigger.broker_id ? undefined : { type: "workflow", path: wfPath, trigger_id: trigger.id }}
          onSelect={(result) => {
            setWebhookUrl(result.url);
            setHasBroker(true);
            setBrokerConnected(true);
            onChangeTrigger({
              token: result.token,
              broker_id: result.broker_id,
              broker_slug: result.broker_slug,
            });
            setWebhookManagerOpen(false);
          }}
        />
      )}
    </>
  );
}
