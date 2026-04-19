import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { getVaultFile, type VaultFile } from "../api";
import "./VaultFilePreview.css";

interface Props {
  path: string | null;
  onClose: () => void;
  /** Called when the user clicks "Open in Vault" — parent navigates to Vault view for this file. */
  onOpenInVault?: (path: string) => void;
}

export default function VaultFilePreview({ path, onClose, onOpenInVault }: Props) {
  const [file, setFile] = useState<VaultFile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    setFile(null);
    setError(null);
    setLoading(true);
    getVaultFile(path)
      .then((f) => setFile(f))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [path]);

  useEffect(() => {
    if (!path) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [path, onClose]);

  if (!path) return null;

  const body = file?.body ?? file?.content ?? "";

  return (
    <>
      <div className="vault-preview-backdrop" onClick={onClose} />
      <div className="vault-preview-modal" role="dialog" aria-label={`Preview ${path}`}>
        <div className="vault-preview-header">
          <div className="vault-preview-path" title={path}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
              <polyline points="9 1.5 9 5 12 5" />
            </svg>
            <span>{path}</span>
          </div>
          <div className="vault-preview-actions">
            {onOpenInVault && (
              <button
                className="vault-preview-btn"
                onClick={() => { onOpenInVault(path); onClose(); }}
                title="Open in Vault view"
              >
                Open in Vault
              </button>
            )}
            <button className="vault-preview-close" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>
        <div className="vault-preview-body">
          {loading && <p className="vault-preview-dim">Loading…</p>}
          {error && <p className="vault-preview-error">Could not load: {error}</p>}
          {!loading && !error && file && (
            <div className="vault-preview-content">
              {file.frontmatter && Object.keys(file.frontmatter).length > 0 && (
                <div className="vault-preview-frontmatter">
                  {Object.entries(file.frontmatter).map(([k, v]) => (
                    <div key={k} className="vault-preview-fm-row">
                      <span className="vault-preview-fm-key">{k}</span>
                      <span className="vault-preview-fm-val">
                        {typeof v === "string" ? v : JSON.stringify(v)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              <div className="vault-preview-markdown">
                <ReactMarkdown>{body}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
