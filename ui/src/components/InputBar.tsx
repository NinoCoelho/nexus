import { useRef, useState } from "react";
import { uploadVaultFiles } from "../api";
import { useToast } from "../toast/ToastProvider";
import "./InputBar.css";

interface AttachedFile {
  name: string;
  vaultPath: string;
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  disabled: boolean;
  busy?: boolean;
  onStop?: () => void;
  attachments?: AttachedFile[];
  onAttachmentsChange?: (files: AttachedFile[]) => void;
  models?: string[];
  selectedModel?: string;
  onModelChange?: (model: string) => void;
}

export default function InputBar({
  value,
  onChange,
  onSend,
  disabled,
  busy,
  onStop,
  attachments,
  onAttachmentsChange,
  models,
  selectedModel,
  onModelChange,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const [uploading, setUploading] = useState(false);

  const adjust = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    try {
      const result = await uploadVaultFiles(Array.from(fileList), "uploads");
      const newAttachments = result.uploaded.map((f) => ({
        name: f.path.split("/").pop() ?? f.path,
        vaultPath: f.path,
      }));
      onAttachmentsChange?.([...(attachments ?? []), ...newAttachments]);
    } catch (err) {
      toast.error("Upload failed", { detail: err instanceof Error ? err.message : undefined });
    } finally {
      setUploading(false);
    }
    e.target.value = "";
  };

  const removeAttachment = (idx: number) => {
    const next = [...(attachments ?? [])];
    next.splice(idx, 1);
    onAttachmentsChange?.(next);
  };

  return (
    <div className="input-bar-wrapper">
      {attachments && attachments.length > 0 && (
        <div className="input-attachments">
          {attachments.map((a, i) => (
            <span key={i} className="input-attachment-chip">
              <svg width="11" height="11" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 2h8l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
                <polyline points="12 2 12 6 16 6" />
              </svg>
              {a.name}
              <button className="input-attachment-remove" onClick={() => removeAttachment(i)} type="button">&times;</button>
            </span>
          ))}
        </div>
      )}
      <div className="input-bar">
        <div className="input-bar-stubs">
          <button
            className="input-stub-btn input-stub-btn--active"
            disabled={disabled || uploading}
            title="Attach file"
            aria-label="Attach file"
            onClick={() => fileInputRef.current?.click()}
          >
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 14v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
              <polyline points="7,8 10,4 13,8" />
              <line x1="10" y1="4" x2="10" y2="14" />
            </svg>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            style={{ display: "none" }}
            onChange={(e) => void handleFileSelect(e)}
          />
          <button className="input-stub-btn" disabled title="Coming soon" aria-label="Microphone">
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <rect x="7" y="2" width="6" height="10" rx="3" />
              <path d="M4 10a6 6 0 0 0 12 0" />
              <line x1="10" y1="16" x2="10" y2="19" />
              <line x1="7" y1="19" x2="13" y2="19" />
            </svg>
          </button>
        </div>
        <textarea
          ref={textareaRef}
          className="input-textarea"
          rows={1}
          placeholder="Message Nexus…"
          value={value}
          onChange={(e) => { onChange(e.target.value); adjust(); }}
          onKeyDown={handleKeyDown}
          disabled={disabled}
        />
        {models && models.length >= 1 && (
          <select
            className="input-model-select"
            value={selectedModel ?? ""}
            onChange={(e) => onModelChange?.(e.target.value)}
            disabled={busy}
          >
            <option value="">Auto</option>
            {models.map((m) => (
              <option key={m} value={m}>{m.split("/").pop()}</option>
            ))}
          </select>
        )}
        {busy && onStop ? (
          <button
            className="input-send-btn input-stop-btn"
            onClick={onStop}
            aria-label="Stop"
            title="Stop the agent"
          >
            <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor" stroke="none">
              <rect x="5" y="5" width="10" height="10" rx="1.5" />
            </svg>
          </button>
        ) : (
          <button
            className="input-send-btn"
            onClick={onSend}
            disabled={disabled || (!value.trim() && (!attachments || attachments.length === 0))}
            aria-label="Send"
          >
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <line x1="10" y1="17" x2="10" y2="4" />
              <polyline points="4,10 10,4 16,10" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}
