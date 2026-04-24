/**
 * FilePreview — renders a vault file by classified kind.
 *
 * Markdown goes through MarkdownView (keeps frontmatter stripping behavior
 * via the caller). Images/PDF/video/audio use raw bytes via /vault/raw.
 * Code, JSON, CSV, text render inline. Binary falls back to a download /
 * open-raw card.
 */

import { useMemo, useState } from "react";
import MarkdownView from "./MarkdownView";
import { classify, formatBytes } from "../fileTypes";
import { vaultRawUrl } from "../api";
import "./FilePreview.css";

interface Props {
  path: string;
  /** Raw text content, if the file is textual. For binary types this is ignored. */
  content?: string;
  /** Optional body (markdown with frontmatter stripped). Falls back to content. */
  body?: string;
  size?: number;
  /** When true, the textual renderers clamp to a smaller height (chat inline use). */
  compact?: boolean;
}

function parseCsv(raw: string, maxRows = 200): string[][] {
  const out: string[][] = [];
  const lines = raw.split(/\r?\n/);
  for (const line of lines) {
    if (!line && out.length === 0) continue;
    if (out.length >= maxRows) break;
    // Minimal CSV split — handles quoted cells with embedded commas and "" escapes.
    const cells: string[] = [];
    let cur = "";
    let inQ = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (inQ) {
        if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (ch === '"') { inQ = false; }
        else { cur += ch; }
      } else {
        if (ch === '"') inQ = true;
        else if (ch === ',') { cells.push(cur); cur = ""; }
        else cur += ch;
      }
    }
    cells.push(cur);
    out.push(cells);
  }
  return out;
}

export default function FilePreview({ path, content = "", body, size, compact = false }: Props) {
  const c = classify(path);
  const rawUrl = vaultRawUrl(path);
  const [showRawCsv, setShowRawCsv] = useState(false);
  const [zoomedImage, setZoomedImage] = useState(false);

  const csvRows = useMemo(() => {
    if (c.kind !== "csv" || showRawCsv) return null;
    return parseCsv(content);
  }, [c.kind, content, showRawCsv]);

  const prettyJson = useMemo(() => {
    if (c.kind !== "json") return null;
    try {
      return JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      return content;
    }
  }, [c.kind, content]);

  const className = `file-preview file-preview--${c.kind}${compact ? " file-preview--compact" : ""}`;

  switch (c.kind) {
    case "markdown":
      return (
        <div className={className}>
          <MarkdownView>{body ?? content}</MarkdownView>
        </div>
      );

    case "image":
      return (
        <div className={className}>
          <img
            src={rawUrl}
            alt={path}
            className={`file-preview-image${zoomedImage ? " file-preview-image--zoomed" : ""}`}
            onClick={() => setZoomedImage((z) => !z)}
          />
        </div>
      );

    case "pdf":
      return (
        <div className={className}>
          <embed src={rawUrl} type="application/pdf" className="file-preview-pdf" />
        </div>
      );

    case "video":
      return (
        <div className={className}>
          <video src={rawUrl} controls className="file-preview-video" />
        </div>
      );

    case "audio":
      return (
        <div className={className}>
          <audio src={rawUrl} controls className="file-preview-audio" />
        </div>
      );

    case "code":
      return (
        <div className={className}>
          <MarkdownView>
            {"```" + (c.language ?? "") + "\n" + content + "\n```"}
          </MarkdownView>
        </div>
      );

    case "json":
      return (
        <div className={className}>
          <MarkdownView>{"```json\n" + (prettyJson ?? "") + "\n```"}</MarkdownView>
        </div>
      );

    case "csv":
      return (
        <div className={className}>
          <div className="file-preview-csv-toolbar">
            <button
              className="file-preview-btn"
              onClick={() => setShowRawCsv((v) => !v)}
            >
              {showRawCsv ? "Show table" : "Show raw"}
            </button>
          </div>
          {showRawCsv || !csvRows ? (
            <pre className="file-preview-pre">{content}</pre>
          ) : (
            <div className="file-preview-csv-scroll">
              <table className="file-preview-csv-table">
                <tbody>
                  {csvRows.map((row, i) => (
                    <tr key={i} className={i === 0 ? "file-preview-csv-head" : undefined}>
                      {row.map((cell, j) => (
                        i === 0
                          ? <th key={j}>{cell}</th>
                          : <td key={j}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      );

    case "text":
      return (
        <div className={className}>
          <pre className="file-preview-pre">{content}</pre>
        </div>
      );

    case "archive":
    case "binary":
      return (
        <div className={className}>
          <div className="file-preview-binary-card">
            <div className="file-preview-binary-label">
              {c.label}{size != null ? ` · ${formatBytes(size)}` : ""}
            </div>
            <div className="file-preview-binary-actions">
              <a
                href={rawUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="file-preview-btn"
              >
                Open raw in new tab
              </a>
              <a
                href={rawUrl}
                download={path.split("/").pop() ?? "file"}
                className="file-preview-btn"
              >
                Download
              </a>
            </div>
          </div>
        </div>
      );
  }
}
