import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FieldSchema } from "../../types/form";
import { resolveRefPath, fetchTableCached, deriveLabelInfo, suggestNextPk, invalidateTableCache } from "../datatable/refOptions";
import type { DataTable } from "../../api/datatable";
import { addVaultDataTableRow } from "../../api/datatable";
import "./Combobox.css";

interface Props {
  field: FieldSchema;
  hostPath: string;
  onSelect: (id: string) => void;
  onClose: () => void;
}

export default function RefSearchPopup({ field, hostPath, onSelect, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [table, setTable] = useState<DataTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const [showAddForm, setShowAddForm] = useState(false);
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [addValues, setAddValues] = useState<Record<string, unknown>>({});
  const inputRef = useRef<HTMLInputElement>(null);
  const tableRef = useRef<HTMLTableSectionElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const targetPath = resolveRefPath(hostPath, field.target_table ?? "");

  useEffect(() => { inputRef.current?.focus(); }, []);

  useEffect(() => {
    if (!targetPath) { setError("No target table configured"); return; }
    let cancelled = false;
    fetchTableCached(targetPath)
      .then((tbl) => { if (!cancelled) setTable(tbl); })
      .catch((e) => { if (!cancelled) setError((e as Error).message ?? "Failed to load table"); });
    return () => { cancelled = true; };
  }, [targetPath]);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        if (showAddForm) { setShowAddForm(false); return; }
        onClose();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose, showAddForm]);

  useEffect(() => {
    if (highlightIdx >= 0 && tableRef.current) {
      const row = tableRef.current.children[highlightIdx] as HTMLElement;
      row?.scrollIntoView({ block: "nearest" });
    }
  }, [highlightIdx]);

  const columns = useMemo(() => {
    if (!table) return [];
    return table.schema.fields.filter((f) => (f.kind ?? "text") !== "formula");
  }, [table]);

  const { pkName } = useMemo(() => {
    if (!table) return { pkName: "_id" };
    const tableMeta = (table.schema as { table?: { primary_key?: string } }).table ?? null;
    return deriveLabelInfo(table.schema.fields, tableMeta);
  }, [table]);

  const filtered = useMemo(() => {
    if (!table) return [];
    if (!query.trim()) return table.rows;
    const q = query.toLowerCase();
    return table.rows.filter((row) =>
      columns.some((col) => {
        const val = row[col.name];
        return val !== undefined && val !== null && String(val).toLowerCase().includes(q);
      }),
    );
  }, [table, query, columns]);

  const handleSelect = useCallback(
    (row: Record<string, unknown>) => {
      const id = String(row[pkName] ?? row._id ?? "");
      if (id) onSelect(id);
    },
    [pkName, onSelect],
  );

  const handleOpenAdd = useCallback(() => {
    if (!table) return;
    const suggested = suggestNextPk(table.rows, pkName);
    setAddValues(suggested ? { [pkName]: suggested } : {});
    setAddError(null);
    setShowAddForm(true);
  }, [table, pkName]);

  const handleAddNew = useCallback(async () => {
    if (!targetPath || !table) return;
    const fields = table.schema.fields.filter((f) => (f.kind ?? "text") !== "formula");
    for (const f of fields) {
      if (f.required && !addValues[f.name]) {
        setAddError(`${f.label ?? f.name} is required`);
        return;
      }
    }
    setAddBusy(true);
    setAddError(null);
    try {
      const created = await addVaultDataTableRow(targetPath, addValues);
      invalidateTableCache(targetPath);
      const id = String(created[pkName] ?? created._id ?? "");
      onSelect(id || "");
      setShowAddForm(false);
      setTable(null);
      fetchTableCached(targetPath).then(setTable).catch(() => {});
    } catch (e) {
      setAddError((e as Error).message ?? "Failed to create row");
    } finally {
      setAddBusy(false);
    }
  }, [targetPath, table, addValues, pkName, onSelect]);

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIdx((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIdx((i) => Math.max(i - 1, -1));
    } else if (e.key === "Enter" && highlightIdx >= 0 && filtered[highlightIdx]) {
      e.preventDefault();
      handleSelect(filtered[highlightIdx]);
    }
  }

  const addFormFields = useMemo(() => {
    if (!table) return [];
    return table.schema.fields.filter((f) => (f.kind ?? "text") !== "formula");
  }, [table]);

  return (
    <>
      <div className="cbx-search-popup" ref={overlayRef} onClick={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}>
        <div className="cbx-search-modal">
          <div className="cbx-search-header">
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => { setQuery(e.target.value); setHighlightIdx(-1); }}
              onKeyDown={handleKeyDown}
              placeholder={`Search by ${columns.map((c) => c.label ?? c.name).join(", ")}...`}
            />
            {!showAddForm && table && (
              <button className="cbx-add-btn" onClick={handleOpenAdd} title="Add new row">+ New</button>
            )}
            <button className="cbx-search-close" onClick={onClose}>×</button>
          </div>
          <div className="cbx-search-body">
            {error && <div className="cbx-search-empty">{error}</div>}
            {!error && !table && <div className="cbx-search-loading">Loading table...</div>}
            {!error && table && filtered.length === 0 && !showAddForm && (
              <div className="cbx-search-empty">
                No matching rows
                <button className="cbx-inline-add" onClick={handleOpenAdd}>+ Add new</button>
              </div>
            )}
            {!error && table && filtered.length > 0 && (
              <table className="cbx-search-table">
                <thead>
                  <tr>
                    {columns.map((col) => (
                      <th key={col.name}>{col.label ?? col.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody ref={tableRef}>
                  {filtered.map((row, idx) => (
                    <tr
                      key={String(row[pkName] ?? row._id ?? idx)}
                      className={idx === highlightIdx ? "cbx-search-active" : ""}
                      onClick={() => handleSelect(row)}
                      onMouseEnter={() => setHighlightIdx(idx)}
                    >
                      {columns.map((col) => (
                        <td key={col.name}>{String(row[col.name] ?? "")}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
      {showAddForm && table && (
        <div className="cbx-add-overlay" onClick={(e) => {
          if (e.target === e.currentTarget) setShowAddForm(false);
        }}>
          <div className="cbx-add-modal">
            <div className="cbx-add-modal-title">
              Add to {table.schema?.title ?? targetPath}
            </div>
            <div className="cbx-add-fields">
              {addFormFields.map((f) => (
                <div key={f.name} className="cbx-add-field">
                  <label className="cbx-add-label">
                    {f.label ?? f.name}{f.required && <span className="cbx-add-required"> *</span>}
                  </label>
                  <input
                    className="cbx-add-input"
                    type={f.kind === "number" ? "number" : "text"}
                    placeholder={f.placeholder ?? ""}
                    value={String(addValues[f.name] ?? "")}
                    onChange={(e) => {
                      const v = f.kind === "number" && e.target.value !== "" ? parseFloat(e.target.value) : e.target.value;
                      setAddValues((prev) => ({ ...prev, [f.name]: v }));
                    }}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void handleAddNew(); } }}
                  />
                </div>
              ))}
            </div>
            {addError && <div className="cbx-add-error">{addError}</div>}
            <div className="cbx-add-actions">
              <button className="cbx-add-cancel" onClick={() => setShowAddForm(false)}>Cancel</button>
              <button
                className="cbx-add-submit"
                onClick={() => void handleAddNew()}
                disabled={addBusy}
              >
                {addBusy ? "Adding..." : "Add & select"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
