// StepDetailModal — vault-specific result renderers (read, list, search, tags, backlinks).

import MarkdownView from "../MarkdownView";
import type { VaultEntry, SearchMatch } from "./types";

export function VaultReadResult({ content }: { content: string }) {
  return (
    <div className="sdm-result-markdown">
      <MarkdownView>{content}</MarkdownView>
    </div>
  );
}

export function VaultListResult({ entries }: { entries: VaultEntry[] }) {
  if (!entries.length) return <div className="sdm-empty">No files found</div>;
  return (
    <ul className="sdm-file-list">
      {entries.map((e) => {
        const parts = e.path.split("/");
        const name = parts.pop() ?? e.path;
        const dir = parts.join("/");
        const isDir = e.type === "dir";
        return (
          <li key={e.path} className="sdm-file-item">
            <span className="sdm-file-icon">
              {isDir ? (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5H12A1.5 1.5 0 0 1 13.5 6v5A1.5 1.5 0 0 1 12 12.5H4A1.5 1.5 0 0 1 2.5 11z" />
                </svg>
              ) : (
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 2.5a1 1 0 0 1 1-1h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
                  <polyline points="9 1.5 9 5 12 5" />
                </svg>
              )}
            </span>
            <span className="sdm-file-name">{name}</span>
            {dir && <span className="sdm-file-dir">{dir}/</span>}
          </li>
        );
      })}
    </ul>
  );
}

export function VaultSearchResult({ results }: { results: SearchMatch[] }) {
  if (!results.length) return <div className="sdm-empty">No matches found</div>;
  return (
    <div className="sdm-search-results">
      {results.map((r, i) => {
        const parts = r.path.split("/");
        const name = parts.pop() ?? r.path;
        const dir = parts.join("/");
        // Convert <mark>…</mark> to bold markdown
        const snippet = r.snippet.replace(/<mark>/g, "**").replace(/<\/mark>/g, "**");
        return (
          <div key={i} className="sdm-search-match">
            <div className="sdm-match-header">
              <span className="sdm-match-file">{name}</span>
              {dir && <span className="sdm-match-path">{dir}/</span>}
            </div>
            <div className="sdm-match-snippet">
              <MarkdownView>{snippet}</MarkdownView>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const TagIcon = () => (
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <line x1="5" y1="3" x2="5" y2="13" />
    <line x1="11" y1="3" x2="11" y2="13" />
    <line x1="2.5" y1="6" x2="13.5" y2="6" />
    <line x1="2.5" y1="10" x2="13.5" y2="10" />
  </svg>
);

const LinkIcon = () => (
  <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6.5 9.5a3.536 3.536 0 0 0 5 0l2-2a3.536 3.536 0 0 0-5-5L7 4" />
    <path d="M9.5 6.5a3.536 3.536 0 0 0-5 0l-2 2a3.536 3.536 0 0 0 5 5L9 12" />
  </svg>
);

export function VaultTagsResult({ items }: { items: string[] }) {
  return (
    <ul className="sdm-file-list">
      {items.map((item, i) => (
        <li key={i} className="sdm-file-item">
          <span className="sdm-file-icon"><TagIcon /></span>
          <span className="sdm-file-name">{item}</span>
        </li>
      ))}
    </ul>
  );
}

export function VaultBacklinksResult({ links }: { links: string[] }) {
  if (!links.length) return <div className="sdm-empty">No backlinks</div>;
  return (
    <ul className="sdm-file-list">
      {links.map((link, i) => {
        const name = typeof link === "string" ? link.split("/").pop() ?? link : JSON.stringify(link);
        const dir = typeof link === "string" ? link.split("/").slice(0, -1).join("/") : "";
        return (
          <li key={i} className="sdm-file-item">
            <span className="sdm-file-icon"><LinkIcon /></span>
            <span className="sdm-file-name">{name}</span>
            {dir && <span className="sdm-file-dir">{dir}/</span>}
          </li>
        );
      })}
    </ul>
  );
}
