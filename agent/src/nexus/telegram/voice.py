"""Voice replies — synthesize the agent's answer and deliver it as a
Telegram voice note.

Pipeline: raw reply → optional question-aware condensation
(``voice_reply_mode="answer"``: fast LLM answers the user's original
question from the processing result — the chat already shows the full
text) → optional LLM "speechify" rewrite (drops markdown, expands
abbreviations, transliterates foreign words to the reply's phonetics)
→ ``tts.synthesize`` (Piper applies ``normalize_for_speech`` —
ranges/units/numbers — on top) → PyAV transcode to OGG/Opus →
sendVoice, with sendAudio as fallback when any step degrades.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .api import TelegramClient

log = logging.getLogger(__name__)

# Piper on long text is slow and voice notes that long are useless anyway —
# keep the spoken reply to roughly a few minutes at most.
_MAX_SYNTH_CHARS = 4000

_SPEECHIFY_TIMEOUT = 8.0
_CONDENSE_TIMEOUT = 10.0

# "Messy" heuristic for voice_speechify="auto": anything the deterministic
# normalizer can't fully fix — markdown residue, emoji, unit symbols,
# range dashes, URLs — earns an LLM rewrite.
_MESSY_RE = re.compile(
    r"[*_#`~•]\S"  # markdown
    r"|[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2757\u3030]"  # emoji/symbols
    r"|\d\s*[°%]"  # units/percent attached to digits
    r"|[–—]"  # ranges
    r"|https?://",
    re.UNICODE,
)

# Question-aware condensation (voice_reply_mode="answer"): the chat
# bubble already carries the full reply, so the voice note should ANSWER
# the user's original question derived from the agent's result, not
# read the screen-oriented text aloud.
_ANSWER_PROMPTS = {
    "pt": (
        "O usuário fez uma pergunta por voz. O assistente já processou tudo "
        "e produziu o resultado abaixo (o texto integral também já está "
        "escrito no chat).\n"
        "Com base nesse resultado, responda diretamente à pergunta do "
        "usuário, para ser lido em voz alta:\n"
        "- Frases corridas, sem markdown, listas, emojis ou parênteses.\n"
        "- Mantenha os fatos, números e conclusões essenciais; seja conciso.\n"
        "- Se o resultado contiver links, código ou arquivos, apenas "
        "mencione que estão no chat.\n"
        "- Responda no mesmo idioma da pergunta.\n"
        "Responda APENAS com o texto a ser falado, sem comentários.\n\n"
        "Pergunta do usuário:\n{question}\n\n"
        "Resultado do processamento:\n{reply}"
    ),
    "en": (
        "The user asked a question by voice. The assistant has already "
        "processed it and produced the result below (the full text is also "
        "written in the chat).\n"
        "Based on that result, answer the user's question directly, to be "
        "read aloud:\n"
        "- Flowing sentences, no markdown, lists, emojis, or parentheses.\n"
        "- Keep the essential facts, numbers, and conclusions; stay concise.\n"
        "- If the result contains links, code, or files, just mention they "
        "are in the chat.\n"
        "- Answer in the same language as the question.\n"
        "Reply with ONLY the text to be spoken, no commentary.\n\n"
        "User question:\n{question}\n\n"
        "Processing result:\n{reply}"
    ),
}

# Phonetic rewrite (voice_speechify): same content, speakable phrasing.
_SPEECHIFY_PROMPTS = {
    "pt": (
        "Reescreva o texto abaixo para ser lido em voz alta em português, "
        "como uma pessoa falando naturalmente:\n"
        "- Sem emojis, markdown, listas ou parênteses; use frases corridas.\n"
        "- Expanda abreviações e unidades (ex.: 30°C → 30 graus; 50% → "
        "50 por cento; 14h → 14 horas).\n"
        "- Translitere palavras estrangeiras para a fonética do português "
        "para que a síntese de voz as pronuncie bem (ex.: \"backup\" → "
        "\"bécape\", \"zero chance\" → \"zero chance\").\n"
        "- Mantenha todo o conteúdo, o mesmo idioma e seja conciso.\n"
        "Responda APENAS com o texto reescrito, sem comentários.\n\n"
        "Texto:\n{text}"
    ),
    "en": (
        "Rewrite the text below to be read aloud in English, like a person "
        "speaking naturally:\n"
        "- No emojis, markdown, lists, or parentheses; use flowing "
        "sentences.\n"
        "- Expand abbreviations and units (e.g. 30°C → 30 degrees; 50% → "
        "50 percent; 2pm → 2 PM).\n"
        "- Transliterate foreign words into English-friendly phonetics so "
        "text-to-speech pronounces them well.\n"
        "- Keep all the content, the same language, and stay concise.\n"
        "Reply with ONLY the rewritten text, no commentary.\n\n"
        "Text:\n{text}"
    ),
}


def needs_speechify(text: str) -> bool:
    return bool(_MESSY_RE.search(text or ""))


async def speechify(agent: Any, text: str, *, timeout: float = _SPEECHIFY_TIMEOUT) -> str:
    """LLM-rewrite ``text`` for natural speech. Returns the original on
    any failure/timeout — callers fall back gracefully."""
    try:
        from ..voice_ack import _detect_lang_short, _generate_text

        def _load_cfg():
            from ..config_file import load_cached as load_config

            return load_config()

        lang = _detect_lang_short(text)
        template = _SPEECHIFY_PROMPTS.get(lang, _SPEECHIFY_PROMPTS["en"])
        prompt = template.format(text=text[:8000])
        out = await asyncio.wait_for(
            _generate_text(agent, _load_cfg(), prompt), timeout=timeout
        )
        out = (out or "").strip()
        return out or text
    except Exception:
        log.debug("telegram: speechify failed — using original text", exc_info=True)
        return text


def strip_group_prefix(question: str) -> str:
    """Drop the ``From <sender>:`` prefix group messages carry — the
    condensation prompt wants the user's raw words."""
    return re.sub(r"^From [^\n]*:\s*\n+", "", (question or "").strip()).strip()


