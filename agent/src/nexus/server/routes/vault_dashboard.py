"""Routes for vault data-dashboard operations: /vault/dashboard*."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/vault/dashboard")
async def vault_dashboard_get(folder: str = "") -> dict:
    """Return the dashboard for ``folder``. Lazy: missing `_data.md` returns
    sensible defaults with ``exists: false`` and does NOT touch disk."""
    from ... import vault_dashboard
    return vault_dashboard.read_dashboard(folder)


@router.put("/vault/dashboard")
async def vault_dashboard_put(body: dict) -> dict:
    """Patch the dashboard. Materializes `_data.md` if absent."""
    from ... import vault_dashboard
    folder = body.get("folder", "")
    if not isinstance(folder, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`folder` must be a string")
    patch = {k: body[k] for k in ("title", "chat_session_id", "operations") if k in body}
    try:
        return vault_dashboard.patch_dashboard(folder, patch)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/vault/dashboard/operations", status_code=status.HTTP_201_CREATED)
async def vault_dashboard_add_operation(body: dict) -> dict:
    """Append or replace an operation (by id) on the dashboard."""
    from ... import vault_dashboard
    folder = body.get("folder", "")
    op = body.get("operation")
    if not isinstance(op, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="`operation` must be an object")
    try:
        return vault_dashboard.upsert_operation(folder, op)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.delete("/vault/dashboard/operations/{op_id}", status_code=status.HTTP_200_OK)
async def vault_dashboard_delete_operation(op_id: str, folder: str = "") -> dict:
    from ... import vault_dashboard
    return vault_dashboard.delete_operation(folder, op_id)


@router.delete("/vault/dashboard")
async def vault_dashboard_delete_database(folder: str, confirm: str) -> dict:
    """Delete an entire database (folder of data-tables + `_data.md`).

    ``confirm`` must equal the folder's basename — server-side guard against
    accidental wipes.
    """
    from ... import vault_dashboard
    try:
        return vault_dashboard.delete_database(folder, confirm=confirm)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
