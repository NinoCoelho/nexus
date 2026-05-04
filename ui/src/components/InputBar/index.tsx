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
import { useTranslation } from "react-i18next";
import { uploadVaultFiles, type SlashCommand } from "../../api";
import { findSecrets, type SecretMatch } from "../../lib/secretPatterns";
import { useToast } from "../../toast/ToastProvider";
import MentionPicker, { type MentionPickerHandle } from "../MentionPicker";
import SecretPicker, { type SecretPickerHandle } from "../SecretPicker";
import SlashCommandPicker, { type SlashPickerHandle } from "../SlashCommandPicker";
import "../InputBar.css";
import { useAudioRecorder } from "./useAudioRecorder";
import { useMentionPicker } from "./useMentionPicker";
import { useSecretPicker } from "./useSecretPicker";
import { useSlashPicker } from "./useSlashPicker";
import AttachmentsBar from "./AttachmentsBar";
import ModelBadge from "./ModelBadge";
import RecordingIndicator from "./RecordingIndicator";
import SecretDetectedDialog from "./SecretDetectedDialog";

interface AttachedFile {
  name: string;
  vaultPath: string;
}

interface ExtraAttachment extends AttachedFile {
  /** Optional explicit mime type. Forces backend routing for ambiguous
   * extensions — voice memos use ``audio/webm`` because ``.webm`` would
   * otherwise sniff to ``video/webm`` and skip the audio transcription
   * branch in ``materialize_message``. */
  mimeType?: string;
}

interface SendOptions {
  text?: string;
  inPlace?: boolean;
  bypassSecretGuard?: boolean;
  extraAttachments?: ExtraAttachment[];
  /** "voice" when the user dictated this turn (hold-to-record). The backend
   * uses this signal to decide whether to fire spoken acknowledgments. */
  inputMode?: "voice" | "text";
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: (override?: string | SendOptions) => void;
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
  const { t } = useTranslation("chat");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const mentionRef = useRef<MentionPickerHandle>(null);
  const secretRef = useRef<SecretPickerHandle>(null);
  const slashRef = useRef<SlashPickerHandle>(null);
  const toast = useToast();

