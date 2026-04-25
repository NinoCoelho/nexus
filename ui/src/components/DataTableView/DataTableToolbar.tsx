// DataTableView — header bar with search, views, column visibility, CSV, schema, and add-row actions.

import { useRef } from "react";
import type { DataTableView } from "../../api";
import type { FieldSchema } from "../../types/form";
import ColumnVisibilityMenu from "./ColumnVisibilityMenu";

interface Props {
  title: string | undefined;
  fields: FieldSchema[];
  views: DataTableView[];
  search: string;
  activeView: string;
  hidden: Set<string>;
  onSearchChange: (v: string) => void;
  onApplyView: (v: DataTableView) => void;
  onClearView: () => void;
  onSaveView: () => void;
  onDeleteView: () => void;
  onToggleHidden: (name: string) => void;
  onExportCSV: () => void;
  onImportCSV: (file: File) => void;
  onOpenSchema: () => void;
  onAddRow: () => void;
}

export default function DataTableToolbar({
  title, fields, views, search, activeView, hidden,
  onSearchChange, onApplyView, onClearView, onSaveView, onDeleteView,
  onToggleHidden, onExportCSV, onImportCSV, onOpenSchema, onAddRow,
}: Props) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="dt-header">
      <span className="dt-title">{title ?? "Data Table"}</span>
      <div className="dt-header-actions">
        <input
          type="search"
          className="dt-search"
          placeholder="Search…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
        />
        {views.length > 0 && (
          <select
            className="dt-search dt-view-select"
            value={activeView}
            onChange={(e) => {
              const v = views.find((x) => x.name === e.target.value);
              if (v) onApplyView(v);
              else onClearView();
            }}
          >
            <option value="">All rows</option>
            {views.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
          </select>
        )}
        <button className="vault-pill" onClick={onSaveView} title="Save current filter/sort as a view">
          Save view
        </button>
        {activeView && (
          <button
            className="dt-action-btn dt-action-btn--delete"
            onClick={onDeleteView}
            title="Delete the active view"
          >
            Del view
          </button>
        )}
        <ColumnVisibilityMenu fields={fields} hidden={hidden} onToggle={onToggleHidden} />
        <button className="vault-pill" onClick={onExportCSV} title="Download as CSV">
          Export
        </button>
        <button
          className="vault-pill"
          onClick={() => fileInputRef.current?.click()}
          title="Import rows from CSV"
        >
          Import
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onImportCSV(f);
            e.target.value = "";
          }}
        />
        <button className="vault-pill" onClick={onOpenSchema} title="Edit schema">
          Schema
        </button>
        <button className="vault-pill" onClick={onAddRow}>
          + Add Row
        </button>
      </div>
    </div>
  );
}
