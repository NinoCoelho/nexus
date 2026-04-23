import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { TraceEvent } from "../api";
import VaultFilePreview from "./VaultFilePreview";
import WorkflowViz from "./WorkflowViz";
import LiveActivityStrip from "./LiveActivityStrip";
import "./AssistantMessage.css";

// Mermaid is ~280 kB gzipped — lazy-loaded on first diagram so users who
// never see one don't pay the cost. `startOnLoad: false` so we render
// explicitly per block; streaming deltas don't trigger a doc re-scan.
let _mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  if (!_mermaidPromise) {
    _mermaidPromise = import("mermaid").then((m) => {
      const mermaid = m.default;
      const bg = getComputedStyle(document.documentElement)
        .getPropertyValue("--bg").trim().toLowerCase();
      const isDark = /^#[01][0-9a-f]/i.test(bg)
        || bg.startsWith("#0") || bg.startsWith("#1") || bg.startsWith("#2");
      mermaid.initialize({
        startOnLoad: false,
        theme: isDark ? "dark" : "default",
        securityLevel: "strict",
        fontFamily: "inherit",
      });
      return mermaid;
    });
  }
  return _mermaidPromise;
}

function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const idRef = useRef(`m${Math.random().toString(36).slice(2, 10)}`);

  useEffect(() => {
    const trimmed = code.trim();
    if (!trimmed) return;
    let cancelled = false;
    loadMermaid()
      .then((mermaid) => mermaid.render(idRef.current, trimmed))
      .then(({ svg }) => {
        if (cancelled || !ref.current) return;
        ref.current.innerHTML = svg;
        setErr(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setErr(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [code]);

  if (err) {
    return (
      <pre className="mermaid-error">
        <code>{`mermaid error: ${err}\n\n${code}`}</code>
      </pre>
    );
  }
  return <div ref={ref} className="mermaid-block" />;
}

interface Props {
  content: string;
  trace?: TraceEvent[];
  timestamp: Date;
  streaming?: boolean;
  onOpenInVault?: (path: string) => void;
  model?: string;
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

export default function AssistantMessage({ content, trace, timestamp, streaming, onOpenInVault, model }: Props) {
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
        {model && <span className="asst-model-badge">via {model.split("/").pop()}</span>}
        <span className="asst-time">{fmt(timestamp)}</span>
      </div>
      <div className="asst-card">
        {streaming && trace && trace.length > 0 && (
          <LiveActivityStrip events={trace} streaming={true} />
        )}
        <div className="asst-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            // react-markdown sanitizes URLs by default and strips unknown
            // schemes like `vault://` — the resulting empty href made our
            // fallback <a> navigate to the current page (full reload).
            // Preserve vault:// (we handle it ourselves below) and still
            // block genuinely dangerous schemes.
            urlTransform={(url) => {
              if (/^vault:/i.test(url)) return url;
              if (/^(?:javascript|data|vbscript):/i.test(url)) return "";
              return url;
            }}
            components={{
              code: ({ className, children, ...rest }) => {
                const match = /language-(\w+)/.exec(className || "");
                const lang = match?.[1];
                const raw = String(children ?? "");
                // Mermaid: render as SVG. Only for actual fenced blocks —
                // inline `mermaid` code spans (no newline) stay as code.
                if (lang === "mermaid" && raw.includes("\n")) {
                  return <MermaidBlock code={raw.replace(/\n$/, "")} />;
                }
                return <code className={className} {...rest}>{children}</code>;
              },
              a: ({ href, children, ...rest }) => {
                const vaultPath = asVaultPath(href ?? "");
                if (vaultPath) {
                  return (
                    <button
                      type="button"
                      className="vault-inline-link"
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
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
                // Empty/missing href: render inert — avoid the navigate-to-
                // current-page reload if react-markdown ever hands us one.
                if (!href) {
                  return <span {...rest}>{children}</span>;
                }
                // External link: new tab.
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
