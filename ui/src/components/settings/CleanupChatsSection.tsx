/**
 * CleanupChatsSection — Settings → Features card for the global chat
 * cleanup. Dry-run preview via cleanupAllBots shows how many chats would
 * be archived (one active chat kept per bot); applying it asks for
 * confirmation through the standard Modal.
 */

import { useState } from "react";
import { cleanupAllBots } from "../../api";
import Modal, { type ModalProps } from "../Modal";
import { useToast } from "../../toast/ToastProvider";

export default function CleanupChatsSection() {
  const toast = useToast();
  const [previewCount, setPreviewCount] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ModalProps | null>(null);

  const runPreview = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const result = await cleanupAllBots({ keep: 1, include_unprojected: true, dry_run: true });
      setPreviewCount(result.count);
      if (result.count === 0) toast.info("Nothing to clean up — every bot already has a single active chat.");
    } catch (e) {
      toast.error("Cleanup preview failed", { detail: e instanceof Error ? e.message : undefined });
    } finally {
      setBusy(false);
    }
  };

  const apply = () => {
    setConfirm({
      kind: "confirm",
      title: "Cleanup old chats",
      message: `Archive ${previewCount} ${previewCount === 1 ? "chat" : "chats"}? Each bot keeps its most recent chat; the coordinator master chat always stays. Archived chats remain restorable from each bot's workspace.`,
      confirmLabel: "Archive",
      onCancel: () => setConfirm(null),
      onSubmit: () => {
        setConfirm(null);
        void (async () => {
          setBusy(true);
          try {
            const result = await cleanupAllBots({ keep: 1, include_unprojected: true });
            toast.success(`Archived ${result.count} ${result.count === 1 ? "chat" : "chats"}`);
            setPreviewCount(null);
          } catch (e) {
            toast.error("Cleanup failed", { detail: e instanceof Error ? e.message : undefined });
          } finally {
            setBusy(false);
          }
        })();
      },
    });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <p style={{ margin: 0, fontSize: 12, color: "var(--fg-faint)" }}>
        Keeps every bot to its most recent chat and tidies unprojected ones —
        archived chats stay restorable from each bot&apos;s workspace.
      </p>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="settings-btn" disabled={busy} onClick={() => void runPreview()}>
          {busy ? "Working…" : "Preview"}
        </button>
        {previewCount != null && previewCount > 0 && (
          <>
            <span style={{ fontSize: 12, color: "var(--fg-dim)" }}>
              {previewCount} {previewCount === 1 ? "chat" : "chats"} would be archived
            </span>
            <button className="settings-btn settings-btn--primary" disabled={busy} onClick={apply}>
              Clean up
            </button>
          </>
        )}
      </div>
      {confirm && <Modal {...confirm} />}
    </div>
  );
}
