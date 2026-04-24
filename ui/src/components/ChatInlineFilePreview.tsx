/**
 * ChatInlineFilePreview — tiny expandable inline preview for a vault file
 * referenced in chat. Gated behind a toggle to keep chat bubbles tidy.
 */

import { useEffect, useState } from "react";
import FilePreview from "./FilePreview";
import { getVaultFile, type VaultFile } from "../api";
import { classify } from "../fileTypes";
import "./ChatInlineFilePreview.css";

interface Props {
  path: string;
}

export default function ChatInlineFilePreview({ path }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [file, setFile] = useState<VaultFile | null>(null);
  const [loading, setLoading] = useState(false);

  const kind = classify(path).kind;
  // Images/video/audio/pdf need no text fetch — FilePreview uses the raw URL.
  const needsFetch = kind === "code" || kind === "csv" || kind === "json" || kind === "text";

  useEffect(() => {
    if (!expanded || !needsFetch || file) return;
    setLoading(true);
    getVaultFile(path)
      .then(setFile)
      .catch(() => setFile({ path, content: "" }))
      .finally(() => setLoading(false));
  }, [expanded, needsFetch, file, path]);

  return (
    <span className="chat-inline-preview">
      <button
        type="button"
        className="chat-inline-preview-toggle"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setExpanded((v) => !v);
        }}
        title={expanded ? "Hide preview" : "Show inline preview"}
      >
        {expanded ? "Hide preview" : "Show preview"}
      </button>
      {expanded && (
        <div className="chat-inline-preview-body">
          {loading ? (
            <span className="chat-inline-preview-dim">Loading…</span>
          ) : (
            <FilePreview
              path={path}
              content={file?.content ?? ""}
              size={file?.size}
              compact
            />
          )}
        </div>
      )}
    </span>
  );
}
