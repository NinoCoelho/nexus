// Sub-component for KnowledgeView: source filter (file/folder) bar.

import { getVaultTree } from "../../api";

interface SourceFilterBarProps {
  sourceFilter: "none" | "file" | "folder";
  sourcePath: string;
  sourceSuggestions: string[];
  showSourceSuggestions: boolean;
  onFilterModeChange: (mode: "none" | "file" | "folder") => void;
  onPathChange: (path: string) => void;
  onSuggestionsChange: (suggestions: string[]) => void;
  onShowSuggestionsChange: (show: boolean) => void;
  onApply: (mode: "file" | "folder", path: string) => void;
  onClear: () => void;
}

export function SourceFilterBar({
  sourceFilter,
  sourcePath,
  sourceSuggestions,
  showSourceSuggestions,
  onFilterModeChange,
  onPathChange,
  onSuggestionsChange,
  onShowSuggestionsChange,
  onApply,
  onClear,
}: SourceFilterBarProps) {
  return (
    <div className="kv-source-filter">
      <select
        className="kv-source-filter-select"
        value={sourceFilter}
        onChange={(e) => {
          const v = e.target.value as "none" | "file" | "folder";
          if (v === "none") onClear();
          else { onFilterModeChange(v); onPathChange(""); }
        }}
      >
        <option value="none">No source filter</option>
        <option value="file">Filter by file</option>
        <option value="folder">Filter by folder</option>
      </select>
      {sourceFilter !== "none" && (
        <div className="kv-source-input-wrap">
          <input
            className="kv-source-input"
            type="text"
            placeholder={sourceFilter === "file" ? "path/to/file.md" : "folder/"}
            value={sourcePath}
            onChange={(e) => {
              onPathChange(e.target.value);
              onShowSuggestionsChange(true);
              const v = e.target.value.toLowerCase();
              if (v.length >= 1) {
                getVaultTree().then((entries) => {
                  const paths = entries
                    .filter(e => {
                      if (sourceFilter === "file") return e.type === "file";
                      return e.type === "dir";
                    })
                    .map(e => e.path)
                    .filter(p => p.toLowerCase().includes(v))
                    .slice(0, 12);
                  onSuggestionsChange(paths);
                });
              } else {
                onSuggestionsChange([]);
              }
            }}
            onFocus={() => { if (sourcePath.length >= 1) onShowSuggestionsChange(true); }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                onShowSuggestionsChange(false);
                void onApply(sourceFilter, sourcePath);
              }
            }}
          />
          <button className="kv-source-go" onClick={() => void onApply(sourceFilter, sourcePath)}>Go</button>
          <button className="kv-source-clear" onClick={onClear}>&times;</button>
          {showSourceSuggestions && sourceSuggestions.length > 0 && (
            <div className="kv-source-suggestions">
              {sourceSuggestions.map(s => (
                <button
                  key={s}
                  className="kv-source-suggestion"
                  onClick={() => {
                    onPathChange(s);
                    onShowSuggestionsChange(false);
                    void onApply(sourceFilter, s);
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
