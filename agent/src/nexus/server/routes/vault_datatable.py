"""Routes for vault data-table operations: /vault/datatable*."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ...i18n import t
from ..deps import get_locale

router = APIRouter()


@router.get("/vault/datatable/databases")
async def vault_datatable_databases() -> dict:
    """List every "database" (folder containing ≥1 data-table file)."""
    from ... import vault_datatable_index
    databases = vault_datatable_index.list_databases()
    return {"databases": databases, "count": len(databases)}


@router.get("/vault/datatable/list")
async def vault_datatable_list(folder: str = "") -> dict:
    """List the data-tables inside a single folder."""
    from ... import vault_datatable_index
    tables = vault_datatable_index.list_tables_in_folder(folder)
    return {"folder": folder, "tables": tables, "count": len(tables)}


@router.get("/vault/datatable/erdiagram")
async def vault_datatable_erdiagram(folder: str = "") -> dict:
    """Generate a mermaid erDiagram for a database (folder of data-tables)."""
    from ... import vault_datatable_index
    return {"folder": folder, "mermaid": vault_datatable_index.er_diagram(folder)}


@router.get("/vault/datatable/related")
async def vault_datatable_related(
    path: str,
    row_id: str,
    locale: str = Depends(get_locale),
) -> dict:
    """Return rows from other tables that reference ``(path, row_id)``."""
    from ... import vault_datatable
    try:
        # Validate the host table exists.
        vault_datatable.read_table(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.vault_datatable.file_not_found", locale),
        )
    return {"path": path, "row_id": row_id, **vault_datatable.related_rows(path, row_id)}


@router.get("/vault/datatable")
async def vault_datatable_get(path: str, locale: str = Depends(get_locale)) -> dict:
    from ... import vault_datatable
    try:
        tbl = vault_datatable.read_table(path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.vault_datatable.file_not_found", locale),
        )
    return {"path": path, **tbl}


@router.post("/vault/datatable/rows", status_code=status.HTTP_201_CREATED)
async def vault_datatable_add_row(
    body: dict,
    path: str,
    locale: str = Depends(get_locale),
) -> dict:
    from ... import vault_datatable
    row = body.get("row", {})
    if not isinstance(row, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("errors.vault_datatable.row_must_be_object", locale),
        )
    try:
        added = vault_datatable.add_row(path, row)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return added


@router.patch("/vault/datatable/rows/{row_id}")
async def vault_datatable_update_row(
    row_id: str,
    body: dict,
    path: str,
    locale: str = Depends(get_locale),
) -> dict:
    from ... import vault_datatable
    updates = body.get("row", body)
    if not isinstance(updates, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("errors.vault_datatable.row_must_be_object", locale),
        )
    try:
        updated = vault_datatable.update_row(path, row_id, updates)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return updated


@router.delete("/vault/datatable/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
async def vault_datatable_delete_row(row_id: str, path: str) -> None:
    from ... import vault_datatable
    try:
        vault_datatable.delete_row(path, row_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/vault/datatable/rows/bulk", status_code=status.HTTP_201_CREATED)
async def vault_datatable_bulk_add(
    body: dict,
    path: str,
    locale: str = Depends(get_locale),
) -> dict:
    from ... import vault_datatable
    rows = body.get("rows", [])
    if not isinstance(rows, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("errors.vault_datatable.rows_must_be_list", locale),
        )
    try:
        added = vault_datatable.add_rows(path, rows)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"added": added, "count": len(added)}


@router.put("/vault/datatable/schema")
async def vault_datatable_set_schema(
    body: dict,
    path: str,
    locale: str = Depends(get_locale),
) -> dict:
    from ... import vault_datatable
    schema = body.get("schema")
    if not isinstance(schema, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("errors.vault_datatable.schema_must_be_object", locale),
        )
    try:
        tbl = vault_datatable.set_schema(path, schema)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"path": path, **tbl}


@router.put("/vault/datatable/views")
async def vault_datatable_set_views(
    body: dict,
    path: str,
    locale: str = Depends(get_locale),
) -> dict:
    from ... import vault_datatable
    views = body.get("views", [])
    if not isinstance(views, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("errors.vault_datatable.views_must_be_list", locale),
        )
    try:
        tbl = vault_datatable.set_views(path, views)
    except (FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"path": path, **tbl}
