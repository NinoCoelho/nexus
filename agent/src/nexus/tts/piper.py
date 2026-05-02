"""Local Piper TTS engine — the only engine Nexus ships.

Voice files live under ``~/.nexus/tts/piper/`` (or ``cfg.voices_dir`` when
set). The two default voices (``en_US-amy-medium``, ``pt_BR-faber-medium``)
auto-download on first daemon start via ``voice_setup.bootstrap_default_voices``;
this module also lazily downloads any *other* voice the user references
manually.

Voices catalog: https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import wave
from pathlib import Path
from typing import Any

import httpx

from ..config_file import TTSConfig
from .base import SynthResult, TTSError, Voice

log = logging.getLogger(__name__)

VOICES_INDEX_URL = "https://huggingface.co/rhasspy/piper-voices/raw/main/voices.json"
VOICE_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def voices_dir(cfg: TTSConfig) -> Path:
    if cfg.voices_dir:
        return Path(cfg.voices_dir).expanduser()
    return Path.home() / ".nexus" / "tts" / "piper"


def voice_paths(cfg: TTSConfig, voice_id: str) -> tuple[Path, Path]:
    d = voices_dir(cfg)
    return d / f"{voice_id}.onnx", d / f"{voice_id}.onnx.json"


async def _ensure_index(cfg: TTSConfig) -> dict[str, Any]:
    d = voices_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    idx = d / "voices.json"
    if not idx.exists():
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(VOICES_INDEX_URL)
        if resp.status_code >= 400:
            raise TTSError(f"failed to fetch piper voices index: {resp.status_code}")
        idx.write_bytes(resp.content)
    return json.loads(idx.read_bytes())


async def ensure_voice(cfg: TTSConfig, voice_id: str) -> tuple[Path, Path]:
    """Download voice ONNX + config if missing. Returns (model, config) paths."""
    model_path, config_path = voice_paths(cfg, voice_id)
    if model_path.exists() and config_path.exists():
        return model_path, config_path

    index = await _ensure_index(cfg)
    entry = index.get(voice_id)
    if entry is None:
        raise TTSError(f"piper voice not in catalog: {voice_id}")
    files = entry.get("files") or {}
    onnx_rel = next((p for p in files if p.endswith(".onnx")), None)
    json_rel = next((p for p in files if p.endswith(".onnx.json")), None)
    if not onnx_rel or not json_rel:
        raise TTSError(f"voice {voice_id} missing files in catalog")

    log.info("[tts/piper] downloading voice %s (~63MB)", voice_id)
    voices_dir(cfg).mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        for rel, dest in ((onnx_rel, model_path), (json_rel, config_path)):
            url = f"{VOICE_BASE_URL}/{rel}"
            resp = await client.get(url)
            if resp.status_code >= 400:
                raise TTSError(f"piper download {url} → {resp.status_code}")
            dest.write_bytes(resp.content)
    return model_path, config_path


def _voice_sample_rate(voice: object, config_path: Path) -> int:
    """Resolve the model's output sample rate.

    Tries ``voice.config.sample_rate`` first (newer piper-tts releases),
    then ``voice.sample_rate``, then falls back to reading the JSON
    config directly. Essential for the legacy `synthesize` path —
    ``wave`` raises "# channels not specified" without it.
    """
    cfg = getattr(voice, "config", None)
    if cfg is not None:
        sr = getattr(cfg, "sample_rate", None)
        if sr:
            return int(sr)
    sr = getattr(voice, "sample_rate", None)
    if sr:
        return int(sr)
    try:
        data = json.loads(config_path.read_text())
        return int(data["audio"]["sample_rate"])
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise TTSError(f"could not resolve sample rate from {config_path}: {exc}")


def _synthesize_blocking(model_path: Path, config_path: Path, text: str, speed: float) -> bytes:
    try:
        from piper import PiperVoice  # type: ignore
    except ImportError as exc:
        # Should never happen — piper-tts is a base dependency now. Kept
        # as defense-in-depth for broken installs.
        raise TTSError("piper-tts not installed") from exc
    try:
        from piper import SynthesisConfig  # type: ignore
    except ImportError:
        SynthesisConfig = None  # type: ignore[assignment]

    voice = PiperVoice.load(str(model_path), config_path=str(config_path))
    # Piper's length_scale is inverse of speed (>1 = slower). Map our
    # multiplier (0.75=slower, 1.5=faster) so 1.0 stays neutral.
    length_scale = 1.0 / max(0.25, min(4.0, speed or 1.0))

    buf = io.BytesIO()
    if SynthesisConfig is not None and hasattr(voice, "synthesize_wav"):
        # piper-tts >= 1.2: synthesize_wav sets the wave parameters
        # internally. We must NOT pre-set channels/samplewidth/framerate.
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(
                text, wav, syn_config=SynthesisConfig(length_scale=length_scale),
            )
    else:
        # Legacy API: caller sets wave params + length_scale kwarg.
        sample_rate = _voice_sample_rate(voice, config_path)
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            voice.synthesize(text, wav, length_scale=length_scale)
    return buf.getvalue()


async def synthesize(text: str, voice: str, speed: float, cfg: TTSConfig) -> SynthResult:
    model_path, config_path = await ensure_voice(cfg, voice)
    audio = await asyncio.to_thread(
        _synthesize_blocking, model_path, config_path, text, speed,
    )
    return SynthResult(audio=audio, mime="audio/wav")


async def list_voices(language: str | None, cfg: TTSConfig) -> list[Voice]:
    index = await _ensure_index(cfg)
    out: list[Voice] = []
    for voice_id, entry in index.items():
        lang_obj = entry.get("language") or {}
        lang = lang_obj.get("code") or lang_obj.get("name_native") or ""
        out.append(Voice(id=voice_id, name=voice_id, language=lang or "und"))
    if language:
        target = language.lower().replace("-", "_")
        out = [
            v for v in out
            if v.language.lower().replace("-", "_").startswith(target[:2])
        ]
    out.sort(key=lambda v: (v.language, v.id))
    return out
