import { useCallback, useEffect, useMemo, useState } from "react";
import {
  addVaultCsvRow,
  deleteVaultCsvRow,
  getVaultCsv,
  setVaultCsvSchema,
  updateVaultCsvCell,
  type CsvPage,
} from "../../api/csv";
import "./CsvEditorView.css";

interface CsvEditorViewProps {
  path: string;
}

const PAGE_SIZE = 50;

export default function CsvEditorView({ path }: CsvEditorViewProps) {
  const [page, setPage] = useState<CsvPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [schemaOpen, setSchemaOpen] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const p = await getVaultCsv(path, {
        offset,
        limit: PAGE_SIZE,
        sort: sort ?? undefined,
        sort_dir: sortDir,
      });
      setPage(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [path, offset, sort, sortDir]);

  useEffect(() => {
    void load();
  }, [load]);

  const onSort = (col: string) => {
    if (sort === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSort(col);
      setSortDir("asc");
    }
    setOffset(0);
  };

  const onCellEdit = async (rowIndex: number, column: string, value: string) => {
    if (!page) return;
    setBusy(true);
    try {
      await updateVaultCsvCell(path, offset + rowIndex, column, value);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onAddRow = async () => {
    if (!page) return;
    setBusy(true);
    try {
      const blank: Record<string, string> = {};
      for (const c of page.columns) blank[c] = "";
      await addVaultCsvRow(path, blank);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteRow = async (rowIndex: number) => {
    setBusy(true);
    try {
      await deleteVaultCsvRow(path, offset + rowIndex);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const totalPages = useMemo(() => {
    if (!page) return 1;
    return Math.max(1, Math.ceil(page.total_rows / PAGE_SIZE));
  }, [page]);
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  if (error) return <div className="csv-error">{error}</div>;
  if (!page) return <div className="csv-loading">Loading…</div>;

  return (
    <div className="csv-editor">
      <div className="csv-toolbar">
        <span className="csv-meta">
          {page.total_rows.toLocaleString()} rows · {page.columns.length} columns
        </span>
        <div className="csv-toolbar-spacer" />
        <button className="csv-btn" disabled={busy} onClick={() => void onAddRow()}>
          + Row
        </button>
        <button className="csv-btn" onClick={() => setSchemaOpen((s) => !s)}>
          Schema
        </button>
      </div>
      {schemaOpen && (
        <SchemaPanel
          path={path}
          columns={page.columns}
          onClose={() => setSchemaOpen(false)}
          onSaved={() => {
            setSchemaOpen(false);
            void load();
          }}
        />
      )}
      <div className="csv-table-wrap">
        <table className="csv-table">
          <thead>
            <tr>
              {page.columns.map((c) => (
                <th key={c} onClick={() => onSort(c)} className="csv-th">
                  {c}
                  {sort === c && <span className="csv-sort">{sortDir === "asc" ? " ▲" : " ▼"}</span>}
                </th>
              ))}
              <th className="csv-th csv-th-actions" />
            </tr>
          </thead>
          <tbody>
            {page.rows.map((row, i) => (
              <tr key={offset + i}>
                {page.columns.map((c) => (
                  <td key={c} className="csv-td">
                    <input
                      className="csv-cell"
                      defaultValue={String(row[c] ?? "")}
                      disabled={busy}
                      onBlur={(e) => {
                        const next = e.target.value;
                        if (next !== String(row[c] ?? "")) void onCellEdit(i, c, next);
                      }}
                    />
                  </td>
                ))}
                <td className="csv-td csv-td-actions">
                  <button
                    className="csv-row-del"
                    title="Delete row"
                    disabled={busy}
                    onClick={() => void onDeleteRow(i)}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="csv-pager">
        <button
          className="csv-btn"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          Prev
        </button>
        <span className="csv-page-info">
          {currentPage} / {totalPages}
        </span>
        <button
          className="csv-btn"
          disabled={offset + PAGE_SIZE >= page.total_rows}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </button>
      </div>
    </div>
  );
}

interface SchemaPanelProps {
  path: string;
  columns: string[];
  onClose: () => void;
  onSaved: () => void;
}

function SchemaPanel({ path, columns, onClose, onSaved }: SchemaPanelProps) {
  const [draft, setDraft] = useState<{ name: string; rename_from?: string }[]>(
    () => columns.map((c) => ({ name: c, rename_from: c })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await setVaultCsvSchema(path, draft);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const move = (i: number, delta: number) => {
    const j = i + delta;
    if (j < 0 || j >= draft.length) return;
    const next = draft.slice();
    [next[i], next[j]] = [next[j], next[i]];
    setDraft(next);
  };

  return (
    <div className="csv-schema-panel">
      <div className="csv-schema-header">
        <strong>Edit schema</strong>
        <button className="csv-btn" onClick={onClose}>Cancel</button>
      </div>
      {error && <div className="csv-error">{error}</div>}
      <ul className="csv-schema-list">
        {draft.map((c, i) => (
          <li key={i} className="csv-schema-row">
            <input
              className="csv-cell"
              value={c.name}
              onChange={(e) =>
                setDraft((d) => d.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)))
              }
            />
            <span className="csv-schema-from">
              {c.rename_from ? `← ${c.rename_from}` : "(new)"}
            </span>
            <button className="csv-row-del" onClick={() => move(i, -1)} disabled={i === 0}>↑</button>
            <button className="csv-row-del" onClick={() => move(i, 1)} disabled={i === draft.length - 1}>↓</button>
            <button
              className="csv-row-del"
              onClick={() => setDraft((d) => d.filter((_, k) => k !== i))}
              title="Drop column"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="csv-schema-actions">
        <button
          className="csv-btn"
          onClick={() => setDraft((d) => [...d, { name: `column_${d.length + 1}` }])}
        >
          + Column
        </button>
        <button className="csv-btn csv-btn-primary" disabled={busy} onClick={() => void save()}>
          Save schema
        </button>
      </div>
    </div>
  );
}
