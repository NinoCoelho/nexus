"""Audio transcription endpoint.

Default: local faster-whisper. Optional remote mode forwards to an
OpenAI-compatible /audio/transcriptions endpoint.
"""

from __future__ import annotations

import logging
import os
import tempfile
from functools import lru_cache
from typing import Any

import httpx
from fastapi import HTTPException, Request, status

from ..config_file import TranscriptionConfig, load as load_config

log = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _get_local_model(model: str, device: str, compute_type: str) -> Any:
    from faster_whisper import WhisperModel  # lazy import; heavy

    log.info("[transcribe] loading faster-whisper model=%s device=%s compute=%s",
             model, device, compute_type)
    return WhisperModel(model, device=device, compute_type=compute_type)


def _local_transcribe(path: str, cfg: TranscriptionConfig) -> str:
    model = _get_local_model(cfg.model, cfg.device, cfg.compute_type)
    segments, _info = model.transcribe(path, language=cfg.language or None)
    return "".join(seg.text for seg in segments).strip()


async def _remote_transcribe(path: str, filename: str, cfg: TranscriptionConfig) -> str:
    remote = cfg.remote
    if not remote.base_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="transcription.remote.base_url not configured",
        )
    api_key = os.environ.get(remote.api_key_env, "") if remote.api_key_env else ""
    url = remote.base_url.rstrip("/") + "/audio/transcriptions"
    with open(path, "rb") as f:
        data = f.read()
    files = {"file": (filename, data, "audio/webm")}
    form: dict[str, str] = {"model": remote.model or "whisper-1"}
    if cfg.language:
        form["language"] = cfg.language
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, data=form, files=files, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=resp.text)
    body = resp.json()
    return (body.get("text") or "").strip()


def register(app: Any) -> None:
    @app.post("/transcribe")
    async def transcribe_endpoint(request: Request) -> dict:
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="expected multipart/form-data",
            )
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "filename"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="`file` field required",
            )
        raw = await upload.read()
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="empty audio",
            )

        cfg = load_config().transcription
        # Per-request language override from form wins over config.
        language_override = form.get("language")
        if isinstance(language_override, str) and language_override.strip():
            cfg = cfg.model_copy(update={"language": language_override.strip()})

        filename = getattr(upload, "filename", "audio.webm") or "audio.webm"
        suffix = os.path.splitext(filename)[1] or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        try:
            if cfg.mode == "remote":
                text = await _remote_transcribe(tmp_path, filename, cfg)
            else:
                # faster-whisper is sync + CPU/GPU-bound; run in a thread.
                import asyncio as _asyncio
                text = await _asyncio.to_thread(_local_transcribe, tmp_path, cfg)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("[transcribe] failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"transcription failed: {exc}",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        return {"text": text}
