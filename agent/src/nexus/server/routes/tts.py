"""TTS routes — synthesize text + list available voices.

The engine is fixed (bundled Piper). Default voices auto-download to
``~/.nexus/tts/piper`` on first daemon start (see ``tts.voice_setup``),
so by the time the user clicks Play in settings the synth call returns
audio bytes immediately.

Click-to-listen long-text behaviour: the route accepts a
``summarize_if_long`` flag; when set and the input is over ``cap_words``,
we run a fast LLM summary first (via ``voice_ack.summarize_long_text``)
and prepend a language-aware preamble (e.g. *"Aqui um resumo. "*).
The response carries an ``X-TTS-Summarized: 1`` header so the UI can
show a "Reading summary…" toast.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from ...agent.loop import Agent
from ...tts import TTSError, list_voices, synthesize
from ...voice_ack import _detect_lang_short, summarize_long_text
from ..deps import get_agent

log = logging.getLogger(__name__)

router = APIRouter()


# Language-aware "here's a summary" preamble. Matches the langs we
# support across the rest of the TTS pipeline (en + pt + es first-class).
_SUMMARY_PREAMBLE = {
    "pt": "Aqui um resumo. ",
    "en": "Here's a summary. ",
    "es": "Aquí un resumen. ",
}


def _word_count(text: str) -> int:
    return len((text or "").split())


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20_000)
    voice: str | None = None
    speed: float | None = None
    # When True, texts over cap_words are sent to the agent's LLM for a
    # 1-2 paragraph summary first; the synthesized output is prefixed with
    # "Here's a summary." (in the user's language). Off by default so
    # programmatic callers (acks) get verbatim synthesis.
    summarize_if_long: bool = False
    cap_words: int = Field(default=500, ge=50, le=5000)


@router.post("/tts/synthesize")
async def synthesize_endpoint(
    req: SynthesizeRequest,
    a: Agent = Depends(get_agent),
) -> Response:
    text = req.text
    summarized = False
    if req.summarize_if_long and _word_count(text) > req.cap_words:
        try:
            summary = await summarize_long_text(agent=a, text=text)
        except Exception as exc:  # noqa: BLE001 — never break the synth call
            log.warning("[tts] summarize failed, falling back to verbatim: %s", exc)
            summary = ""
        if summary:
            lang = _detect_lang_short(text)
            preamble = _SUMMARY_PREAMBLE.get(lang, _SUMMARY_PREAMBLE["en"])
            text = preamble + summary
            summarized = True
            log.info("[tts] summarized %d words → %d words", _word_count(req.text),
                     _word_count(text))
    try:
        result = await synthesize(text, voice=req.voice, speed=req.speed)
    except TTSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("[tts] synthesize failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"tts failed: {exc}",
        )
    headers = {"X-TTS-Summarized": "1"} if summarized else {}
    return Response(content=result.audio, media_type=result.mime, headers=headers)


@router.get("/tts/voices")
async def voices_endpoint(
    engine: str | None = None,  # legacy kwarg, ignored
    language: str | None = None,
) -> dict[str, Any]:
    try:
        voices = await list_voices(engine=engine, language=language)
    except TTSError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("[tts] list_voices failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"voice listing failed: {exc}",
        )
    return {
        "voices": [
            {"id": v.id, "name": v.name, "language": v.language} for v in voices
        ],
    }
