// Sub-component for VaultTreePanel: search results and tag filter results.

import type { VaultSearchResult, VaultTagCount } from "../../api";
import { SnippetText } from "./SnippetText";

interface SearchResultsPanelProps {
  searchQuery: string;
  searchResults: VaultSearchResult[];
  tags: VaultTagCount[];
  activeTag: string | null;
  tagFiles: string[];
  selectedPath: string | null;
  onSelectPath: (path: string) => void;
  onTagClick: (tag: string) => void;
  onClearSearch: () => void;
}

export function SearchResultsPanel({
  searchQuery,
  searchResults,
  tags,
  activeTag,
  tagFiles,
  selectedPath,
  onSelectPath,
  onTagClick,
  onClearSearch,
}: SearchResultsPanelProps) {
  if (searchQuery) {
    const q = searchQuery.trim().toLowerCase().replace(/^#/, "");
    const suggestions = q
      ? tags.filter((t) => t.tag.toLowerCase().includes(q)).slice(0, 8)
      : [];

    return (
      <div className="vault-search-results">
        {suggestions.length > 0 && (
          <div className="vault-tag-suggestions">
            {suggestions.map((t) => (
              <button
                key={t.tag}
                className={`vault-tag-pill${activeTag === t.tag ? " vault-tag-pill--active" : ""}`}
                onClick={() => {
                  onClearSearch();
                  onTagClick(t.tag);
                }}
                title={`${t.count} file${t.count !== 1 ? "s" : ""}`}
              >
                #{t.tag}
              </button>
            ))}
          </div>
        )}
        {searchResults.length === 0 && (
          <div className="vault-tree-empty">No results</div>
        )}
        {searchResults.map((r: VaultSearchResult) => (
          <button
            key={r.path}
            className={`vault-search-result${r.path === selectedPath ? " vault-tree-row--active" : ""}`}
            onClick={() => { onSelectPath(r.path); }}
          >
            <span className="vault-search-result-path">{r.path}</span>
            <span className="vault-search-snippet"><SnippetText snippet={r.snippet} /></span>
          </button>
        ))}
      </div>
    );
  }

  if (activeTag) {
    return (
      <div className="vault-search-results">
        {tagFiles.length === 0 && (
          <div className="vault-tree-empty">No files with tag #{activeTag}</div>
        )}
        {tagFiles.map((p) => (
          <button
            key={p}
            className={`vault-search-result${p === selectedPath ? " vault-tree-row--active" : ""}`}
            onClick={() => { onSelectPath(p); }}
          >
            <span className="vault-search-result-path">{p}</span>
          </button>
        ))}
      </div>
    );
  }

  return null;
}
