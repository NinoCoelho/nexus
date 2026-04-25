// API client for vault CSV/TSV CRUD operations (DuckDB-backed analytics on the
// agent side; plain CRUD here for the editor UI).
import { BASE } from "./base";

export interface CsvPage {
  path: string;
  columns: string[];
  rows: Record<string, string>[];
  total_rows: number;
  offset: number;
  limit: number;
}

export interface CsvSchemaColumn {
  name: string;
  rename_from?: string;
}

export async function getVaultCsv(
  path: string,
  opts: { offset?: number; limit?: number; sort?: string; sort_dir?: "asc" | "desc" } = {},
): Promise<CsvPage> {
  const params = new URLSearchParams({ path });
  if (opts.offset != null) params.set("offset", String(opts.offset));
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.sort) params.set("sort", opts.sort);
  if (opts.sort_dir) params.set("sort_dir", opts.sort_dir);
  const res = await fetch(`${BASE}/vault/csv?${params.toString()}`);
  if (!res.ok) throw new Error(`CSV load error: ${res.status}`);
  return res.json();
}

export async function addVaultCsvRow(
  path: string,
  values: Record<string, string>,
): Promise<{ row_index: number; total_rows: number }> {
  const res = await fetch(`${BASE}/vault/csv/row`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, values }),
  });
  if (!res.ok) throw new Error(`CSV add row error: ${res.status}`);
  return res.json();
}

export async function updateVaultCsvCell(
  path: string,
  rowIndex: number,
  column: string,
  value: string,
): Promise<void> {
  const res = await fetch(`${BASE}/vault/csv/cell`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, row_index: rowIndex, column, value }),
  });
  if (!res.ok) throw new Error(`CSV update cell error: ${res.status}`);
}

export async function deleteVaultCsvRow(path: string, rowIndex: number): Promise<void> {
  const params = new URLSearchParams({ path, row_index: String(rowIndex) });
  const res = await fetch(`${BASE}/vault/csv/row?${params.toString()}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`CSV delete row error: ${res.status}`);
}

export async function setVaultCsvSchema(
  path: string,
  columns: CsvSchemaColumn[],
): Promise<{ columns: string[]; total_rows: number }> {
  const res = await fetch(`${BASE}/vault/csv/schema`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, columns }),
  });
  if (!res.ok) throw new Error(`CSV set schema error: ${res.status}`);
  return res.json();
}
