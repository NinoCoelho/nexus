import { useEffect, useState } from "react";
import { transcribeVaultAudio } from "../../api";

interface Props {
  path: string;
}

export default function VoiceAttachment({ path }: Props) {
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

  return (
    <div className="user-msg-voice">
      {loading ? (
        <em className="user-msg-voice-muted">Transcribing&#8230;</em>
      ) : error ? (
        <span className="user-msg-voice-error">{error}</span>
      ) : hasText ? (
        text
      ) : (
        <em className="user-msg-voice-muted">No speech detected</em>
      )}
    </div>
  );
}
