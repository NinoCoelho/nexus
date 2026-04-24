/**
 * MarkdownView — safe markdown renderer with GFM + Mermaid support.
 *
 * Renders markdown content using react-markdown with remark-gfm (tables,
 * strikethrough, task lists). Code blocks with language "mermaid" are
 * lazy-loaded and rendered as SVG diagrams via the mermaid library.
 *
 * Vault links (vault://path) are intercepted and rendered as clickable
 * links that trigger onOpenInVault in the parent component.
 */

import { useEffect, useRef, useState, type ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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
};

export default function MarkdownView({ children, components, urlTransform }: Props) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={urlTransform}
      components={{
        code: ({ className, children: codeChildren, ...rest }) => {
          const match = /language-(\w+)/.exec(className || "");
          const lang = match?.[1];
          const raw = String(codeChildren ?? "");
          if (lang === "mermaid" && raw.includes("\n")) {
            return <MermaidBlock code={raw.replace(/\n$/, "")} />;
          }
          return <code className={className} {...rest}>{codeChildren}</code>;
        },
        a: ({ href, children: linkChildren, ...rest }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
            {linkChildren}
          </a>
        ),
        ...components,
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
