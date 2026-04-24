import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { TraceEvent } from "../api";
import type { TimelineStep } from "./ChatView";

/**
 * AssistantMessage — renders a single assistant response in the chat.
 *
 * Layout: avatar + name header, then one of:
 *   1. Streaming mode (content still arriving) — shows partial text + activity strip
 *   2. Final message with timeline — expandable step-by-step breakdown
 *   3. Final message without timeline — plain rendered markdown
 *
 * Tool calls within the response are rendered as compact chips in the
 * activity strip; clicking one opens the StepDetailModal.
 * Vault links (vault://path) are intercepted and surfaced as
 * "Open in Vault" buttons via the onOpenInVault callback.
 */
import VaultFilePreview from "./VaultFilePreview";
import ActivityTimeline from "./ActivityTimeline";
import ChatInlineFilePreview from "./ChatInlineFilePreview";
const LazyChartBlock = lazy(() => import("./ChartBlock"));
import { classify } from "../fileTypes";
import { vaultRawUrl } from "../api";
import "./AssistantMessage.css";

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
  timeline?: TimelineStep[];
  timestamp: Date;
  streaming?: boolean;
  onOpenInVault?: (path: string) => void;
  model?: string;
  routedBy?: "user" | "auto";
}

function fmt(d: Date) {
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function asVaultPath(href: string): string | null {
  if (!href) return null;
  const m = href.match(/^vault:\/\/(.+)$/i) ?? href.match(/^vault:(.+)$/i);
  if (m) return m[1].replace(/^\/+/, "");
  if (/^https?:\/\//i.test(href)) return null;
  if (href.startsWith("#") || href.startsWith("mailto:")) return null;
  if (/\.mdx?$/i.test(href)) return href.replace(/^\/+/, "");
  return null;
}

function linkifyVaultPaths(content: string): string {
  if (!content) return "";
  return content.replace(
    /(^|[\s("])([a-z0-9][a-z0-9_\-./]*\/[a-z0-9][a-z0-9_\-. ]*\.mdx?)(?=$|[\s.,;:)!"])/gi,
    (_match, pre, path) => `${pre}[${path}](vault://${path})`,
  );
}

export default function AssistantMessage({ content, trace, timeline, timestamp, streaming, onOpenInVault, model, routedBy }: Props) {
  const [copied, setCopied] = useState(false);
  const [previewPath, setPreviewPath] = useState<string | null>(null);

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
        {model && (
          <span
            className={`asst-model-badge${routedBy === "auto" ? " asst-model-badge--auto" : ""}`}
            title={routedBy === "auto" ? "Auto-routed by the classifier" : undefined}
          >
            {routedBy === "auto" ? `auto → ${model.split("/").pop()}` : `via ${model.split("/").pop()}`}
          </span>
        )}
        <span className="asst-time">{fmt(timestamp)}</span>
      </div>
      <div className="asst-card">
        <ActivityTimeline steps={timeline} trace={trace} streaming={!!streaming} />
        <div className="asst-body">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            urlTransform={(url) => {
              if (/^vault:/i.test(url)) return url;
              if (/^(?:javascript|data|vbscript):/i.test(url)) return "";
              return url;
            }}
            components={{
              code: ({ className, children, ...rest }) => {
                const match = /language-([\w-]+)/.exec(className || "");
                const lang = match?.[1];
                const raw = String(children ?? "");
                if (lang === "mermaid" && raw.includes("\n")) {
                  return <MermaidBlock code={raw.replace(/\n$/, "")} />;
                }
                if (lang === "nexus-chart") {
                  return (
                    <Suspense fallback={<span className="mermaid-block" />}>
                      <LazyChartBlock code={raw.replace(/\n$/, "")} />
                    </Suspense>
                  );
                }
                return <code className={className} {...rest}>{children}</code>;
              },
              a: ({ href, children, ...rest }) => {
                const vaultPath = asVaultPath(href ?? "");
                if (vaultPath) {
                  const kind = classify(vaultPath).kind;
                  // Markdown opens in the Vault view in a new tab; everything
                  // else opens the raw bytes (images/PDFs/etc.) in a new tab.
                  const newTabHref = kind === "markdown"
                    ? `${window.location.pathname}?view=vault&path=${encodeURIComponent(vaultPath)}`
                    : vaultRawUrl(vaultPath);
                  return (
                    <span className="vault-inline-link-wrap">
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
                      <a
                        href={newTabHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="vault-inline-link-newtab"
                        title="Open in a new tab"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M8 3H3v10h10V8" />
                          <polyline points="9 2 14 2 14 7" />
                          <line x1="14" y1="2" x2="8" y2="8" />
                        </svg>
                      </a>
                      {kind !== "markdown" && (
                        <ChatInlineFilePreview path={vaultPath} />
                      )}
                    </span>
                  );
                }
                if (!href) {
                  return <span {...rest}>{children}</span>;
                }
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
        </div>
      </div>
      <VaultFilePreview
        path={previewPath}
        onClose={() => setPreviewPath(null)}
        onOpenInVault={onOpenInVault}
      />
    </div>
  );
}
