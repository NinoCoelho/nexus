import { useEffect, useMemo, useState } from "react";
import {
  deleteVaultDataTableRow,
  getRelatedRows,
  updateVaultDataTableRow,
  type OneToManyGroup,
  type RelatedRows,
} from "../../api/datatable";
import {
  deriveLabelInfo,
  fetchTableCached,
  resolveRefPath,
  summarizeRow,
  suggestNextPk,
} from "../datatable/refOptions";
import type { FieldSchema } from "../../types/form";
import FormRenderer from "../FormRenderer";
import { useToast } from "../../toast/ToastProvider";
import type { DataTable } from "../../api/datatable";

type RefLookup = Map<string, string>;

function useResolvedRefs(
  fields: FieldSchema[],
  hostPath: string,
): RefLookup {
  const [lookup, setLookup] = useState<RefLookup>(new Map());

  const refFields = useMemo(
    () => fields.filter((f) => f.kind === "ref" && f.target_table),
    [fields],
  );

  useEffect(() => {
    if (refFields.length === 0) return;
    let cancelled = false;
    const next = new Map<string, string>();

    void (async () => {
      const targetTables = new Map<string, { pkName: string; labelField: FieldSchema | null; rows: Record<string, unknown>[] }>();
      for (const f of refFields) {
        const absPath = resolveRefPath(hostPath, f.target_table!);
        if (targetTables.has(absPath)) continue;
        try {
          const tbl = await fetchTableCached(absPath);
          const info = deriveLabelInfo(tbl.schema.fields, tbl.schema.table);
          targetTables.set(absPath, { pkName: info.pkName, labelField: info.labelField, rows: tbl.rows });
        } catch { /* skip */ }
      }
      if (cancelled) return;
      for (const f of refFields) {
        const absPath = resolveRefPath(hostPath, f.target_table!);
        const target = targetTables.get(absPath);
        if (!target) continue;
        for (const r of target.rows) {
          const id = String(r[target.pkName] ?? r._id ?? "");
          if (id) next.set(`${f.name}::${id}`, summarizeRow(r, target.pkName, target.labelField));
        }
      }
      if (!cancelled) setLookup(next);
    })();

    return () => { cancelled = true; };
  }, [refFields, hostPath]);

  return lookup;
}

interface Props {
  path: string;
  rowId: string;
  row: Record<string, unknown>;
  table: DataTable;
  onClose: () => void;
  onOpenTable?: (path: string) => void;
  onRefresh: () => void;
  refreshKey?: number;
}

