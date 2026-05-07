/**
 * VoiceAttachment — audio player + collapsed transcript preview.
 *
 * Used inside the user message bubble for voice-memo attachments. Renders
 * the playable audio inline. The transcript is fetched eagerly on mount
 * via GET /vault/transcribe (cached server-side by path+mtime) and shown
 * as a single-line collapsed preview (~200 chars). Clicking the preview
 * reveals the full transcript text.
 */
import { useEffect, useState } from "react";
import { transcribeVaultAudio, vaultRawUrl } from "../../api";

interface Props {
  path: string;
}

export default function VoiceAttachment({ path }: Props) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    transcribeVaultAudio(path)
      .then((r) => { if (!cancelled) setText(r.text); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Transcription failed"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [path]);

  const previewText = text != null && text.trim().length > 0
    ? text.length > 200 ? text.slice(0, 200) + "\u2026" : text
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
        <button
          type="button"
          className="user-msg-voice-preview"
          onClick={() => setOpen(true)}
          aria-label="Show transcript"
          title="Show transcript"
        >
          {loading && <span className="user-msg-voice-muted">Transcribing\u2026</span>}
          {!loading && error && <span className="user-msg-voice-error">{error}</span>}
          {!loading && !error && text != null && (
            previewText
              ? <>{previewText}</>
              : <span className="user-msg-voice-muted">No speech detected</span>
          )}
        </button>
      )}
      {open && (
        <div className="user-msg-voice-transcript">
          {loading && <span className="user-msg-voice-muted">Transcribing\u2026</span>}
          {!loading && error && <span className="user-msg-voice-error">{error}</span>}
          {!loading && !error && text != null && (
            text.trim().length > 0
              ? text
              : <span className="user-msg-voice-muted">No speech detected</span>
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
