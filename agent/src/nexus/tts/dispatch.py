"""Resolve the right Piper voice for a chunk of text and synthesize it.

The engine is fixed (Piper). For every utterance we run ``langdetect``
over the text and map the detected ISO code to one of the bundled
voices (English / Portuguese). Anything we don't have a mapping for
falls back to English. No manual override knobs in the UI.
"""

from __future__ import annotations

import logging

from ..config_file import TTSConfig, load as load_config
from .base import SynthResult, TTSError, Voice
from .normalize import normalize_for_speech
from . import piper as _piper

log = logging.getLogger(__name__)


# ISO-639-1 → bundled Piper voice id. Adding a new language here means
# (a) adding it to ``voice_setup.DEFAULT_VOICES`` so the daemon prefetches
# it on startup, and (b) shipping the .onnx + .onnx.json under
# ``~/.nexus/tts/piper/``.
DEFAULT_VOICES: dict[str, str] = {
    "en": "en_US-bryce-medium",
    "pt": "pt_BR-faber-medium",
}
_FALLBACK_VOICE = DEFAULT_VOICES["en"]
_DEFAULT_SPEED = 1.0
_VOICE_SPEEDS: dict[str, float] = {
    "en_US-bryce-medium": 1.0 / 0.7,
}


def _voice_for_language(lang: str | None) -> str:
    """Map a BCP-47 / ISO-639 language tag to one of our bundled voices."""
    if not lang:
        return _FALLBACK_VOICE
    code = lang.lower().split("-")[0].split("_")[0]
    return DEFAULT_VOICES.get(code, _FALLBACK_VOICE)


def _detect_language(text: str) -> str | None:
    """Best-effort language detection. Returns a short code (e.g. ``"pt"``)
    or None on failure. ``langdetect`` is non-deterministic by default
    on short strings, so we seed it for stable test behavior."""
    sample = (text or "").strip()
    if len(sample) < 3:
        return None
    try:
        from langdetect import DetectorFactory, detect  # type: ignore

        DetectorFactory.seed = 0
        return detect(sample)
    except Exception:  # noqa: BLE001 — detection is opportunistic
        return None


def resolve_voice_for_text(text: str, cfg: TTSConfig | None = None) -> str:
    """Decide which Piper voice id to synthesize this utterance with.

    ``cfg`` is accepted for API compat but unused — the resolution is
    always ``langdetect`` → ``DEFAULT_VOICES`` → English fallback.
    """
    detected = _detect_language(text)
    return _voice_for_language(detected) if detected else _FALLBACK_VOICE


async def synthesize(
    text: str,
    *,
    voice: str | None = None,
    speed: float | None = None,
    cfg: TTSConfig | None = None,
) -> SynthResult:
    if cfg is None:
        cfg = load_config().tts
    if not cfg.enabled:
        raise TTSError("TTS is disabled in settings")
    # Detect language ONCE: the same code drives voice picking and the
    # number/date expansion in normalize_for_speech, so they can't disagree.
    lang_code = _detect_language(text) or ""
    chosen_voice = voice or _voice_for_language(lang_code)
    chosen_speed = speed if speed is not None else _VOICE_SPEEDS.get(chosen_voice, _DEFAULT_SPEED)
    spoken_text = normalize_for_speech(text, lang=lang_code)
    return await _piper.synthesize(spoken_text.strip(), chosen_voice, chosen_speed, cfg)


async def list_voices(
    *,
    engine: str | None = None,  # accepted for API compat; ignored
    language: str | None = None,
    cfg: TTSConfig | None = None,
) -> list[Voice]:
    if cfg is None:
        cfg = load_config().tts
    if not cfg.enabled:
        return []
    return await _piper.list_voices(language, cfg)