export default function RowDetailDrawer({
  path,
  rowId,
  row,
  table,
  onClose,
  onOpenTable,
  onRefresh,
  refreshKey,
}: Props) {
  const toast = useToast();
  const fields: FieldSchema[] = table.schema?.fields ?? [];
  const { pkName } = deriveLabelInfo(fields, table.schema.table);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [editValues, setEditValues] = useState<Record<string, unknown>>({ ...row });
  const [saving, setSaving] = useState(false);

  const refLookup = useResolvedRefs(fields, path);

  const [related, setRelated] = useState<RelatedRows | null>(null);
  const [groupSchemas, setGroupSchemas] = useState<Record<string, { fields: FieldSchema[]; pkName: string; allRows: Record<string, unknown>[] }>>({});

  useEffect(() => {
    setEditValues({ ...row });
    setMode("view");
  }, [row]);

  useEffect(() => {
    let cancelled = false;
    getRelatedRows(path, rowId).then((data) => {
      if (cancelled) return;
      setRelated(data);

      void (async () => {
        const schemas: typeof groupSchemas = {};
        for (const g of data.one_to_many) {
          if (cancelled) return;
          try {
            const tbl = await fetchTableCached(g.from_table);
            const info = deriveLabelInfo(tbl.schema.fields, tbl.schema.table);
            schemas[g.from_table] = { fields: tbl.schema.fields, pkName: info.pkName, allRows: tbl.rows };
          } catch { /* skip */ }
        }
        for (const g of data.many_to_many) {
          if (cancelled) return;
          try {
            const tbl = await fetchTableCached(g.target_table);
            const info = deriveLabelInfo(tbl.schema.fields, tbl.schema.table);
            schemas[g.target_table] = { fields: tbl.schema.fields, pkName: info.pkName, allRows: tbl.rows };
          } catch { /* skip */ }
        }
        if (!cancelled) setGroupSchemas(schemas);
      })();
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [path, rowId, refreshKey]);

  const handleSave = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await updateVaultDataTableRow(path, String(row._id ?? ""), editValues);
      toast.success("Row updated");
      setMode("view");
      onRefresh();
    } catch (e) {
      toast.error("Update failed", { detail: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = () => {
    void (async () => {
      try {
        await deleteVaultDataTableRow(path, String(row._id ?? ""));
        toast.success("Row deleted");
        onClose();
        onRefresh();
      } catch (e) {
        toast.error("Delete failed", { detail: (e as Error).message });
      }
    })();
  };

  const displayValue = (val: unknown, field: FieldSchema): string => {
    if (val == null || val === "") return "";
    if (field.kind === "ref") {
      const resolved = refLookup.get(`${field.name}::${val}`);
      return resolved ?? String(val);
    }
    if (typeof val === "object") return JSON.stringify(val);
    if (field.kind === "boolean") return val ? "Yes" : "No";
    return String(val);
  };

  const visibleFields = fields.filter((f) => f.kind !== "formula" && f.kind !== "rollup");

  return (
    <div className="dt-drawer">
      <div className="dt-drawer-header">
        <h3 className="dt-drawer-title">
          {table.schema?.title ?? "Row"}: {String(row[pkName] ?? row._id ?? "")}
        </h3>
        <button className="dt-drawer-close" onClick={onClose}>
          &times;
        </button>
      </div>

      <div className="dt-drawer-body">
        {mode === "view" ? (
          <div className="dt-drawer-fields">
            {visibleFields.map((f) => (
              <div key={f.name} className="dt-drawer-field">
                <span className="dt-drawer-field-label">{f.label ?? f.name}</span>
                <span className="dt-drawer-field-value">{displayValue(row[f.name], f) || "—"}</span>
              </div>
            ))}
            <div className="dt-drawer-actions">
              <button className="dt-action-btn" onClick={() => { setEditValues({ ...row }); setMode("edit"); }}>
                Edit
              </button>
              <button className="dt-action-btn dt-action-btn--delete" onClick={handleDelete}>
                Delete
              </button>
            </div>
          </div>
        ) : (
          <div className="dt-drawer-form">
            <FormRenderer
              hostPath={path}
              fields={fields.filter((f) => f.kind !== "formula")}
              initialValues={editValues}
              onSubmit={handleSave}
              onCancel={() => setMode("view")}
              submitLabel={saving ? "Saving..." : "Save"}
            />
          </div>
        )}

        {related && (
          <div className="dt-drawer-related">
            <h4 className="dt-drawer-related-heading">Related Records</h4>
            {related.one_to_many.length === 0 && related.many_to_many.length === 0 && (
              <div className="dt-drawer-related-empty">No related records</div>
            )}
            {related.one_to_many.map((group) => (
              <OneToManyGroupSection
                key={group.from_table}
                group={group}
                schema={groupSchemas[group.from_table]}
                parentRowId={rowId}
                onRefresh={onRefresh}
                onOpenTable={onOpenTable}
              />
            ))}
            {related.many_to_many.map((group) => (
              <ManyToManyGroupSection
                key={group.target_table}
                group={group}
                schema={groupSchemas[group.target_table]}
                onOpenTable={onOpenTable}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const MAX_COLS = 5;

function pickDisplayCols(
  schemaFields: FieldSchema[],
  fkField: string,
): FieldSchema[] {
  const skip = new Set(["formula", "rollup", "textarea"]);
  const eligible = schemaFields.filter(
    (f) => f.name !== fkField && f.name !== "_id" && !skip.has(f.kind ?? "text"),
  );
  const refs = eligible.filter((f) => f.kind === "ref");
  const others = eligible.filter((f) => f.kind !== "ref");
  const ordered: FieldSchema[] = [];
  for (const r of refs) {
    if (ordered.length >= MAX_COLS) break;
    ordered.push(r);
  }
  for (const o of others) {
    if (ordered.length >= MAX_COLS) break;
    ordered.push(o);
  }
  return ordered;
}

function useGroupRefLookup(
  groupTable: string,
  displayCols: FieldSchema[],
): RefLookup {
  const [lookup, setLookup] = useState<RefLookup>(new Map());
  useEffect(() => {
    const refFields = displayCols.filter((f) => f.kind === "ref" && f.target_table);
    if (refFields.length === 0) return;
    let cancelled = false;
    const next = new Map<string, string>();
    void (async () => {
      const targets = new Map<string, { pkName: string; labelField: FieldSchema | null; rows: Record<string, unknown>[] }>();
      for (const f of refFields) {
        const absPath = resolveRefPath(groupTable, f.target_table!);
        if (targets.has(absPath)) continue;
        try {
          const tbl = await fetchTableCached(absPath);
          const info = deriveLabelInfo(tbl.schema.fields, tbl.schema.table);
          targets.set(absPath, { pkName: info.pkName, labelField: info.labelField, rows: tbl.rows });
        } catch { /* skip */ }
      }
      if (cancelled) return;
      for (const f of refFields) {
        const absPath = resolveRefPath(groupTable, f.target_table!);
        const target = targets.get(absPath);
        if (!target) continue;
        for (const r of target.rows) {
          const id = String(r[target.pkName] ?? r._id ?? "");
          if (id) next.set(`${f.name}::${id}`, summarizeRow(r, target.pkName, target.labelField));
        }
      }
      if (!cancelled) setLookup(next);
    })();
    return () => { cancelled = true; };
  }, [groupTable, displayCols]);
  return lookup;
}

function OneToManyGroupSection({
  group,
  schema,
  parentRowId,
  onRefresh,
  onOpenTable,
}: {
  group: OneToManyGroup;
  schema?: { fields: FieldSchema[]; pkName: string; allRows: Record<string, unknown>[] };
  parentRowId: string;
  parentPath?: string;
  onRefresh: () => void;
  onOpenTable?: (path: string) => void;
}) {
  const toast = useToast();
  const [showAdd, setShowAdd] = useState(false);
  const rows = group.rows;
  const displayCols = useMemo(() => schema ? pickDisplayCols(schema.fields, group.field_name) : [], [schema, group.field_name]);
  const refLookup = useGroupRefLookup(group.from_table, displayCols);

  const renderCell = (val: unknown, field: FieldSchema): string => {
    if (val == null || val === "") return "";
    if (field.kind === "ref") {
      return refLookup.get(`${field.name}::${val}`) ?? String(val);
    }
    if (field.kind === "boolean") return val ? "✓" : "";
    if (Array.isArray(val)) return val.join(", ");
    return String(val);
  };

  const handleAddRow = async (values: Record<string, unknown>) => {
    try {
      const { addVaultDataTableRow } = await import("../../api/datatable");
      await addVaultDataTableRow(group.from_table, { ...values, [group.field_name]: parentRowId });
      toast.success("Row added");
      setShowAdd(false);
      onRefresh();
    } catch (e) {
      toast.error("Add failed", { detail: (e as Error).message });
    }
  };

  const handleDeleteRow = async (targetRowId: string) => {
    try {
      const { deleteVaultDataTableRow } = await import("../../api/datatable");
      await deleteVaultDataTableRow(group.from_table, targetRowId);
      toast.success("Row deleted");
      onRefresh();
    } catch (e) {
      toast.error("Delete failed", { detail: (e as Error).message });
    }
  };

  const addFields = schema
    ? schema.fields.filter((f) => f.name !== group.field_name && f.kind !== "formula" && f.kind !== "rollup")
    : [];
  const pkForAdd = schema ? suggestNextPk(schema.allRows, schema.pkName) : undefined;
  const addInitial = pkForAdd ? { [schema?.pkName ?? ""]: pkForAdd } : undefined;

  return (
    <div className="dt-drawer-group">
      <div className="dt-drawer-group-head">
        <span className="dt-drawer-group-title">{group.from_title}</span>
        <span className="dt-drawer-group-meta">{rows.length} record{rows.length === 1 ? "" : "s"}</span>
        <div className="dt-drawer-group-actions">
          {!showAdd && (
            <button className="dt-action-btn" onClick={() => setShowAdd(true)}>+ Add</button>
          )}
          {onOpenTable && (
            <button className="dt-action-btn" onClick={() => onOpenTable(group.from_table)}>Open table</button>
          )}
        </div>
      </div>

      {showAdd && schema && (
        <div className="dt-drawer-inline-form">
          <FormRenderer
            hostPath={group.from_table}
            fields={addFields}
            initialValues={addInitial}
            onSubmit={handleAddRow}
            onCancel={() => setShowAdd(false)}
            submitLabel="Add"
          />
        </div>
      )}

      {rows.length > 0 ? (
        <table className="dt-related-mini">
          <thead>
            <tr>
              {displayCols.map((f) => (
                <th key={f.name}>{f.label ?? f.name}</th>
              ))}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={String(r._id ?? i)}>
                {displayCols.map((f) => (
                  <td key={f.name}>{renderCell(r[f.name], f)}</td>
                ))}
                <td className="dt-related-mini-actions">
                  <button className="dt-action-btn dt-action-btn--delete" onClick={() => handleDeleteRow(String(r._id ?? ""))}>
                    Del
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !showAdd && <div className="dt-drawer-related-empty">No records yet</div>
      )}
    </div>
  );
}

function ManyToManyGroupSection({
  group,
  schema,
  onOpenTable,
}: {
  group: { junction_table: string; junction_title: string; target_table: string; target_title: string; rows: Record<string, unknown>[]; count: number };
  schema?: { fields: FieldSchema[]; pkName: string };
  onOpenTable?: (path: string) => void;
}) {
  const displayCols = useMemo(() => schema
    ? schema.fields.filter((f) => f.kind !== "formula" && f.kind !== "rollup" && f.kind !== "textarea" && f.name !== "_id").slice(0, MAX_COLS)
    : [], [schema]);
  const refLookup = useGroupRefLookup(group.junction_table, displayCols);

  const renderCell = (val: unknown, field: FieldSchema): string => {
    if (val == null || val === "") return "";
    if (field.kind === "ref") {
      return refLookup.get(`${field.name}::${val}`) ?? String(val);
    }
    if (field.kind === "boolean") return val ? "✓" : "";
    if (Array.isArray(val)) return val.join(", ");
    return String(val);
  };

  return (
    <div className="dt-drawer-group">
      <div className="dt-drawer-group-head">
        <span className="dt-drawer-group-title">{group.target_title}</span>
        <span className="dt-drawer-group-meta">{group.rows.length} record{group.rows.length === 1 ? "" : "s"}</span>
        {onOpenTable && (
          <div className="dt-drawer-group-actions">
            <button className="dt-action-btn" onClick={() => onOpenTable(group.junction_table)}>
              Open table
            </button>
          </div>
        )}
      </div>
      {group.rows.length > 0 && schema ? (
        <table className="dt-related-mini">
          <thead>
            <tr>
              {displayCols.map((f) => (
                <th key={f.name}>{f.label ?? f.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {group.rows.map((r, i) => (
              <tr key={String(r._id ?? i)}>
                {displayCols.map((f) => (
                  <td key={f.name}>{renderCell(r[f.name], f)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="dt-drawer-related-empty">No records</div>
      )}
    </div>
  );
}
