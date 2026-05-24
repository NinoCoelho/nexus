"""Schema inference and sidecar persistence for workflow debug mode."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA_DIR_NAME = "workflow_schemas"


def _schema_dir() -> Path:
    return Path.home() / ".nexus" / SCHEMA_DIR_NAME


def _sidecar_path(workflow_path: str) -> Path:
    stem = Path(workflow_path).stem
    return _schema_dir() / f"{stem}.schema.json"


def infer_schema(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        keys = list(data.keys())
        types: dict[str, str] = {}
        for k, v in data.items():
            types[k] = _type_name(v)
        return {"keys": keys, "types": types}
    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if isinstance(first, dict):
            return {"array_of": "object", "item_schema": infer_schema(first), "length": len(data)}
        return {"array_of": _type_name(first), "length": len(data)}
    return None


def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, dict):
        return "object"
    if isinstance(v, list):
        return "array"
    return "unknown"


def truncate_sample(data: Any, max_len: int = 500) -> Any:
    if isinstance(data, str) and len(data) > max_len:
        return data[:max_len] + "…"
    if isinstance(data, (dict, list)):
        raw = json.dumps(data, default=str)
        if len(raw) > max_len:
            return json.loads(raw[:max_len] + '"}')
    return data


def save_schema(workflow_path: str, step_schemas: dict[str, dict]) -> None:
    try:
        _schema_dir().mkdir(parents=True, exist_ok=True)
        target = _sidecar_path(workflow_path)
        existing: dict[str, Any] = {}
        if target.exists():
            try:
                existing = json.loads(target.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        existing["steps"] = step_schemas
        target.write_text(json.dumps(existing, indent=2, default=str))
    except Exception:
        log.debug("failed to save workflow schema for %s", workflow_path, exc_info=True)


def load_schema(workflow_path: str) -> dict[str, Any]:
    target = _sidecar_path(workflow_path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _samples_dir() -> Path:
    return Path.home() / ".nexus" / "workflow_samples"


def _samples_path(workflow_path: str) -> Path:
    stem = Path(workflow_path).stem
    return _samples_dir() / f"{stem}.samples.json"


def _read_samples_file(workflow_path: str) -> dict[str, Any]:
    target = _samples_path(workflow_path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_samples_file(workflow_path: str, data: dict[str, Any]) -> None:
    try:
        _samples_dir().mkdir(parents=True, exist_ok=True)
        target = _samples_path(workflow_path)
        target.write_text(json.dumps(data, indent=2, default=str))
    except Exception:
        log.debug("failed to save samples for %s", workflow_path, exc_info=True)


def save_step_sample(
    workflow_path: str,
    step_id: str,
    step_name: str | None = None,
    step_slug: str | None = None,
    input_resolved: Any = None,
    output: Any = None,
) -> None:
    data = _read_samples_file(workflow_path)
    steps = data.setdefault("steps", {})
    steps[step_id] = {
        "name": step_name or step_id,
        "slug": step_slug or step_id,
        "input_resolved": input_resolved,
        "output": output,
    }
    _write_samples_file(workflow_path, data)


def load_step_samples(workflow_path: str) -> dict[str, Any]:
    data = _read_samples_file(workflow_path)
    return data.get("steps", {})


def save_trigger_sample(workflow_path: str, payload: dict[str, Any]) -> None:
    data = _read_samples_file(workflow_path)
    data["trigger_payload"] = payload
    _write_samples_file(workflow_path, data)


def load_trigger_sample(workflow_path: str) -> dict[str, Any]:
    data = _read_samples_file(workflow_path)
    return data.get("trigger_payload", {})
