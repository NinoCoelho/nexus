// Active filter chips row — shows removable chips for type/source/query filters.

interface FilterChipsProps {
  typeFilter: string | null;
  sourceFilter: "none" | "file" | "folder";
  sourcePath: string;
  queryText: string;
  onClearType: () => void;
  onClearSource: () => void;
  onClearQuery: () => void;
}

export function FilterChips({
  typeFilter,
  sourceFilter,
  sourcePath,
  queryText,
  onClearType,
  onClearSource,
  onClearQuery,
}: FilterChipsProps) {
  const hasAny = typeFilter !== null || (sourceFilter !== "none" && sourcePath) || queryText.trim();
  if (!hasAny) return null;

  return (
    <div className="kv-filter-chips">
      {typeFilter !== null && (
        <span className="kv-filter-chip">
          Type: {typeFilter}
          <button className="kv-filter-chip-close" onClick={onClearType} title="Remove type filter">&times;</button>
        </span>
      )}
      {sourceFilter !== "none" && sourcePath && (
        <span className="kv-filter-chip">
          {sourceFilter === "file" ? "File" : "Folder"}: {sourcePath.split("/").pop() ?? sourcePath}
          <button className="kv-filter-chip-close" onClick={onClearSource} title="Remove source filter">&times;</button>
        </span>
      )}
      {queryText.trim() && (
        <span className="kv-filter-chip">
          Query: &ldquo;{queryText.length > 30 ? queryText.slice(0, 29) + "…" : queryText}&rdquo;
          <button className="kv-filter-chip-close" onClick={onClearQuery} title="Clear query">&times;</button>
        </span>
      )}
    </div>
  );
}
