import { useEffect, useState } from "react";
import MarkdownView from "./MarkdownView";
import { getVaultFile, type VaultFile } from "../api";
import "./VaultFilePreview.css";

interface Props {
  path: string | null;
  onClose: () => void;
  onOpenInVault?: (path: string) => void;
  onViewEntityGraph?: (path: string) => void;
}

export default function VaultFilePreview({ path, onClose, onOpenInVault, onViewEntityGraph }: Props) {
  const [file, setFile] = useState<VaultFile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

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

  const rawContent = file?.content ?? "";
  const body = file?.body ?? rawContent;

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(rawContent);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // clipboard may be blocked
    }
  }

  function handleDownload() {
    const blob = new Blob([rawContent], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const basename = (path ?? "file").split("/").pop() || "file.md";
    const link = document.createElement("a");
    link.href = url;
    link.download = basename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

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
            <button
              className="vault-preview-btn vault-preview-btn--icon"
              onClick={handleCopy}
              disabled={!rawContent}
              title={copied ? "Copied" : "Copy markdown"}
              aria-label="Copy markdown"
            >
              {copied ? (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 8 7 12 13 4" />
                </svg>
              ) : (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="5" y="5" width="8" height="9" rx="1.5" />
                  <path d="M3 10V3a1 1 0 0 1 1-1h7" />
                </svg>
              )}
              <span>{copied ? "Copied" : "Copy"}</span>
            </button>
            <button
              className="vault-preview-btn vault-preview-btn--icon"
              onClick={handleDownload}
              disabled={!rawContent}
              title="Download as .md"
              aria-label="Download"
            >
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 2v9" />
                <polyline points="4 7 8 11 12 7" />
                <path d="M3 13h10" />
              </svg>
              <span>Download</span>
            </button>
            {onOpenInVault && (
              <button
                className="vault-preview-btn vault-preview-btn--icon vault-preview-btn--primary"
                onClick={() => { onOpenInVault(path); onClose(); }}
                title="Open in Vault view"
              >
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M8 3H3v10h10V8" />
                  <polyline points="9 2 14 2 14 7" />
                  <line x1="14" y1="2" x2="8" y2="8" />
                </svg>
                <span>Open in Vault</span>
              </button>
            )}
            {onViewEntityGraph && (
              <button
                className="vault-preview-btn vault-preview-btn--icon"
                onClick={() => { onViewEntityGraph(path); onClose(); }}
                title="View entity graph for this file"
              >
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="8" cy="4" r="2" />
                  <circle cx="4" cy="12" r="2" />
                  <circle cx="12" cy="12" r="2" />
                  <line x1="8" y1="6" x2="5" y2="10" />
                  <line x1="8" y1="6" x2="11" y2="10" />
                </svg>
                <span>Entity Graph</span>
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
                <MarkdownView>{body}</MarkdownView>
              </div>
              {(file.tags && file.tags.length > 0) && (
                <div className="vault-preview-footer-section">
                  <div className="vault-preview-footer-label">Tags</div>
                  <div className="vault-preview-tags">
                    {file.tags.map((tag) => (
                      <span key={tag} className="vault-preview-tag-pill">#{tag}</span>
                    ))}
                  </div>
                </div>
              )}
              {(file.backlinks && file.backlinks.length > 0) && (
                <div className="vault-preview-footer-section">
                  <div className="vault-preview-footer-label">Backlinks</div>
                  <div className="vault-preview-backlinks">
                    {file.backlinks.map((bl) => (
                      <button
                        key={bl}
                        className="vault-preview-backlink-btn"
                        onClick={() => setPreviewPath(bl)}
                      >
                        {bl}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      {previewPath && (
        <VaultFilePreview
          path={previewPath}
          onClose={() => setPreviewPath(null)}
          onOpenInVault={onOpenInVault}
          onViewEntityGraph={onViewEntityGraph}
        />
      )}
    </>
  );
}