  const [uploading, setUploading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  // ``pendingSecret`` parks a send while the user decides what to do with a
  // secret-shaped substring. ``pendingSendText`` carries the exact text we
  // were about to send so "Send anyway" doesn't lose attachments or
  // transcribed audio merged in by the caller.
  const [pendingSecret, setPendingSecret] = useState<SecretMatch | null>(null);
  const [pendingSendText, setPendingSendText] = useState<string>("");

  // Intercepts onSend with the secret guard. Returns true when send proceeded
  // (or was queued); false when it was blocked pending the modal.
  const guardedSend = (textToCheck: string, doSend: () => void): boolean => {
    const matches = findSecrets(textToCheck);
    if (matches.length === 0) {
      doSend();
      return true;
    }
    setPendingSecret(matches[0]);
    setPendingSendText(textToCheck);
    return false;
  };

  const { recording, audio, setAudio, levels, seconds, startRecording, stopRecording, cancelRecording, clearAudio } = useAudioRecorder();

  // iOS Safari refuses async `audio.play()` (e.g. from an SSE-driven
  // voice ack) unless the page has a "consumed" user gesture. Pressing
  // record IS such a gesture — but we need to actually *touch* the
  // audio system inside the gesture for Safari to remember it. The
  // Web Audio API trick is the most reliable: create an AudioContext,
  // play a 1-sample buffer at zero volume, and Safari unlocks ALL
  // subsequent audio for the page lifetime.
  const audioUnlockedRef = useRef(false);
  const unlockAudioForIOS = useCallback(() => {
    if (audioUnlockedRef.current) return;
    audioUnlockedRef.current = true;
    if (typeof window === "undefined") return;
    try {
      // 1) Web Audio path — required for HTMLAudioElement playback on
      //    iOS Safari to work from later async callbacks.
      const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
      if (Ctx) {
        const ctx = new Ctx();
        // resume() is a no-op on browsers that don't suspend new contexts,
        // but iOS suspends until user gesture; this is what unlocks it.
        if (typeof ctx.resume === "function") void ctx.resume().catch(() => {});
        const buffer = ctx.createBuffer(1, 1, 22050);
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(ctx.destination);
        if (typeof source.start === "function") source.start(0);
        // Don't close the context — keep it alive so subsequent plays inherit
        // the unlock. Safari will GC it on tab close.
      }
      // 2) Belt-and-suspenders: also prime an HTMLAudioElement. Some iOS
      //    versions need both paths primed.
      const SILENT_WAV =
        "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
      const a = new Audio(SILENT_WAV);
      a.muted = false;
      a.volume = 0;
      void a.play().then(() => a.pause()).catch(() => { /* ignore */ });
    } catch {
      // Not fatal — Web Speech fallback in the player still works.
    }
  }, []);
  const { mention, setMention, mentionResults, mentionLoading, detectMention, insertMention } = useMentionPicker(value, onChange);
  const { secret, setSecret, secretResults, detectSecret, insertSecret } = useSecretPicker(value, onChange);
  const { slash, setSlash, commands: slashCommands } = useSlashPicker(value);

  const insertSlashCommand = (cmd: SlashCommand) => {
    // Replace the leading "/<query>" with "/<name>" and (if the command takes
    // args) leave a trailing space so the user can type the args directly.
    const trailing = cmd.args_hint ? " " : "";
    const next = `/${cmd.name}${trailing}`;
    onChange(next);
    setSlash(null);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(next.length, next.length);
        el.style.height = "auto";
        el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
      }
    });
  };

  const hasContent = value.trim().length > 0 || (attachments && attachments.length > 0) || !!audio;

  const adjust = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  };

  // Re-fit the textarea whenever the controlled value changes — this is
  // what shrinks the box back to a single row after a successful send
  // (the parent clears `value` to "" but keeps the imperative height
  // we set on the last keystroke).
  useEffect(() => { adjust(); }, [value]);

  const handleTextChange = (text: string) => {
    onChange(text);
    adjust();
    const caret = textareaRef.current?.selectionStart ?? text.length;
    setMention(detectMention(text, caret));
    setSecret(detectSecret(text, caret));
  };

  const handleSelectionChange = () => {
    const el = textareaRef.current;
    if (!el) return;
    const caret = el.selectionStart ?? 0;
    setMention(detectMention(el.value, caret));
    setSecret(detectSecret(el.value, caret));
  };

  const uploadAudioAndSend = async (blob: Blob, urlToRevoke?: string) => {
    try {
      // Pick filename extension from the actual blob mime — iOS Safari
      // produces audio/mp4 (AAC), desktop Chrome/Firefox produce
      // audio/webm. The previous hardcoded `.webm` was making
      // faster-whisper choke on iPhone recordings.
      const rawMime = (blob.type || "audio/webm").split(";")[0].trim();
      const extByMime: Record<string, string> = {
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/mp4": "m4a",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
      };
      const ext = extByMime[rawMime] || "webm";
      const stamp = new Date().toISOString().replace(/[:T]/g, "-").replace(/\..*/, "");
      const file = new File([blob], `voice-${stamp}.${ext}`, { type: rawMime });
      const result = await uploadVaultFiles([file], "uploads/voice");
      const newAttachments: ExtraAttachment[] = result.uploaded.map((f) => ({
        name: f.path.split("/").pop() ?? f.path,
        vaultPath: f.path,
        mimeType: rawMime,
      }));
      if (newAttachments.length === 0) {
        toast.error(t("chat:input.uploadFailed"));
        return;
      }
      // Voice memos send unconditionally — the secret guard would route
      // through a modal whose "Send anyway" path doesn't carry
      // ``extraAttachments``, so a typed prefix that tripped the regex would
      // silently drop the recording. The audio is the primary signal here.
      const typed = value.trim();
      onChange("");
      onSend({ text: typed, extraAttachments: newAttachments, inputMode: "voice" });
    } catch (err) {
      toast.error(t("chat:input.uploadFailed"), { detail: err instanceof Error ? err.message : undefined });
    } finally {
      if (urlToRevoke) URL.revokeObjectURL(urlToRevoke);
      setTranscribing(false);
    }
  };

  // Legacy path: keyboard-only Enter on a stored audio attachment. Now uploads
  // the staged blob the same way the live recording flow does, so behavior
  // stays consistent if `audio` ever lands in state via a future entry point.
  const runTranscribeAndSend = async () => {
    if (!audio) return;
    const { blob, url } = audio;
    setAudio(null);
    await uploadAudioAndSend(blob, url);
  };

  // Stop the current recording and immediately upload + send the result.
  // The audio rides as a chat attachment; the backend transcribes inline as
  // part of preparing the model call (see multimodal.materialize_message),
  // so the user perceives "recording → message in chat" with no separate
  // transcribe round-trip and no input-bar pill.
  const stopRecordingAndSend = () => {
    // ``transcribing`` doubles as a "voice send in flight" guard so the
    // synthetic click that follows pointerup (and the stop-button visual)
    // doesn't re-enter startRecording before the blob lands.
    setTranscribing(true);
    stopRecording({
      onComplete: (a) => { void uploadAudioAndSend(a.blob, a.url); },
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mention && mentionRef.current?.handleKey(e)) return;
    if (secret && secretRef.current?.handleKey(e)) return;
    if (slash && slashRef.current?.handleKey(e)) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (disabled || transcribing) return;
      if (audio) { void runTranscribeAndSend(); return; }
      if (hasContent) guardedSend(value, () => onSend());
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
      toast.error(t("chat:input.uploadFailed"), { detail: err instanceof Error ? err.message : undefined });
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

  // Voice-button gesture state. The button supports two flows:
  //   - tap → starts recording; a second tap stops + sends.
  //   - press-and-hold → records while held; release stops + sends.
  // We discriminate at pointerup: a release within HOLD_THRESHOLD_MS of
  // pointerdown is treated as a tap (recording stays on); anything longer is
  // a hold (auto-send on release).
  const HOLD_THRESHOLD_MS = 250;
  const pressStartedAtRef = useRef<number | null>(null);
  const tapModeRef = useRef(false);

  const handleActionClick = () => {
    // Click only handles the non-voice paths (stop/send/transcribe-existing).
    // Voice start/stop is driven by pointerdown/pointerup so we can detect
    // press-and-hold vs. tap. Without this guard a synthetic click that
    // follows pointerup would fire stopRecordingAndSend a second time.
    if (busy && onStop) { onStop(); return; }
    if (recording) return;
    if (transcribing) return;
    if (audio) { void runTranscribeAndSend(); return; }
    if (hasContent) { guardedSend(value, () => onSend()); return; }
    // Mic-only click (no pointer events, e.g. keyboard activation): start
    // recording in tap-mode so a follow-up Enter/Space stops + sends.
    tapModeRef.current = true;
    unlockAudioForIOS();
    void startRecording();
  };

  const handleActionPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    // Only react to primary button presses; leave right-click etc. alone.
    if (e.button !== 0) return;
    if (busy || transcribing) return;
    if (hasContent || audio) return;
    if (recording && tapModeRef.current) {
      // Second tap of tap-tap flow → stop + send immediately.
      e.preventDefault();
      tapModeRef.current = false;
      pressStartedAtRef.current = null;
      stopRecordingAndSend();
      return;
    }
    if (recording) return;
    e.preventDefault();
    pressStartedAtRef.current = Date.now();
    tapModeRef.current = false;
    unlockAudioForIOS();
    void startRecording();
  };

  const handleActionPointerUp = () => {
    const startedAt = pressStartedAtRef.current;
    if (startedAt == null) return;
    pressStartedAtRef.current = null;
    const elapsed = Date.now() - startedAt;
    if (elapsed >= HOLD_THRESHOLD_MS) {
      // Press-and-hold release → auto-send.
      stopRecordingAndSend();
    } else {
      // Quick tap → stay in recording, await the next tap to send.
      tapModeRef.current = true;
    }
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
        {secret && !mention && (
          <SecretPicker
            ref={secretRef}
            results={secretResults}
            onSelect={(name) => insertSecret(name, textareaRef)}
            onClose={() => setSecret(null)}
          />
        )}
        {slash && !mention && !secret && (
          <SlashCommandPicker
            ref={slashRef}
            results={slashCommands}
            onSelect={insertSlashCommand}
            onClose={() => setSlash(null)}
          />
        )}
        <div className="input-bar">
          <div className="input-bar-left">
            <button
              className="input-icon-btn"
              onClick={() => fileInputRef.current?.click()}
              disabled={disabled || uploading}
              aria-label={t("chat:input.uploadFileAria")}
              title={t("chat:input.uploadFile")}
            >
              <svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 14v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
                <polyline points="7,8 10,4 13,8" />
                <line x1="10" y1="4" x2="10" y2="14" />
              </svg>
            </button>
          </div>
          {recording ? (
            <RecordingIndicator
              levels={levels}
              seconds={seconds}
              onCancel={cancelRecording}
            />
          ) : (
            <textarea
              ref={textareaRef}
              className="input-textarea"
              rows={1}
              placeholder={t("chat:input.placeholder")}
              value={value}
              onChange={(e) => handleTextChange(e.target.value)}
              onKeyDown={handleKeyDown}
              onKeyUp={handleSelectionChange}
              onClick={handleSelectionChange}
              onBlur={() => setTimeout(() => { setMention(null); setSecret(null); }, 120)}
              disabled={disabled}
            />
          )}
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
            onPointerDown={handleActionPointerDown}
            onPointerUp={handleActionPointerUp}
            onPointerLeave={handleActionPointerUp}
            onPointerCancel={handleActionPointerUp}
            disabled={disabled && !busy}
            aria-label={isStop ? t("chat:input.stop") : hasContent ? t("chat:input.send") : t("chat:input.voiceMessage")}
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
      {pendingSecret && (
        <SecretDetectedDialog
          detected={pendingSecret.value}
          onCancel={() => {
            setPendingSecret(null);
            setPendingSendText("");
            textareaRef.current?.focus();
          }}
          onSendAnyway={() => {
            const textToSend = pendingSendText;
            setPendingSecret(null);
            setPendingSendText("");
            // If the parked send was a transcription/attachment-merged
            // payload that the parent didn't yet have in state, pass it
            // back as an override; otherwise the textarea content is the
            // source of truth.
            const sameAsValue = textToSend === value;
            onChange("");
            onSend(sameAsValue
              ? { bypassSecretGuard: true }
              : { text: textToSend, bypassSecretGuard: true }
            );
          }}
          onSaveAsCredential={(name) => {
            // Replace every occurrence of the matched secret in the
            // textarea with the placeholder, then close the modal.
            const replaced = pendingSendText.replaceAll(
              pendingSecret.value,
              `$${name}`,
            );
            setPendingSecret(null);
            setPendingSendText("");
            onChange(replaced);
            // Don't auto-send — the user might want to review the message
            // with the placeholder in place before pressing send again.
            textareaRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}
