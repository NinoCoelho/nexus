/**
 * InputBar — the bottom input area for the chat view.
 *
 * Features:
 *   - Multi-line textarea (Shift+Enter for newline, Enter to send)
 *   - Model selector dropdown (when multiple models are available)
 *   - Vault file attachment via drag-and-drop or the "+" button
 *   - Stop button (replaces send while a turn is in-flight)
 *
 * Attachments are resolved to vault:// URLs before being sent;
 * the agent receives them as markdown links in the user message.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { uploadVaultFiles } from "../api";
import { useToast } from "../toast/ToastProvider";
import "./InputBar.css";

interface AttachedFile {
  name: string;
  vaultPath: string;
}

interface AudioAttachment {
  blob: Blob;
  url: string;
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
  routingMode?: "fixed" | "auto";
  onRoutingModeChange?: (mode: "fixed" | "auto") => void;
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
  routingMode = "fixed",
  onRoutingModeChange,
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const toast = useToast();

  const [uploading, setUploading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [audio, setAudio] = useState<AudioAttachment | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const hasContent = value.trim().length > 0 || (attachments && attachments.length > 0) || !!audio;

  const adjust = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (hasContent && !disabled) onSend();
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
    setMenuOpen(false);
  };

  const removeAttachment = (idx: number) => {
    const next = [...(attachments ?? [])];
    next.splice(idx, 1);
    onAttachmentsChange?.(next);
  };

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        const url = URL.createObjectURL(blob);
        setAudio({ blob, url });
        stream.getTracks().forEach((t) => t.stop());
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      toast.error("Microphone access denied");
    }
  }, [toast]);

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop();
    setRecording(false);
  }, []);

  const clearAudio = useCallback(() => {
    if (audio) {
      URL.revokeObjectURL(audio.url);
      setAudio(null);
    }
  }, [audio]);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  const handleActionClick = () => {
    if (busy && onStop) { onStop(); return; }
    if (recording) { stopRecording(); return; }
    if (hasContent) { onSend(); return; }
    startRecording();
  };

  const isStop = busy || recording;

  return (
    <div className="input-bar-wrapper">
      {(attachments && attachments.length > 0) || audio ? (
        <div className="input-attachments">
          {attachments?.map((a, i) => (
            <span key={i} className="input-attachment-chip">
              <svg width="11" height="11" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 2h8l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" />
                <polyline points="12 2 12 6 16 6" />
              </svg>
              {a.name}
              <button className="input-attachment-remove" onClick={() => removeAttachment(i)} type="button">&times;</button>
            </span>
          ))}
          {audio && (
            <span className="input-attachment-chip input-attachment-chip--audio">
              <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M2 10 Q6 6 10 10 Q14 14 18 10" strokeDasharray="2 2" />
              </svg>
              Voice memo
              <button className="input-attachment-remove" onClick={clearAudio} type="button">&times;</button>
            </span>
          )}
        </div>
      ) : null}
      <div className="input-bar">
        <div className="input-bar-left" ref={menuRef}>
          <button
            className="input-icon-btn"
            onClick={() => setMenuOpen((o) => !o)}
            disabled={disabled || uploading}
            aria-label="Attach or change options"
          >
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <line x1="10" y1="4" x2="10" y2="16" />
              <line x1="4" y1="10" x2="16" y2="10" />
            </svg>
          </button>
          {menuOpen && (
            <div className="input-menu">
              <button className="input-menu-item" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 14v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
                  <polyline points="7,8 10,4 13,8" />
                  <line x1="10" y1="4" x2="10" y2="14" />
                </svg>
                Upload file
              </button>
              {models && models.length >= 1 && (
                <>
                  <div className="input-menu-sep" />
                  <div className="input-menu-group">
                    <span className="input-menu-heading">
                      {routingMode === "auto" ? "Model (auto-route is on)" : "Model"}
                    </span>
                    {models.map((m) => (
                      <button
                        key={m}
                        className={`input-menu-item${selectedModel === m ? " is-active" : ""}`}
                        onClick={() => {
                          onModelChange?.(m);
                          // Picking a specific model implies fixed mode.
                          if (routingMode === "auto") onRoutingModeChange?.("fixed");
                          setMenuOpen(false);
                        }}
                      >
                        {m.split("/").pop()}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}
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
        <button
          className={`input-send-btn${isStop ? " input-stop-btn" : ""}${!hasContent && !isStop ? " input-send-btn--mic" : ""}`}
          onClick={handleActionClick}
          disabled={disabled && !busy}
          aria-label={isStop ? "Stop" : hasContent ? "Send" : "Voice message"}
        >
          {isStop ? (
            <svg width="18" height="18" viewBox="0 0 20 20" fill="currentColor" stroke="none">
              <rect x="5" y="5" width="10" height="10" rx="1.5" />
            </svg>
          ) : hasContent ? (
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <line x1="10" y1="17" x2="10" y2="4" />
              <polyline points="4,10 10,4 16,10" />
            </svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
              <rect x="7" y="2" width="6" height="10" rx="3" />
              <path d="M4 10a6 6 0 0 0 12 0" />
              <line x1="10" y1="16" x2="10" y2="19" />
              <line x1="7" y1="19" x2="13" y2="19" />
            </svg>
          )}
        </button>
      </div>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        style={{ display: "none" }}
        onChange={(e) => void handleFileSelect(e)}
      />
    </div>
  );
}
