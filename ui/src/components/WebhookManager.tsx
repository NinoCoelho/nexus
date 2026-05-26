import { useEffect, useState } from "react";
import {
  listBrokerWebhooks,
  createBrokerWebhook,
  deleteBrokerWebhook,
  assignBrokerWebhook,
  unassignBrokerWebhook,
  type BrokerWebhook,
  type WebhookListResponse,
} from "../api/broker";

type SelectMode = {
  type: "kanban" | "workflow";
  path: string;
  lane_id?: string;
  trigger_id?: string;
};

interface Props {
  onClose: () => void;
  onSelect?: (result: { url: string; token: string; broker_id: string; broker_slug: string }) => void;
  selectMode?: SelectMode;
}

export default function WebhookManager({ onClose, onSelect, selectMode }: Props) {
  const [data, setData] = useState<WebhookListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [assigning, setAssigning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  function refresh() {
    setLoading(true);
    setError(null);
    listBrokerWebhooks()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => { refresh(); }, []);

  async function handleCreate() {
    setCreating(true);
    setError(null);
    try {
      const wh = await createBrokerWebhook();
      if (selectMode && onSelect) {
        const result = await assignBrokerWebhook(wh.broker_id, selectMode);
        onSelect({
          url: result.url,
          token: result.token,
          broker_id: wh.broker_id,
          broker_slug: wh.broker_slug,
        });
        onClose();
        return;
      }
      refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(brokerId: string) {
    setDeleting(brokerId);
    setError(null);
    try {
      await deleteBrokerWebhook(brokerId);
      setConfirmDelete(null);
      refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setDeleting(null);
    }
  }

  async function handleAssign(wh: BrokerWebhook) {
    if (!selectMode || !onSelect) return;
    setAssigning(wh.broker_id);
    setError(null);
    try {
      const result = await assignBrokerWebhook(wh.broker_id, selectMode);
      onSelect({
        url: result.url,
        token: result.token,
        broker_id: wh.broker_id,
        broker_slug: wh.broker_slug,
      });
      onClose();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAssigning(null);
    }
  }

  async function handleUnassign(wh: BrokerWebhook) {
    setAssigning(wh.broker_id);
    setError(null);
    try {
      await unassignBrokerWebhook(wh.broker_id);
      refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAssigning(null);
    }
  }

  function handleCopy(url: string, id: string) {
    navigator.clipboard.writeText(url).then(() => {
      setCopied(id);
      setTimeout(() => setCopied(null), 2000);
    }).catch(() => {});
  }

  const connected = data?.connected ?? false;
  const signedIn = data?.signed_in ?? false;
  const webhooks = data?.webhooks ?? [];
  const quota = data?.quota;

  const unassigned = webhooks.filter((w) => !w.assigned && w.exists_on_broker && w.is_active);
  const assigned = webhooks.filter((w) => w.assigned);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()} style={{ minWidth: 520, maxWidth: 600, maxHeight: "80vh", display: "flex", flexDirection: "column" }}>
        <div className="modal-title" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span>Webhook Manager</span>
          {quota && (
            <span style={{ fontSize: 11, color: "var(--fg-dim)", fontWeight: 400 }}>
              {quota.used} webhook{quota.used !== 1 ? "s" : ""} used
              {unassigned.length > 0 && ` (${unassigned.length} unassigned)`}
            </span>
          )}
        </div>

        {!signedIn && (
          <div style={{ padding: "20px 0", textAlign: "center" }}>
            <p style={{ fontSize: 13, color: "var(--fg-dim)", margin: "0 0 12px" }}>
              Sign in to your Nexus account to manage webhooks.
            </p>
            <button
              className="modal-btn modal-btn--primary"
              onClick={() => {
                window.dispatchEvent(new CustomEvent("nexus:navigate", { detail: { view: "settings" } }));
                onClose();
              }}
            >
              Go to Settings
            </button>
          </div>
        )}

        {signedIn && !connected && (
          <div style={{ padding: "20px 0", textAlign: "center" }}>
            <p style={{ fontSize: 13, color: "var(--fg-dim)", margin: "0 0 12px" }}>
              Webhook relay is being provisioned. This happens automatically after sign-in.
            </p>
            <button className="modal-btn" onClick={refresh} disabled={loading}>
              {loading ? "Checking..." : "Retry"}
            </button>
          </div>
        )}

        {connected && (
          <div style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
            {error && (
              <div style={{ fontSize: 11, color: "var(--danger, #e53935)", padding: "6px 0" }}>{error}</div>
            )}

            {selectMode && unassigned.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "var(--fg-dim)" }}>
                  Select a webhook
                </div>
                {unassigned.map((wh) => (
                  <div key={wh.broker_id} style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "8px 10px", borderBottom: "1px solid var(--border)",
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 500 }}>{wh.name}</div>
                      <code style={{ fontSize: 10, color: "var(--fg-dim)", wordBreak: "break-all" }}>{wh.url}</code>
                    </div>
                    <button
                      onClick={() => handleAssign(wh)}
                      disabled={assigning === wh.broker_id}
                      style={{
                        background: "var(--accent)", color: "white", border: "none",
                        borderRadius: 4, padding: "4px 12px", fontSize: 11, cursor: "pointer",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {assigning === wh.broker_id ? "..." : "Use"}
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <button
                className="modal-btn modal-btn--primary"
                onClick={handleCreate}
                disabled={creating}
                style={{ fontSize: 12, padding: "6px 14px" }}
              >
                {creating ? "Creating..." : selectMode ? "Create & Use" : "+ Create Webhook"}
              </button>
              <button className="modal-btn" onClick={refresh} disabled={loading} style={{ fontSize: 12, padding: "6px 14px" }}>
                Refresh
              </button>
            </div>

            {assigned.length > 0 && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "var(--fg-dim)" }}>
                  Assigned ({assigned.length})
                </div>
                {assigned.map((wh) => (
                  <WebhookRow
                    key={wh.broker_id}
                    webhook={wh}
                    copied={copied}
                    confirmDelete={confirmDelete}
                    deleting={deleting}
                    assigning={assigning}
                    onCopy={(url, id) => handleCopy(url, id)}
                    onUnassign={() => handleUnassign(wh)}
                    onDelete={() => setConfirmDelete(wh.broker_id)}
                    onConfirmDelete={() => handleDelete(wh.broker_id)}
                    onCancelDelete={() => setConfirmDelete(null)}
                  />
                ))}
              </div>
            )}

            {!selectMode && unassigned.length > 0 && (
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "var(--fg-dim)" }}>
                  Unassigned ({unassigned.length})
                </div>
                {unassigned.map((wh) => (
                  <WebhookRow
                    key={wh.broker_id}
                    webhook={wh}
                    copied={copied}
                    confirmDelete={confirmDelete}
                    deleting={deleting}
                    assigning={assigning}
                    onCopy={(url, id) => handleCopy(url, id)}
                    onUnassign={() => handleUnassign(wh)}
                    onDelete={() => setConfirmDelete(wh.broker_id)}
                    onConfirmDelete={() => handleDelete(wh.broker_id)}
                    onCancelDelete={() => setConfirmDelete(null)}
                  />
                ))}
              </div>
            )}

            {webhooks.length === 0 && !creating && (
              <div style={{ fontSize: 12, color: "var(--fg-dim)", textAlign: "center", padding: 20 }}>
                No webhooks yet. Create one to get started.
              </div>
            )}
          </div>
        )}

        <div className="modal-actions" style={{ marginTop: 12 }}>
          <button className="modal-btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function WebhookRow({
  webhook: wh,
  copied,
  confirmDelete,
  deleting,
  assigning,
  onCopy,
  onUnassign,
  onDelete,
  onConfirmDelete,
  onCancelDelete,
}: {
  webhook: BrokerWebhook;
  copied: string | null;
  confirmDelete: string | null;
  deleting: string | null;
  assigning: string | null;
  onCopy: (url: string, id: string) => void;
  onUnassign: () => void;
  onDelete: () => void;
  onConfirmDelete: () => void;
  onCancelDelete: () => void;
}) {
  const assignmentLabel = wh.assignment
    ? wh.assignment.type === "kanban"
      ? `Kanban: ${wh.assignment.path}${wh.assignment.lane_id ? ` → ${wh.assignment.lane_id}` : ""}`
      : `Workflow: ${wh.assignment.path}${wh.assignment.trigger_id ? ` → ${wh.assignment.trigger_id}` : ""}`
    : null;

  return (
    <div style={{
      padding: "8px 10px",
      borderBottom: "1px solid var(--border)",
      opacity: wh.is_active ? 1 : 0.5,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 500 }}>{wh.name}</span>
        {!wh.exists_on_broker && (
          <span style={{ fontSize: 10, color: "var(--danger, #e53935)", background: "rgba(229,57,53,0.08)", padding: "1px 5px", borderRadius: 3 }}>
            Missing
          </span>
        )}
        {wh.message_count > 0 && (
          <span style={{ fontSize: 10, color: "var(--accent)", background: "rgba(0,120,212,0.08)", padding: "1px 5px", borderRadius: 3 }}>
            {wh.message_count} msg{wh.message_count !== 1 ? "s" : ""}
          </span>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 4 }}>
        <code style={{ fontSize: 10, color: "var(--fg-dim)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {wh.url}
        </code>
        <button
          onClick={() => onCopy(wh.url, wh.broker_id)}
          style={{ background: "none", border: "1px solid var(--border)", borderRadius: 3, padding: "2px 6px", fontSize: 10, cursor: "pointer", color: copied === wh.broker_id ? "var(--ok, #4caf80)" : "var(--fg-dim)" }}
        >
          {copied === wh.broker_id ? "✓" : "Copy"}
        </button>
      </div>
      {assignmentLabel && (
        <div style={{ fontSize: 10, color: "var(--fg-dim)", marginBottom: 4 }}>
          → {assignmentLabel}
        </div>
      )}
      <div style={{ display: "flex", gap: 6 }}>
        {wh.assigned && (
          <button
            onClick={onUnassign}
            disabled={assigning === wh.broker_id}
            style={{ background: "none", border: "1px solid var(--border)", borderRadius: 3, padding: "2px 8px", fontSize: 10, cursor: "pointer", color: "var(--fg-dim)" }}
          >
            {assigning === wh.broker_id ? "..." : "Unassign"}
          </button>
        )}
        {confirmDelete === wh.broker_id ? (
          <>
            <span style={{ fontSize: 10, color: "var(--danger, #e53935)", lineHeight: "20px" }}>Delete permanently?</span>
            <button onClick={onConfirmDelete} disabled={deleting === wh.broker_id} style={{ background: "var(--danger, #e53935)", color: "white", border: "none", borderRadius: 3, padding: "2px 8px", fontSize: 10, cursor: "pointer" }}>
              {deleting === wh.broker_id ? "..." : "Yes"}
            </button>
            <button onClick={onCancelDelete} style={{ background: "none", border: "1px solid var(--border)", borderRadius: 3, padding: "2px 8px", fontSize: 10, cursor: "pointer", color: "var(--fg-dim)" }}>
              No
            </button>
          </>
        ) : (
          <button
            onClick={onDelete}
            style={{ background: "none", border: "1px solid var(--danger, #e53935)", borderRadius: 3, padding: "2px 8px", fontSize: 10, cursor: "pointer", color: "var(--danger, #e53935)" }}
          >
            Delete
          </button>
        )}
      </div>
    </div>
  );
}
