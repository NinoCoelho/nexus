/**
 * VoiceAttachment — audio player + lazy transcript reveal.
 *
 * Used inside the user message bubble for voice-memo attachments. Renders
 * the playable audio inline; the transcript stays hidden behind a small
 * "person speaking" chip and is fetched on the first click via
 * GET /vault/transcribe (cached server-side by path+mtime).
 */
import { useState } from "react";
import { transcribeVaultAudio, vaultRawUrl } from "../../api";

interface Props {
  path: string;
}

export default function VoiceAttachment({ path }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onToggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (text != null || loading) return;
    setLoading(true);
    setError(null);
    try {
      const r = await transcribeVaultAudio(path);
      setText(r.text);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Transcription failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="user-msg-voice">
      <audio
        className="user-msg-audio"
        src={vaultRawUrl(path)}
        controls
        preload="metadata"
      />
      <button
        type="button"
        className={`user-msg-voice-chip${open ? " open" : ""}`}
        onClick={onToggle}
        aria-expanded={open}
        aria-label={open ? "Hide transcript" : "Show transcript"}
        title={open ? "Hide transcript" : "Show transcript"}
      >
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="6" cy="5" r="2.2" />
          <path d="M2.5 13c0-2 7-2 7 0" />
          <path d="M11 4.5q1.6 1 0 4" />
          <path d="M13 3q2.6 1.5 0 7" />
        </svg>
        <span>{open ? "Hide" : "Transcript"}</span>
      </button>
      {open && (
        <div className="user-msg-voice-transcript">
          {loading && <span className="user-msg-voice-muted">Transcribing…</span>}
          {!loading && error && <span className="user-msg-voice-error">{error}</span>}
          {!loading && !error && text != null && (
            text.trim().length > 0
              ? text
              : <span className="user-msg-voice-muted">No speech detected</span>
          )}
        </div>
      )}
    </div>
  );
}
