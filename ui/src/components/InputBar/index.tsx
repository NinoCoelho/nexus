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

import { useEffect, useRef, useState } from "react";
import { transcribeAudio, uploadVaultFiles } from "../../api";
import { useToast } from "../../toast/ToastProvider";
import MentionPicker, { type MentionPickerHandle } from "../MentionPicker";
import "../InputBar.css";
import { useAudioRecorder } from "./useAudioRecorder";
import { useMentionPicker } from "./useMentionPicker";
import AttachmentsBar from "./AttachmentsBar";
import ModelBadge from "./ModelBadge";

interface AttachedFile {
  name: string;
  vaultPath: string;
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: (overrideText?: string) => void;
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
  const menuRef = useRef<HTMLDivElement>(null);
  const mentionRef = useRef<MentionPickerHandle>(null);
  const toast = useToast();

  const [uploading, setUploading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  const { recording, audio, setAudio, startRecording, stopRecording, clearAudio } = useAudioRecorder();
  const { mention, setMention, mentionResults, mentionLoading, detectMention, insertMention } = useMentionPicker(value, onChange);

  const hasContent = value.trim().length > 0 || (attachments && attachments.length > 0) || !!audio;

  const adjust = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  };

  const handleTextChange = (text: string) => {
    onChange(text);
    adjust();
    const caret = textareaRef.current?.selectionStart ?? text.length;
    setMention(detectMention(text, caret));
  };

  const handleSelectionChange = () => {
    const el = textareaRef.current;
    if (!el) return;
    setMention(detectMention(el.value, el.selectionStart ?? 0));
  };

  const runTranscribeAndSend = async () => {
    if (!audio) return;
    setTranscribing(true);
    try {
      const { text } = await transcribeAudio(audio.blob);
      const typed = value.trim();
      const combined = typed ? `${text.trim()}\n\n${typed}` : text.trim();
      if (!combined) { toast.error("Transcription returned no text"); return; }
      URL.revokeObjectURL(audio.url);
      setAudio(null);
      onChange("");
      onSend(combined);
    } catch (err) {
      toast.error("Transcription failed", { detail: err instanceof Error ? err.message : undefined });
    } finally {
      setTranscribing(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mention && mentionRef.current?.handleKey(e)) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (disabled || transcribing) return;
      if (audio) { void runTranscribeAndSend(); return; }
      if (hasContent) onSend();
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

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  const handleActionClick = () => {
    if (busy && onStop) { onStop(); return; }
    if (recording) { stopRecording(); return; }
    if (transcribing) return;
    if (audio) { void runTranscribeAndSend(); return; }
    if (hasContent) { onSend(); return; }
    void startRecording();
  };

  const isStop = busy || recording;
  const showModelBadge = !hasContent && !!selectedModel;

  return (
    <div className="input-bar-wrapper">
      <AttachmentsBar
        attachments={attachments}
        audio={audio}
        transcribing={transcribing}
        onRemoveAttachment={removeAttachment}
        onClearAudio={clearAudio}
      />
      <div className="input-bar-positioner">
        {mention && (
          <MentionPicker
            ref={mentionRef}
            results={mentionResults}
            loading={mentionLoading}
            onSelect={(node) => insertMention(node, textareaRef)}
            onClose={() => setMention(null)}
          />
        )}
        <div className="input-bar">
          <div className="input-bar-left">
            <button
              className="input-icon-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || uploading}
              aria-label="Upload file"
              title="Upload file"
            >
              <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 14v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
                <polyline points="7,8 10,4 13,8" />
                <line x1="10" y1="4" x2="10" y2="14" />
              </svg>
            </button>
          </div>
          <textarea
            ref={textareaRef}
            className="input-textarea"
            rows={1}
            placeholder="Message Nexus…"
            value={value}
            onChange={(e) => handleTextChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onKeyUp={handleSelectionChange}
            onClick={handleSelectionChange}
            onBlur={() => setTimeout(() => setMention(null), 120)}
            disabled={disabled}
          />
          {showModelBadge && (
            <ModelBadge
              selectedModel={selectedModel!}
              models={models}
              menuOpen={menuOpen}
              menuRef={menuRef}
              onToggleMenu={() => setMenuOpen((o) => !o)}
              onSelectModel={(m) => { onModelChange?.(m); setMenuOpen(false); }}
            />
          )}
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
