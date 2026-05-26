"""Routes for project management: CRUD + session assignment.

Projects are named workspaces that group related chat sessions with
project-scoped instructions and an auto-created vault subfolder.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status

from ..project_store import ProjectStore

log = logging.getLogger(__name__)

router = APIRouter()

_DB_PATH = Path("~/.nexus/sessions.sqlite").expanduser()


def _get_store() -> ProjectStore:
    return ProjectStore(_DB_PATH)


@router.get("/projects")
async def list_projects(
    limit: int = 50,
) -> list[dict]:
    store = _get_store()
    summaries = await asyncio.to_thread(store.list, limit=max(1, min(limit, 200)))
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "color": s.color,
            "icon": s.icon,
            "session_count": s.session_count,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in summaries
    ]


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: dict,
) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    store = _get_store()
    project = await asyncio.to_thread(
        store.create,
        name=name,
        description=body.get("description", ""),
        instructions=body.get("instructions", ""),
        color=body.get("color", ""),
        icon=body.get("icon", ""),
    )
    return _project_to_dict(project)


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
) -> dict:
    store = _get_store()
    project = await asyncio.to_thread(store.get, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_to_dict(project)


@router.patch("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_project(
    project_id: str,
    body: dict,
) -> None:
    store = _get_store()
    project = await asyncio.to_thread(store.get, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await asyncio.to_thread(store.update, project_id, **body)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
) -> None:
    store = _get_store()
    project = await asyncio.to_thread(store.get, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await asyncio.to_thread(store.delete, project_id)


@router.post(
    "/projects/{project_id}/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def move_session_to_project(
    project_id: str,
    session_id: str,
) -> None:
    store = _get_store()
    project = await asyncio.to_thread(store.get, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await asyncio.to_thread(store.move_session, session_id, project_id)


@router.delete(
    "/projects/{project_id}/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_session_from_project(
    project_id: str,
    session_id: str,
) -> None:
    store = _get_store()
    await asyncio.to_thread(store.move_session, session_id, None)


def _project_to_dict(project: Any) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "instructions": project.instructions,
        "vault_path": project.vault_path,
        "color": project.color,
        "icon": project.icon,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
