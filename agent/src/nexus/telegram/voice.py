"""Voice replies — synthesize the agent's answer and deliver it as a
Telegram voice note.

Pipeline: ``tts.synthesize`` (Piper, mono 16-bit WAV) → PyAV transcode to
OGG/Opus (the only container Telegram's sendVoice accepts) → sendVoice.
When the transcode is unavailable (PyAV wheel without libopus) or Telegram
rejects the voice note, delivery falls back to sendAudio and finally
sendDocument — a text-degraded reply is better than none.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .api import TelegramClient

log = logging.getLogger(__name__)

# Piper on long text is slow and voice notes that long are useless anyway —
# keep the spoken reply to roughly a few minutes at most.
_MAX_SYNTH_CHARS = 4000


def wav_to_ogg_opus(wav: bytes) -> bytes | None:
    """Transcode WAV → OGG/Opus in memory via PyAV. None on any failure."""
    try:
        import av

        inp = av.open(io.BytesIO(wav))
        out_buf = io.BytesIO()
        out = av.open(out_buf, "w", format="ogg")
        # libopus only accepts 8/12/16/24/48 kHz — resample whatever Piper
        # produced (typically 22.05 kHz mono) to 48 kHz mono s16.
        stream = out.add_stream("libopus")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)

        def _encode(frame: Any) -> None:
            frame.pts = None
            for packet in stream.encode(frame):
                out.mux(packet)

        for frame in inp.decode(audio=0):
            resampled = resampler.resample(frame)
            for rf in (resampled if isinstance(resampled, list) else [resampled]):
                _encode(rf)
        # PyAV's resampler buffers sub-frame tails without a flush API in
        # some versions — a few lost samples at the end are inaudible.
        for packet in stream.encode(None):  # flush the encoder
            out.mux(packet)
        out.close()
        inp.close()
        data = out_buf.getvalue()
        return data if data else None
    except Exception:
        log.debug("telegram: WAV→OGG/Opus transcode failed", exc_info=True)
        return None


async def deliver_voice_note(
    client: "TelegramClient",
    chat_id: int,
    thread_id: int,
    text: str,
    *,
    tts_cfg: Any = None,
) -> bool:
    """Synthesize ``text`` and send it as a voice note (with fallbacks).

    Returns True when some audio bubble was delivered. Silently no-ops
    when TTS is disabled or synthesis fails — the text reply already
    went out, audio is a bonus.
    """
    text = (text or "").strip()
    if not text:
        return False

    from ..tts import TTSError, synthesize

    if tts_cfg is None:
        from ..config_file import load_cached as load_config

        tts_cfg = load_config().tts
    if not getattr(tts_cfg, "enabled", False):
        return False

    try:
        result = await synthesize(text[:_MAX_SYNTH_CHARS], cfg=tts_cfg)
    except TTSError:
        log.info("telegram: TTS unavailable for voice reply — text only")
        return False
    except Exception:
        log.exception("telegram: TTS synthesis failed")
        return False

    audio, mime = result.audio, result.mime or "audio/wav"

    def _ext() -> str:
        return "ogg" if "ogg" in mime else ("wav" if "wav" in mime else "bin")

    # Voice note requires OGG/Opus — transcode when Piper gave us WAV.
    try:
        payload = audio
        if mime not in ("audio/ogg", "application/ogg"):
            payload = wav_to_ogg_opus(audio)
        if payload:
            await client.send_voice(
                chat_id, payload, "reply.ogg", thread_id=thread_id or None
            )
            return True
    except Exception:
        log.debug("telegram: voice note failed, trying audio fallback", exc_info=True)

    # Fallback: audio bubble (music player), then give up — the text
    # reply has already been delivered.
    try:
        await client.send_audio(
            chat_id, audio, f"reply.{_ext()}", mime, thread_id=thread_id or None
        )
        return True
    except Exception:
        log.debug("telegram: audio fallback failed", exc_info=True)
    return False