async def condense_for_speech(
    agent: Any,
    question: str,
    reply: str,
    *,
    timeout: float = _CONDENSE_TIMEOUT,
) -> str:
    """Answer the user's original question from the agent's processing
    result, in speakable form. Returns ``reply`` unchanged on any
    failure/timeout — callers fall back to the speechify chain."""
    question = strip_group_prefix(question)
    if not question or not (reply or "").strip():
        return reply
    try:
        from ..voice_ack import _detect_lang_short, _generate_text

        def _load_cfg():
            from ..config_file import load_cached as load_config

            return load_config()

        lang = _detect_lang_short(question)
        template = _ANSWER_PROMPTS.get(lang, _ANSWER_PROMPTS["en"])
        prompt = template.format(question=question[:2000], reply=reply[:8000])
        out = await asyncio.wait_for(
            _generate_text(agent, _load_cfg(), prompt), timeout=timeout
        )
        out = (out or "").strip()
        return out or reply
    except Exception:
        log.debug("telegram: condense failed — using original reply", exc_info=True)
        return reply


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
    agent: Any = None,
    speechify_mode: str = "auto",
    question: str | None = None,
    reply_mode: str = "answer",
) -> bool:
    """Synthesize ``text`` and send it as a voice note (with fallbacks).

    ``reply_mode="answer"`` (default): when the turn's user question is
    known, first condense the reply into a question-aware spoken answer
    (``condense_for_speech`` — no tools, fast ack model). ``"read"``
    keeps the read-aloud behavior.

    ``speechify_mode``: ``always`` rewrites the (possibly condensed)
    text through the LLM (``[tts].ack_model``) for natural spoken
    phrasing; ``auto`` only when the text is messy
    (markdown/emoji/units/foreign words); ``off`` never. LLM
    failure/timeout degrades silently to the rule-normalized text.

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

    if agent is not None and reply_mode == "answer" and question:
        try:
            text = await condense_for_speech(agent, question, text)
        except Exception:
            log.debug(
                "telegram: condense failed — falling back to read-aloud",
                exc_info=True,
            )

    if agent is not None and speechify_mode in ("auto", "always"):
        if speechify_mode == "always" or needs_speechify(text):
            try:
                text = await speechify(agent, text)
            except Exception:
                log.debug(
                    "telegram: speechify failed — falling back to rule "
                    "normalization",
                    exc_info=True,
                )

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
