import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { TraceEvent } from "../api";
import VaultFilePreview from "./VaultFilePreview";
import WorkflowViz from "./WorkflowViz";
import LiveActivityStrip from "./LiveActivityStrip";
import "./AssistantMessage.css";

interface Props {
  content: string;
  trace?: TraceEvent[];
  timestamp: Date;
  streaming?: boolean;
  onOpenInVault?: (path: string) => void;
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

const META_TOOLS = new Set(["skills_list", "skill_view"]);

/**
 * Normalize a possibly-vault reference into a clean relative vault path.
 * Accepts:
 *   vault://research/foo.md     → research/foo.md
 *   vault:research/foo.md       → research/foo.md
 *   /research/foo.md            → research/foo.md
 *   research/foo.md             → research/foo.md
 * Returns null if it doesn't look like a vault path (no .md, absolute URL, etc).
 */
function asVaultPath(href: string): string | null {
  if (!href) return null;
  // strip vault:// or vault: prefix
  const m = href.match(/^vault:\/\/(.+)$/i) ?? href.match(/^vault:(.+)$/i);
  if (m) return m[1].replace(/^\/+/, "");
  // absolute URL? no
  if (/^https?:\/\//i.test(href)) return null;
  // anchor / mailto / etc
  if (href.startsWith("#") || href.startsWith("mailto:")) return null;
  // looks like a markdown file path
  if (/\.mdx?$/i.test(href)) return href.replace(/^\/+/, "");
  return null;
}

/**
 * Linkify bare vault-path mentions in plain text so the user doesn't have to
 * rely on the model writing `[x](vault://x)` every time. Matches conservative
 * patterns that end in .md and have at least one `/` OR are wrapped in
 * backticks. Returns a markdown-transformed string where those mentions
 * become [path](vault://path).
 */
function linkifyVaultPaths(content: string): string {
  // Match paths like research/foo.md or projects/my-plan.md (must contain /).
  // Avoid replacing content already inside markdown links or code blocks.
  // Simple strategy: replace only occurrences NOT preceded by `(`, `[`, or `` ` ``.
  return content.replace(
    /(^|[\s("])([a-z0-9][a-z0-9_\-./]*\/[a-z0-9][a-z0-9_\-. ]*\.mdx?)(?=$|[\s.,;:)!"])/gi,
    (_match, pre, path) => `${pre}[${path}](vault://${path})`,
  );
}

export default function AssistantMessage({ content, trace, timestamp, streaming, onOpenInVault }: Props) {
  const [traceOpen, setTraceOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [previewPath, setPreviewPath] = useState<string | null>(null);

  const toolCount = trace
    ? trace.filter((e) => e.tool && !META_TOOLS.has(e.tool)).length
    : 0;
  const showWorkflow = toolCount >= 2;

  const processed = useMemo(() => linkifyVaultPaths(content), [content]);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // clipboard may be blocked
    }
  }

  return (
    <div className="asst-msg">
      <div className="asst-header">
        <div className="asst-avatar" aria-hidden="true" />
        <span className="asst-name">Nexus</span>
        <span className="asst-time">{fmt(timestamp)}</span>
      </div>
      <div className="asst-card">
        {streaming && trace && trace.length > 0 && (
          <LiveActivityStrip events={trace} streaming={true} />
        )}
        <div className="asst-body">
          <ReactMarkdown
            components={{
              a: ({ href, children, ...rest }) => {
                const vaultPath = asVaultPath(href ?? "");
                if (vaultPath) {
                  return (
                    <button
                      type="button"
                      className="vault-inline-link"
                      onClick={(e) => {
                        e.preventDefault();
                        setPreviewPath(vaultPath);
                      }}
                      title={`Preview ${vaultPath}`}
                    >
                      <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
                        <polyline points="9 1.5 9 5 12 5" />
                      </svg>
                      {children}
                    </button>
                  );
                }
                // external link: render as anchor, open in new tab
                return (
                  <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
                    {children}
                  </a>
                );
              },
            }}
          >
            {processed}
          </ReactMarkdown>
        </div>
        {showWorkflow && trace && <WorkflowViz trace={trace} />}
        <div className="asst-footer">
          <button
            className="bubble-action-btn"
            onClick={handleCopy}
            title={copied ? "Copied" : "Copy markdown"}
            aria-label="Copy markdown"
          >
            {copied ? (
              <>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 8 7 12 13 4" />
                </svg>
                <span>Copied</span>
              </>
            ) : (
              <>
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="5" y="5" width="8" height="9" rx="1.5" />
                  <path d="M3 10V3a1 1 0 0 1 1-1h7" />
                </svg>
                <span>Copy</span>
              </>
            )}
          </button>
          {trace && trace.length > 0 && (
            <button
              className="asst-trace-toggle"
              onClick={() => setTraceOpen((v) => !v)}
            >
              {traceOpen ? "▾" : "▸"} Tool activity ({trace.length})
            </button>
          )}
        </div>
        {traceOpen && trace && (
          <pre className="asst-trace-json">
            {JSON.stringify(trace, null, 2)}
          </pre>
        )}
      </div>
      <VaultFilePreview
        path={previewPath}
        onClose={() => setPreviewPath(null)}
        onOpenInVault={onOpenInVault}
      />
    </div>
  );
}
