// API client for vault data-table CRUD operations.
import { BASE } from "./base";

export interface DataTableView {
  name: string;
  filter?: string;
  sort?: { field: string; dir: "asc" | "desc" };
  hidden?: string[];
}

export interface DataTable {
  path: string;
  schema: { title?: string; fields: import("../types/form").FieldSchema[] };
  rows: Record<string, unknown>[];
  views?: DataTableView[];
}

export async function getVaultDataTable(path: string): Promise<DataTable> {
  const res = await fetch(`${BASE}/vault/datatable?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`DataTable load error: ${res.status}`);
  return res.json();
}

export async function addVaultDataTableRow(
  path: string,
  row: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE}/vault/datatable/rows?path=${encodeURIComponent(path)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ row }),
  });
  if (!res.ok) throw new Error(`DataTable add row error: ${res.status}`);
  return res.json();
}

export async function updateVaultDataTableRow(
  path: string,
  rowId: string,
  row: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  const res = await fetch(
    `${BASE}/vault/datatable/rows/${encodeURIComponent(rowId)}?path=${encodeURIComponent(path)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ row }),
    },
  );
  if (!res.ok) throw new Error(`DataTable update row error: ${res.status}`);
  return res.json();
}

export async function deleteVaultDataTableRow(path: string, rowId: string): Promise<void> {
  const res = await fetch(
    `${BASE}/vault/datatable/rows/${encodeURIComponent(rowId)}?path=${encodeURIComponent(path)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(`DataTable delete row error: ${res.status}`);
}

export async function bulkAddVaultDataTableRows(
  path: string,
  rows: Record<string, unknown>[],
): Promise<{ added: Record<string, unknown>[]; count: number }> {
  const res = await fetch(`${BASE}/vault/datatable/rows/bulk?path=${encodeURIComponent(path)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows }),
  });
  if (!res.ok) throw new Error(`DataTable bulk add error: ${res.status}`);
  return res.json();
}

export async function setVaultDataTableSchema(
  path: string,
  schema: DataTable["schema"],
): Promise<DataTable> {
  const res = await fetch(`${BASE}/vault/datatable/schema?path=${encodeURIComponent(path)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ schema }),
  });
  if (!res.ok) throw new Error(`DataTable set schema error: ${res.status}`);
  return res.json();
}

export async function setVaultDataTableViews(
  path: string,
  views: DataTableView[],
): Promise<DataTable> {
  const res = await fetch(`${BASE}/vault/datatable/views?path=${encodeURIComponent(path)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ views }),
  });
  if (!res.ok) throw new Error(`DataTable set views error: ${res.status}`);
  return res.json();
}
