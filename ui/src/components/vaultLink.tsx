/**
 * Shared vault-link primitives used by every markdown surface.
 *
 * Anywhere user/agent-authored markdown is rendered (chat, vault preview,
 * step results, data chat bubbles, kanban activity), a link whose target is
 * a vault file should render with the same affordances: an inline preview
 * button, a "new tab" anchor, and optionally an inline preview of non-
 * markdown attachments. Centralizing those here keeps every surface
 * consistent — see MarkdownView for the wiring.
 */

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { vaultRawUrl } from "../api";
import { classify } from "../fileTypes";
import ChatInlineFilePreview from "./ChatInlineFilePreview";
import VaultFilePreview from "./VaultFilePreview";

/** Best-effort detector — returns a normalized vault path or null. */
export function asVaultPath(href: string): string | null {
  if (!href) return null;
  const m = href.match(/^vault:\/\/(.+)$/i) ?? href.match(/^vault:(.+)$/i);
  if (m) return m[1].replace(/^\/+/, "");
  if (/^https?:\/\//i.test(href)) return null;
  if (href.startsWith("#") || href.startsWith("mailto:")) return null;
  if (/\.mdx?$/i.test(href)) return href.replace(/^\/+/, "");
  return null;
}

/** Auto-wrap bare vault paths (e.g. `notes/foo.md`) as proper markdown links. */
export function linkifyVaultPaths(content: string): string {
  if (!content) return "";
  return content.replace(
    /(^|[\s("])([a-z0-9][a-z0-9_\-./]*\/[a-z0-9][a-z0-9_\-. ]*\.mdx?)(?=$|[\s.,;:)!"])/gi,
    (_match, pre, path) => `${pre}[${path}](vault://${path})`,
  );
}

/** urlTransform helper: keep `vault:` schemes intact; drop dangerous ones. */
export function vaultUrlTransform(url: string): string {
  if (/^vault:/i.test(url)) return url;
  if (/^(?:javascript|data|vbscript):/i.test(url)) return "";
  return url;
}

interface VaultLinkProps {
  path: string;
  children?: React.ReactNode;
  /** Called when the user clicks the preview affordance. */
  onPreview?: (path: string) => void;
}

/** Inline vault link: preview button + new-tab anchor + optional inline preview. */
export function VaultLink({ path, children, onPreview }: VaultLinkProps) {
  const ctxPreview = useVaultLinkPreviewFromContext();
  const previewHandler = onPreview ?? ctxPreview ?? undefined;
  const kind = classify(path).kind;
  const newTabHref =
    kind === "markdown"
      ? `${window.location.pathname}?view=vault&path=${encodeURIComponent(path)}`
      : vaultRawUrl(path);
  return (
    <span className="vault-inline-link-wrap">
      <button
        type="button"
        className="vault-inline-link"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          previewHandler?.(path);
        }}
        disabled={!previewHandler}
        title={previewHandler ? `Preview ${path}` : path}
      >
        <svg
          width="11"
          height="11"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
          <polyline points="9 1.5 9 5 12 5" />
        </svg>
        {children ?? path}
      </button>
      <a
        href={newTabHref}
        target="_blank"
        rel="noopener noreferrer"
        className="vault-inline-link-newtab"
        title="Open in a new tab"
        onClick={(e) => e.stopPropagation()}
      >
        <svg
          width="10"
          height="10"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M8 3H3v10h10V8" />
          <polyline points="9 2 14 2 14 7" />
          <line x1="14" y1="2" x2="8" y2="8" />
        </svg>
      </a>
      {kind !== "markdown" && <ChatInlineFilePreview path={path} />}
    </span>
  );
}

/**
 * useVaultLinkPreview — shorthand for surfaces that don't already manage a
 * VaultFilePreview modal. Returns an `onPreview` handler to thread into
 * MarkdownView and a `modal` element to render alongside.
 *
 * The returned handler is also published via VaultLinkPreviewContext so
 * descendants (e.g. nested ResultRenderers) can pick it up without prop-
 * drilling. Pass `onOpenInVault` so the modal's "Open in Vault view" button
 * routes to the host app's vault navigation (App.tsx). When omitted, that
 * affordance is hidden and the user can still preview + open in a new tab.
 */
export function useVaultLinkPreview(onOpenInVault?: (path: string) => void): {
  onPreview: (path: string) => void;
  modal: ReactNode;
} {
  const [path, setPath] = useState<string | null>(null);
  const onPreview = useCallback((p: string) => setPath(p), []);
  const modal = (
    <VaultFilePreview
      path={path}
      onClose={() => setPath(null)}
      onOpenInVault={onOpenInVault}
    />
  );
  return { onPreview, modal };
}

/** Context for components deep in the tree that render markdown but don't
 *  want to manage their own preview modal — e.g. nested step result renderers. */
const VaultLinkPreviewContext = createContext<((path: string) => void) | null>(null);

export function VaultLinkPreviewProvider({
  onPreview,
  children,
}: {
  onPreview: (path: string) => void;
  children: ReactNode;
}) {
  return (
    <VaultLinkPreviewContext.Provider value={onPreview}>
      {children}
    </VaultLinkPreviewContext.Provider>
  );
}

/** Returns the ambient vault-preview handler if any ancestor provided one. */
export function useVaultLinkPreviewFromContext(): ((path: string) => void) | null {
  return useContext(VaultLinkPreviewContext);
}
