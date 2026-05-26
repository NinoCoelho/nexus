import { useState, useEffect, useRef } from "react";
import type { TriggerConfig, TriggerType, EventType, VaultFolder } from "../../../types/workflow";
import { listEventTypes, listVaultFolders } from "../../../api/workflows";
import { TRIGGER_TYPES, TRIGGER_ICONS } from "./constants";
import { CopyBtn } from "./shared";

export default function TriggerConfigForm({
  trigger,
  onChangeTrigger,
  onDelete,
  onClose,
}: {
  trigger: TriggerConfig;
  onChangeTrigger: (patch: Partial<TriggerConfig>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const [eventTypes, setEventTypes] = useState<EventType[]>([]);
  const [showEventDropdown, setShowEventDropdown] = useState(false);
  const [vaultFolders, setVaultFolders] = useState<VaultFolder[]>([]);
  const [showFolderPicker, setShowFolderPicker] = useState(false);
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

  const webhookUrl = trigger.broker_slug
    ? `https://nexus-broker.dev/wh/${trigger.broker_slug}`
    : null;

  return (
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
            onChange={(e) =>
              onChangeTrigger({ type: e.target.value as TriggerType })
            }
          >
            {TRIGGER_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        {trigger.type === "webhook" && (
          <>
            <div className="wf-field">
              <label>Webhook Token</label>
              <input
                value={trigger.token || ""}
                readOnly
                placeholder="Generated on save"
              />
            </div>
            {webhookUrl && (
              <div className="wf-field">
                <label>Webhook URL</label>
                <div className="wf-webhook-url-row">
                  <code className="wf-webhook-url">{webhookUrl}</code>
                  <CopyBtn text={webhookUrl} />
                </div>
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

        <button className="wf-delete-btn" onClick={onDelete}>
          Remove Trigger
        </button>
      </div>
    </div>
  );
}
