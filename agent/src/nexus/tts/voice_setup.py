"""First-run setup for the bundled Piper voices.

The two default voices (``en_GB-northern_english_male-medium``,
``pt_BR-faber-medium``) are
~63 MB each. We download them in the background on first daemon start
so the user gets a "feels embedded" experience: by the time they click
Play in settings or send a voice message, synthesis is instant.

Subsequent starts are no-ops (the files persist under
``~/.nexus/tts/piper/``).
"""

from __future__ import annotations

import asyncio
import logging

from ..config_file import load as load_config
from .dispatch import DEFAULT_VOICES
from .piper import ensure_voice, voice_paths, warm_voice_cache

log = logging.getLogger(__name__)


def voices_already_present() -> bool:
    """Cheap check used to skip the prefetch task on warm starts."""
    cfg = load_config().tts
    for vid in DEFAULT_VOICES.values():
        model, config = voice_paths(cfg, vid)
        if not (model.exists() and config.exists()):
            return False
    return True


async def bootstrap_default_voices() -> None:
    """Download missing default voices and warm the in-memory voice cache.
    Call as a background task — failures are logged but never raised so
    they can't kill startup. Pre-loading PiperVoice into the process
    cache is what makes the *first* TTS request fast (vs. paying the
    23 MB ONNX deserialize on the user's critical path)."""
    cfg = load_config().tts
    need_download = not voices_already_present()
    if need_download:
        log.info("[tts/setup] downloading default Piper voices (~%d MB total)",
                 63 * len(DEFAULT_VOICES))
    # Sequential — HuggingFace's CDN is fine in parallel but two 63MB
    # files at the same time can saturate slow connections and starve
    # the rest of startup. One after the other is fast enough.
    for vid in DEFAULT_VOICES.values():
        try:
            model, config = await ensure_voice(cfg, vid)
            if need_download:
                log.info("[tts/setup] voice ready: %s", vid)
            await warm_voice_cache(model, config)
        except Exception:  # noqa: BLE001
            log.warning("[tts/setup] failed to prepare %s", vid, exc_info=True)


def schedule_bootstrap() -> None:
    """Fire-and-forget wrapper for use from sync startup code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # called outside an event loop — nothing to schedule
    loop.create_task(bootstrap_default_voices())
