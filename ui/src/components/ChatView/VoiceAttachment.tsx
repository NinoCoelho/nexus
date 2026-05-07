/**
 * VoiceAttachment — audio player + collapsed transcript preview.
 *
 * Used inside the user message bubble for voice-memo attachments. Renders
 * the playable audio inline. The transcript is fetched eagerly on mount
 * via GET /vault/transcribe (cached server-side by path+mtime) and shown
 * as a collapsed preview (~200 chars). Clicking the preview reveals the
 * full transcript text.
 */
import { useEffect, useState } from "react";
import { transcribeVaultAudio, vaultRawUrl } from "../../api";

interface Props {
  path: string;
}

export default function VoiceAttachment({ path }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    transcribeVaultAudio(path)
      .then((r) => { if (!cancelled) setText(r.text ?? ""); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Transcription failed"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [path]);

  const hasText = text != null && text.trim().length > 0;
  const previewText = hasText
    ? text!.length > 200 ? text!.slice(0, 200) + "\u2026" : text!
    : null;

  return (
    <div className="user-msg-voice">
      <audio
        className="user-msg-audio"
        src={vaultRawUrl(path)}
        controls
        preload="metadata"
      />
      {!open && (
        <div
          className="user-msg-voice-preview"
          role="button"
          tabIndex={0}
          onClick={() => setOpen(true)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setOpen(true); }}
          aria-label="Show full transcript"
          title="Show full transcript"
        >
          {loading ? (
            <em className="user-msg-voice-muted">Transcribing&#8230;</em>
          ) : error ? (
            <span className="user-msg-voice-error">{error}</span>
          ) : hasText ? (
            <em>{previewText}</em>
          ) : (
            <em className="user-msg-voice-muted">No speech detected</em>
          )}
        </div>
      )}
      {open && (
        <div className="user-msg-voice-transcript">
          {loading ? (
            <em className="user-msg-voice-muted">Transcribing&#8230;</em>
          ) : error ? (
            <span className="user-msg-voice-error">{error}</span>
          ) : hasText ? (
            text
          ) : (
            <em className="user-msg-voice-muted">No speech detected</em>
          )}
          <button
            type="button"
            className="user-msg-voice-collapse"
            onClick={() => setOpen(false)}
            aria-label="Collapse transcript"
            title="Collapse transcript"
          >
            Collapse
          </button>
        </div>
      )}
    </div>
  );
}
