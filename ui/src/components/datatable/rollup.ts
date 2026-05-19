import type { FieldSchema, RollupAggregate } from "../../types/form";
import { resolveRefPath, fetchTableCached } from "./refOptions";
import { evalFormula } from "./formula";

function toNum(v: unknown): number {
  if (typeof v === "number") return v;
  if (v == null || v === "") return 0;
  const n = parseFloat(String(v));
  return Number.isFinite(n) ? n : 0;
}

function computeAggregate(values: number[], fn: RollupAggregate): number | string {
  if (fn === "count") return values.length;
  if (values.length === 0) return "";
  const sum = values.reduce((a, b) => a + b, 0);
  switch (fn) {
    case "sum": return Math.round(sum * 1e6) / 1e6;
    case "avg": return values.length === 0 ? "" : Math.round((sum / values.length) * 1e6) / 1e6;
    case "min": return Math.min(...values);
    case "max": return Math.max(...values);
    default: return "";
  }
}

export async function evalRollups(
  rows: Record<string, unknown>[],
  fields: FieldSchema[],
  hostPath: string,
  pkName: string,
): Promise<Record<string, unknown>[]> {
  const rollupFields = fields.filter(
    (f) => f.kind === "rollup" && f.rollup_target_table && f.rollup_relation_field && f.rollup_aggregate,
  );
  if (rollupFields.length === 0 || rows.length === 0) return rows;

  const tableCache = new Map<string, { rows: Record<string, unknown>[]; fields: FieldSchema[] }>();

  for (const rf of rollupFields) {
    const absPath = resolveRefPath(hostPath, rf.rollup_target_table!);
    if (!absPath) continue;

    let targetData = tableCache.get(absPath);
    if (!targetData) {
      try {
        const tbl = await fetchTableCached(absPath);
        const enrichedRows = tbl.rows.map((r) => {
          const out = { ...r };
          for (const f of tbl.schema.fields) {
            if (f.kind === "formula" && f.formula) out[f.name] = evalFormula(f.formula, out);
          }
          return out;
        });
        targetData = { rows: enrichedRows, fields: tbl.schema.fields };
        tableCache.set(absPath, targetData);
      } catch {
        continue;
      }
    }

    const relField = rf.rollup_relation_field!;
    const aggFn = rf.rollup_aggregate!;
    const srcField = rf.rollup_source_field;

    const grouped = new Map<string, unknown[]>();
    for (const detailRow of targetData.rows) {
      const fkVal = String(detailRow[relField] ?? "");
      if (!fkVal) continue;
      let group = grouped.get(fkVal);
      if (!group) {
        group = [];
        grouped.set(fkVal, group);
      }
      group.push(srcField ? detailRow[srcField] : 1);
    }

    for (const row of rows) {
      const pkVal = String(row[pkName] ?? row._id ?? "");
      const groupValues = grouped.get(pkVal);
      if (!groupValues || groupValues.length === 0) {
        (row as Record<string, unknown>)[rf.name] = aggFn === "count" ? 0 : "";
        continue;
      }
      const nums = groupValues.map(toNum);
      (row as Record<string, unknown>)[rf.name] = computeAggregate(nums, aggFn);
    }
  }

  return rows;
}
