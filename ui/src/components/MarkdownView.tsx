/**
 * MarkdownView — safe markdown renderer with GFM + Mermaid support.
 *
 * Renders markdown content using react-markdown with remark-gfm (tables,
 * strikethrough, task lists). Code blocks with language "mermaid" are
 * lazy-loaded and rendered as SVG diagrams via the mermaid library.
 *
 * Vault links (vault://path, plus markdown paths ending in .md) are detected
 * via asVaultPath and rendered with the shared VaultLink primitive when an
 * `onVaultLinkPreview` callback is provided. This is the central place that
 * turns vault references into clickable preview affordances — keep it here
 * instead of duplicating across surfaces.
 */

import { lazy, Suspense, useEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { vaultRawUrl } from "../api";
import {
  VaultLink,
  asVaultPath,
  linkifyVaultPaths as linkifyVaultPathsFn,
  useVaultLinkPreviewFromContext,
  vaultUrlTransform,
} from "./vaultLink";

/**
 * Resolve a `vault://path` (or bare `vault:path`) URL to a streaming `/vault/raw`
 * URL the browser can actually load. Plain http(s) URLs and other schemes are
 * passed through unchanged. Returns `undefined` for missing input so it can
 * be spread into JSX `src`/`href` props safely.
 */
function rewriteVaultMediaUrl(src: string | undefined | null): string | undefined {
  if (!src) return undefined;
  const m = src.match(/^vault:\/\/(.+)$/i) ?? src.match(/^vault:(.+)$/i);
  if (!m) return src;
  return vaultRawUrl(m[1].replace(/^\/+/, ""));
}

// ChartBlock lazy-loaded on first nexus-chart fence.
const LazyChartBlock = lazy(() => import("./ChartBlock"));

// Mermaid is ~280 kB gzipped — lazy-loaded on first diagram.
let _mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  if (!_mermaidPromise) {
    _mermaidPromise = import("mermaid").then((m) => {
      const mermaid = m.default;
      mermaid.initialize({
        startOnLoad: false,
        theme: "neutral",
        securityLevel: "strict",
        fontFamily: "inherit",
      });
      return mermaid;
    });
  }
  return _mermaidPromise;
}

export function MermaidBlock({ code }: { code: string }) {
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

type Props = {
  children: string;
  components?: ComponentProps<typeof ReactMarkdown>["components"];
  urlTransform?: ComponentProps<typeof ReactMarkdown>["urlTransform"];
  /** When set, vault links route to this preview handler instead of opening
   *  in a new tab. Without it, vault links still render as a styled link but
   *  the preview button is disabled. */
  onVaultLinkPreview?: (path: string) => void;
  /** Auto-wrap bare vault paths like `notes/foo.md` as proper markdown links
   *  before rendering. Off by default — useful for chat/agent output where
   *  unwrapped paths are common, off for generic markdown bodies. */
  linkifyVaultPaths?: boolean;
};

export default function MarkdownView({
  children,
  components,
  urlTransform,
  onVaultLinkPreview,
  linkifyVaultPaths,
}: Props) {
  const ctxPreview = useVaultLinkPreviewFromContext();
  const previewHandler = onVaultLinkPreview ?? ctxPreview ?? undefined;
  const processed = useMemo(
    () => (linkifyVaultPaths ? linkifyVaultPathsFn(children) : children),
    [children, linkifyVaultPaths],
  );
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={urlTransform ?? vaultUrlTransform}
      components={{
        img: ({ src, alt, ...rest }) => {
          const url = rewriteVaultMediaUrl(typeof src === "string" ? src : undefined);
          return <img src={url} alt={alt ?? ""} {...rest} />;
        },
        code: ({ className, children: codeChildren, ...rest }) => {
          const match = /language-([\w-]+)/.exec(className || "");
          const lang = match?.[1];
          const raw = String(codeChildren ?? "");
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
          return <code className={className} {...rest}>{codeChildren}</code>;
        },
        a: ({ href, children: linkChildren, ...rest }) => {
          const vaultPath = asVaultPath(href ?? "");
          if (vaultPath) {
            return (
              <VaultLink path={vaultPath} onPreview={previewHandler}>
                {linkChildren}
              </VaultLink>
            );
          }
          if (!href) {
            return <span {...rest}>{linkChildren}</span>;
          }
          return (
            <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
              {linkChildren}
            </a>
          );
        },
        ...components,
      }}
    >
      {processed}
    </ReactMarkdown>
  );
}
