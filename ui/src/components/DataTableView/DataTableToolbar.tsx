// DataTableView — header bar with search, views, column visibility, CSV, schema, and add-row actions.

import { useRef } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("datatable");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  return (
    <div className="dt-header">
      <span className="dt-title">{title ?? t("datatable:toolbar.defaultTitle")}</span>
      <div className="dt-header-actions">
        <input
          type="search"
          className="dt-search"
          placeholder={t("datatable:toolbar.searchPlaceholder")}
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
            <option value="">{t("datatable:toolbar.allRows")}</option>
            {views.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
          </select>
        )}
        <button className="vault-pill" onClick={onSaveView} title={t("datatable:toolbar.saveViewTitle")}>
          {t("datatable:toolbar.saveView")}
        </button>
        {activeView && (
          <button
            className="dt-action-btn dt-action-btn--delete"
            onClick={onDeleteView}
            title={t("datatable:toolbar.deleteViewTitle")}
          >
            {t("datatable:toolbar.deleteView")}
          </button>
        )}
        <ColumnVisibilityMenu fields={fields} hidden={hidden} onToggle={onToggleHidden} />
        <button className="vault-pill" onClick={onExportCSV} title={t("datatable:toolbar.exportTitle")}>
          {t("datatable:toolbar.exportButton")}
        </button>
        <button
          className="vault-pill"
          onClick={() => fileInputRef.current?.click()}
          title={t("datatable:toolbar.importTitle")}
        >
          {t("datatable:toolbar.importButton")}
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
        <button className="vault-pill" onClick={onOpenSchema} title={t("datatable:toolbar.schemaTitle")}>
          {t("datatable:toolbar.schemaButton")}
        </button>
        <button className="vault-pill" onClick={onAddRow}>
          {t("datatable:toolbar.addRow")}
        </button>
      </div>
    </div>
  );
}
