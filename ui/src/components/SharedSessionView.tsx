import { useEffect, useState } from "react";
import { getSharedSession, type SharedSession } from "../api";
import MarkdownView from "./MarkdownView";
import { useVaultLinkPreview } from "./vaultLink";
import "./SharedSessionView.css";

function fmtTime(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

interface Props {
  token: string;
}

export default function SharedSessionView({ token }: Props) {
  const [data, setData] = useState<SharedSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { onPreview, modal } = useVaultLinkPreview();

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    getSharedSession(token)
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [token]);

  if (error) {
    return (
      <div className="share-view share-view--error">
        <h1>Link unavailable</h1>
        <p>This share link is invalid, expired, or has been revoked.</p>
      </div>
    );
  }
  if (!data) {
    return <div className="share-view share-view--loading">Loading…</div>;
  }

  return (
    <div className="share-view">
      <header className="share-header">
        <span className="share-badge">Read-only</span>
        <h1>{data.title || "Shared session"}</h1>
        <p className="share-meta">Shared {fmtTime(data.shared_at)}</p>
      </header>
      <div className="share-messages">
        {data.messages.map((m, idx) => (
          <article key={idx} className={`share-msg share-msg--${m.role}`}>
            <header>
              <span className="share-msg-role">{m.role === "user" ? "You" : "Nexus"}</span>
              <span className="share-msg-time">{fmtTime(m.created_at)}</span>
            </header>
            {m.role === "assistant" ? (
              <MarkdownView onVaultLinkPreview={onPreview} linkifyVaultPaths>
                {m.content}
              </MarkdownView>
            ) : (
              <p className="share-user-bubble">{m.content}</p>
            )}
          </article>
        ))}
      </div>
      <footer className="share-footer">
        <span>Powered by <strong>Nexus</strong></span>
      </footer>
      {modal}
    </div>
  );
}
