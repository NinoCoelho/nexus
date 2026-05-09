"""Spoken acknowledgments for voice-input turns.

Two kinds, both gated by ``cfg.tts.ack_enabled``:

  - **start**:    fired the moment a voice message arrives — a short
                  contextual phrase (max 7 words) so the user gets
                  instant audio feedback before the agent loop runs.
  - **complete**: fired when the agent's reply lands — a concise
                  highlight (max 15 words) of the main result.

Each ack uses a dedicated fast model (``cfg.tts.ack_model``) when
configured, otherwise the agent's default model. Audio is synthesized
server-side through the bundled Piper engine; the event carries
base64 WAV bytes that the UI decodes and plays. When the LLM returns
empty (some local models do), a hardcoded language-aware template is
used instead so the user always hears something.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config_file import NexusConfig, load as load_config
from .tts import TTSError, synthesize

if TYPE_CHECKING:
    from .agent.loop import Agent
    from .server.session_store import SessionStore

log = logging.getLogger(__name__)


# Prompts are kept per language because local models follow language cues
# in the *instructions* far more reliably than the "match the user's
# language" directive in an English prompt. We pick the template by the
# detected language of the user's request (or text being summarized).

_START_PROMPT_EN = (
    "You are a voice assistant giving an instant verbal acknowledgment "
    "BEFORE starting work. The user dictated: {user_text!r}\n\n"
    "Generate ONE short, casual spoken sentence (at most 7 words) that:\n"
    "  - Confirms what you understood, in plain words\n"
    "  - Sounds natural and informal, like a person, not a script\n"
    "  - Hints at the expected action (e.g. 'right back', 'one moment', "
    "'looking that up', 'checking now')\n\n"
    "REPLY IN ENGLISH. Reply with ONLY the spoken sentence — no quotes, "
    "no preamble.\n"
    "Do NOT use asterisks, hashes, backticks, dashes, pipes, angle brackets, "
    "or any formatting symbols. Use only plain words and basic punctuation "
    "(period, comma, question mark, exclamation mark)."
)

_START_PROMPT_PT = (
    "Você é um assistente de voz dando uma confirmação verbal instantânea "
    "ANTES de começar a trabalhar. O usuário ditou: {user_text!r}\n\n"
    "Gere UMA frase falada, curta e casual (no máximo 7 palavras) que:\n"
    "  - Confirme o que você entendeu, em palavras simples\n"
    "  - Soe natural e informal, como uma pessoa, não um script\n"
    "  - Dê uma pista da ação esperada (ex: 'tô vendo isso', "
    "'um momento', 'verificando agora', 'olhando isso')\n\n"
    "RESPONDA EM PORTUGUÊS BRASILEIRO. Responda com APENAS a frase falada — "
    "sem aspas, sem preâmbulo.\n"
    "NÃO use asteriscos, cerquilhas, crases, traços, pipes ou qualquer "
    "símbolo de formatação. Use apenas palavras e pontuação básica "
    "(ponto, vírgula, ponto de interrogação, ponto de exclamação)."
)

_COMPLETE_PROMPT_EN = (
    "You are a voice assistant. The user just watched you work through a "
    "problem and can SEE your full written reply on screen. They need the "
    "CONCLUSION spoken aloud — the final answer, the key number, the "
    "decision, or the single takeaway. Do NOT narrate or rehash what you "
    "did — they watched it happen.\n\n"
    "Rules:\n"
    "- ONE spoken sentence, at most 30 words\n"
    "- State the bottom-line result: the answer, the fix, the recommendation\n"
    "- If the reply is a list, name only the top item or the count\n"
    "- If it's a question answered, just give the answer\n"
    "- If it's work done, state the outcome ('Done — X was updated', 'Found 3 issues')\n"
    "- Plain spoken English, no formatting symbols\n"
    "Do NOT use asterisks, hashes, backticks, dashes, pipes, angle brackets, "
    "or any formatting symbols. Use only plain words and basic punctuation "
    "(period, comma, question mark, exclamation mark).\n\n{full_reply}"
)

_COMPLETE_PROMPT_PT = (
    "Você é um assistente de voz. O usuário acabou de assistir você "
    "trabalhar num problema e PODE VER a resposta completa na tela. Ele "
    "precisa ouvir a CONCLUSÃO — a resposta final, o número-chave, a "
    "decisão ou o principal resultado. NÃO narre ou repita o que você "
    "fez — ele viu acontecer.\n\n"
    "Regras:\n"
    "- UMA frase falada, no máximo 30 palavras\n"
    "- Diga o resultado final: a resposta, a correção, a recomendação\n"
    "- Se a resposta é uma lista, cite só o principal ou a quantidade\n"
    "- Se é uma pergunta respondida, dê só a resposta\n"
    "- Se é trabalho feito, state o resultado ('Pronto — X foi atualizado', 'Encontrei 3 problemas')\n"
    "- Português brasileiro falado, sem símbolos de formatação\n"
    "NÃO use asteriscos, cerquilhas, crases, traços, pipes ou qualquer "
    "símbolo de formatação. Use apenas palavras e pontuação básica "
    "(ponto, vírgula, ponto de interrogação, ponto de exclamação).\n\n{full_reply}"
)

_SUMMARIZE_PROMPT_EN = (
    "You are converting a written message into speech for someone who can "
    "already SEE the full text on their screen. They clicked 'listen' to "
    "hear the CONCLUSION — not a walkthrough of what they already read.\n\n"
    "Produce 1-2 spoken paragraphs (under 150 words) that deliver the "
    "BOTTOM LINE:\n"
    "- What was the answer or result?\n"
    "- What action should they take?\n"
    "- What is the key insight or decision?\n\n"
    "RULES:\n"
    "- Skip the journey — give the destination\n"
    "- Do NOT re-list steps, code, or intermediate reasoning\n"
    "- If the text contains a clear answer, lead with it\n"
    "- If it's a comparison, state the recommendation\n"
    "- If it's an explanation, give the takeaway\n"
    "- No bullets, no markdown, no headers, no code blocks\n"
    "- No asterisks (*), hashes (#), backticks (`), dashes (-), pipes (|), "
    "angle brackets (<>), underscores (_), tildes (~), brackets ([) or braces ({)\n"
    "- No URLs, no file paths, no HTML tags\n"
    "- No numbers written as digits — spell them out (e.g. 'three' not '3')\n"
    "- Use only plain words, commas, periods, question marks and exclamation marks\n"
    "- Write as if you are speaking to someone naturally\n"
    "- REPLY IN ENGLISH\n\n{text}"
)

_SUMMARIZE_PROMPT_PT = (
    "Você está convertendo uma mensagem escrita em fala para alguém que "
    "JÁ PODE VER o texto completo na tela. A pessoa clicou em 'ouvir' para "
    "ouvir a CONCLUSÃO — não um resumo do que já leu.\n\n"
    "Produza 1-2 parágrafos falados (menos de 150 palavras no total) com "
    "a CONCLUSÃO:\n"
    "- Qual foi a resposta ou resultado?\n"
    "- Que ação deve ser tomada?\n"
    "- Qual é o principal insight ou decisão?\n\n"
    "REGRAS:\n"
    "- Pule o caminho — dê o destino\n"
    "- NÃO rele liste passos, código ou raciocínio intermediário\n"
    "- Se o texto tem uma resposta clara, comece por ela\n"
    "- Se é uma comparação, dê a recomendação\n"
    "- Se é uma explicação, dê a conclusão\n"
    "- Sem marcadores, sem markdown, sem títulos, sem blocos de código\n"
    "- Sem asteriscos, cerquilhas, crases, traços, pipes, sinais de maior/menor, "
    "sublinhados, tils, colchetes ou chaves\n"
    "- Sem URLs, caminhos de arquivo ou tags HTML\n"
    "- Sem números em dígitos — escreva por extenso (ex: 'três' não '3')\n"
    "- Use apenas palavras, vírgulas, pontos, pontos de interrogação e exclamação\n"
    "- Escreva como se estivesse falando com alguém naturalmente\n"
    "- RESPONDA EM PORTUGUÊS BRASILEIRO\n\n{text}"
)

# Last-ditch retry prompts when the structured complete-ack prompt comes
# back empty. Plain "give the conclusion" works on weaker local models that
# choke on multi-section instructions.
_SIMPLE_SUMMARY_PROMPT_EN = (
    "Give the CONCLUSION of this text in 1 short spoken sentence, at most "
    "45 words — the final answer, result, or takeaway. Not a summary — "
    "the bottom line. Plain English, no markdown. No asterisks, hashes, "
    "backticks, or formatting symbols.\n\n{reply}"
)
_SIMPLE_SUMMARY_PROMPT_PT = (
    "Dê a CONCLUSÃO deste texto em 1 frase curta falada, no máximo 45 "
    "palavras — a resposta final, resultado ou principal ponto. Não um "
    "resumo — a conclusão. Português simples, sem markdown. Sem "
    "asteriscos, cerquilhas, crases ou símbolos de formatação.\n\n{reply}"
)


def _start_prompt(lang: str, user_text: str) -> str:
    template = _START_PROMPT_PT if lang == "pt" else _START_PROMPT_EN
    return template.format(user_text=user_text)


def _complete_prompt(lang: str, user_text: str, full_reply: str) -> str:
    """Build the completion-summary prompt. ``user_text`` is accepted for
    historical-API compat (callers still pass it) but the new minimal
    prompts only need the agent's reply text — the user's original
    request is implicit in the reply context."""
    _ = user_text  # intentionally unused
    template = _COMPLETE_PROMPT_PT if lang == "pt" else _COMPLETE_PROMPT_EN
    return template.format(full_reply=full_reply)


def _summarize_prompt(lang: str, text: str) -> str:
    template = _SUMMARIZE_PROMPT_PT if lang == "pt" else _SUMMARIZE_PROMPT_EN
    return template.format(text=text)


@dataclass
class _AckTrigger:
    user_text: str
    session_id: str
    full_reply: str = ""


def _detect_lang_short(text: str, *, fallback: str = "en") -> str:
    """Best-effort 2-letter ISO code from text. Returns ``fallback`` on
    failure (defaults to "en"). Pass the user's UI language as fallback
    for voice messages — when the user dictates without typing, the
    transcript hasn't run yet and ``user_text`` is empty, so we'd
    otherwise default to English regardless of who they are."""
    sample = (text or "").strip()
    if len(sample) < 3:
        return _short_code(fallback)
    try:
        from langdetect import DetectorFactory, detect  # type: ignore

        DetectorFactory.seed = 0
        code = detect(sample) or fallback
        return _short_code(code)
    except Exception:  # noqa: BLE001
        return _short_code(fallback)


def _short_code(lang: str | None) -> str:
    """Normalize 'pt-BR' / 'pt_BR' / 'PT' → 'pt'. Empty / None → 'en'."""
    if not lang:
        return "en"
    return lang.split("-")[0].split("_")[0].lower()


def _ack_lang(trigger: "_AckTrigger", cfg: NexusConfig) -> str:
    """Pick a language for an ack. When ``cfg.tts.voice_language`` is set,
    use it directly — skip unreliable detection on short utterances.
    Otherwise detect from the user's request text; if that's empty
    (typical for pure-voice messages), fall back to the agent's reply,
    and finally to the user's configured UI language."""
    override = getattr(cfg.tts, "voice_language", "").strip()
    if override:
        return _short_code(override)
    ui_lang = _short_code(getattr(cfg.ui, "language", None))
    detected = _detect_lang_short(trigger.user_text, fallback=ui_lang)
    if detected != "en" or trigger.user_text.strip():
        return detected
    return _detect_lang_short(trigger.full_reply or "", fallback=ui_lang)


# Fallback text used when the LLM returns empty — the user dictated and
# expects to hear SOMETHING. Keys are 2-letter ISO codes (with "en" the
# universal fallback).
_FALLBACK_START = {
    "en": "Looking that up, one moment.",
    "pt": "Tô olhando isso, um momento.",
    "es": "Lo estoy buscando, un momento.",
}
_FALLBACK_COMPLETE_GENERIC = {
    "en": "Done. The full answer is in the chat.",
    "pt": "Pronto. A resposta completa está no chat.",
    "es": "Listo. La respuesta completa está en el chat.",
}


def _truncate_for_speech(text: str, *, max_words: int = 15) -> str:
    """Take the first N words of text, ending on a sentence boundary
    when possible. Used as the last-resort fallback for the completion
    ack when both the structured prompt and the simpler retry come back
    empty."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    cleaned = cleaned.replace("**", "").replace("`", "").replace("#", "")
    words = cleaned.split()
    if len(words) <= max_words:
        return cleaned
    snippet = " ".join(words[:max_words])
    for end in (".", "!", "?", ";"):
        idx = snippet.rfind(end)
        if idx > 30:
            return snippet[: idx + 1]
    return snippet + "…"


async def _generate_text(
    agent: "Agent",
    cfg: NexusConfig,
    prompt: str,
    *,
    max_tokens: int = 400,
) -> str:
    from .agent.llm import ChatMessage, Role

    # Prefer a dedicated ack model (fast/cheap) when configured;
    # otherwise fall back to the agent's default model.
    target = cfg.tts.ack_model or cfg.agent.default_model or ""
    if not target:
        log.warning(
            "[voice_ack] no ack_model or default_model set — trying "
            "default provider, but you should configure a model in settings"
        )
    provider, upstream = agent._resolve_provider(target)
    if provider is None:
        log.warning("[voice_ack] _resolve_provider returned None for model=%r", target)
        return ""
    try:
        no_think = {
            "thinking": {"type": "disabled"},
            "enable_thinking": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        resp = await provider.chat(
            [ChatMessage(role=Role.USER, content=prompt)],
            model=upstream,
            max_tokens=max_tokens,
            extra_payload=no_think,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never break the turn
        log.warning("[voice_ack] LLM call failed: %s", exc, exc_info=True)
        return ""
    raw = (resp.content or "").strip()
    stop_reason = getattr(resp, "stop_reason", None)
    if not raw:
        if str(stop_reason) == "length" or str(stop_reason).endswith(".LENGTH"):
            log.warning(
                "[voice_ack] LLM hit max_tokens with no visible content — "
                "this is usually a reasoning model that needs more budget. "
                "model=%r usage=%s",
                upstream or target, getattr(resp, "usage", None),
            )
        else:
            log.warning(
                "[voice_ack] LLM returned empty content (model=%r stop_reason=%s usage=%s)",
                upstream or target, stop_reason, getattr(resp, "usage", None),
            )
    # Models love quoting; strip surrounding punctuation.
    return raw.strip('"').strip("'").strip()


async def _publish(
    *,
    store: "SessionStore",
    session_id: str,
    kind: str,
    transcript: str,
    cfg: NexusConfig,
) -> None:
    from .server.events import SessionEvent

    audio_b64: str | None = None
    audio_mime: str = ""
    try:
        result = await synthesize(transcript, cfg=cfg.tts)
    except TTSError as exc:
        log.warning("[voice_ack/%s] Piper synth failed: %s — UI will fall back to Web Speech",
                    kind, exc)
        result = None
    except Exception:  # noqa: BLE001
        log.exception("[voice_ack/%s] Piper synth unexpected failure", kind)
        result = None
    if result and result.audio:
        audio_b64 = base64.b64encode(result.audio).decode("ascii")
        audio_mime = result.mime
        log.warning("[voice_ack/%s] Piper produced %d bytes (%s)",
                    kind, len(result.audio), result.mime)
    payload = {
        "kind": kind,
        "transcript": transcript,
        "audio_b64": audio_b64,
        "audio_mime": audio_mime,
        # Carried for the UI's Web Speech fallback when audio_b64 is null —
        # backend defaults are good enough; the UI uses them as hints only.
        "voice": "",
        "language": "",
        "speed": 1.0,
    }
    store.publish(session_id, SessionEvent(kind="voice_ack", data=payload))
    log.warning("[voice_ack/%s] published to session=%s (audio_b64=%s)",
                kind, session_id, "yes" if audio_b64 else "no — fallback to Web Speech")


async def emit_start_ack(
    *,
    agent: "Agent",
    store: "SessionStore",
    trigger: _AckTrigger,
    cfg: NexusConfig | None = None,
) -> None:
    cfg = cfg or load_config()
    if not cfg.tts.enabled or not cfg.tts.ack_enabled:
        log.info(
            "[voice_ack/start] skipped sess=%s (tts.enabled=%s ack_enabled=%s)",
            trigger.session_id, cfg.tts.enabled, cfg.tts.ack_enabled,
        )
        return
    lang = _ack_lang(trigger, cfg)
    log.info("[voice_ack/start] firing sess=%s lang=%s", trigger.session_id, lang)
    # When user_text is empty (pure-voice message before transcription
    # ran) the prompt has nothing to confirm. Skip straight to the
    # template so we don't ask the LLM to acknowledge thin air.
    if not trigger.user_text.strip():
        text = _FALLBACK_START.get(lang, _FALLBACK_START["en"])
        log.info("[voice_ack/start] empty user_text — using template (lang=%s): %r",
                 lang, text)
    else:
        prompt = _start_prompt(lang, trigger.user_text[:300])
        text = await _generate_text(agent, cfg, prompt, max_tokens=25)
        if not text:
            text = _FALLBACK_START.get(lang, _FALLBACK_START["en"])
            log.info("[voice_ack/start] using template fallback (lang=%s): %r", lang, text)
    log.info("[voice_ack/start] transcript=%r", text)
    await _publish(
        store=store, session_id=trigger.session_id,
        kind="start", transcript=text, cfg=cfg,
    )


async def emit_completion_ack(
    *,
    agent: "Agent",
    store: "SessionStore",
    trigger: _AckTrigger,
    cfg: NexusConfig | None = None,
) -> None:
    cfg = cfg or load_config()
    if not cfg.tts.enabled or not cfg.tts.ack_enabled:
        log.info(
            "[voice_ack/complete] skipped sess=%s (tts.enabled=%s ack_enabled=%s)",
            trigger.session_id, cfg.tts.enabled, cfg.tts.ack_enabled,
        )
        return
    cleaned_reply = _truncate_for_speech(trigger.full_reply, max_words=999)
    if not cleaned_reply:
        log.info("[voice_ack/complete] empty reply — skipping sess=%s",
                 trigger.session_id)
        return

    if len(cleaned_reply.split()) <= 15:
        log.info("[voice_ack/complete] short reply (%d words) — speaking directly sess=%s",
                 len(cleaned_reply.split()), trigger.session_id)
        await _publish(
            store=store, session_id=trigger.session_id,
            kind="complete", transcript=cleaned_reply, cfg=cfg,
        )
        return

    lang = _ack_lang(trigger, cfg)
    log.info("[voice_ack/complete] firing sess=%s lang=%s reply_chars=%d",
             trigger.session_id, lang, len(trigger.full_reply))
    prompt = _complete_prompt(
        lang,
        trigger.user_text[:300] or "(voice message)",
        trigger.full_reply[:1500],
    )
    text = await _generate_text(agent, cfg, prompt, max_tokens=120)
    if not text:
        log.info("[voice_ack/complete] structured prompt empty — retrying simpler")
        simple = _SIMPLE_SUMMARY_PROMPT_PT if lang == "pt" else _SIMPLE_SUMMARY_PROMPT_EN
        text = await _generate_text(
            agent, cfg, simple.format(reply=trigger.full_reply[:1200]),
            max_tokens=120,
        )
    if not text:
        snippet = _truncate_for_speech(trigger.full_reply, max_words=45)
        if snippet:
            preamble = "Resultado: " if lang == "pt" else "Result: "
            text = preamble + snippet
            log.info("[voice_ack/complete] using snippet fallback with preamble (lang=%s)",
                     lang)
        else:
            text = _FALLBACK_COMPLETE_GENERIC.get(lang, _FALLBACK_COMPLETE_GENERIC["en"])
            log.info("[voice_ack/complete] using generic template (lang=%s)", lang)
        log.info("[voice_ack/complete] LLM empty — using generic template (lang=%s): %r",
                 lang, text)
    log.info("[voice_ack/complete] transcript=%r", text)
    await _publish(
        store=store, session_id=trigger.session_id,
        kind="complete", transcript=text, cfg=cfg,
    )


async def summarize_long_text(
    *,
    agent: "Agent",
    text: str,
    cfg: NexusConfig | None = None,
) -> str:
    """Summarize a long text into 1-2 short paragraphs in its own language.

    Used by the /tts/synthesize route to cap click-to-listen audio at a
    sane length. Returns empty string when the LLM call fails — caller
    should fall back to a generic message.
    """
    cfg = cfg or load_config()
    lang = _detect_lang_short(text)
    prompt = _summarize_prompt(lang, text[:8000])
    return await _generate_text(agent, cfg, prompt)


async def emit_user_notification(
    *,
    store: "SessionStore",
    session_id: str,
    message: str,
    speak: bool,
    cfg: NexusConfig | None = None,
) -> None:
    """Publish a `voice_ack` event with the agent-supplied message.

    Used by the ``notify_user`` tool. When ``speak`` is True (voice-input
    turn), the message is also synthesized through Piper so the user
    hears it. When False, the event still goes through (the UI surfaces
    it as a toast regardless) — audio bytes are just absent.
    """
    cfg = cfg or load_config()
    if not cfg.tts.enabled:
        # Master switch — no toast, no audio. The agent's reply is still
        # in the chat; the toast was just an accessibility nicety.
        return
    text = (message or "").strip()
    if not text:
        return
    log.warning("[voice_ack/notify] sess=%s speak=%s msg=%r",
                session_id, speak, text[:80])
    if speak:
        await _publish(
            store=store, session_id=session_id,
            kind="notify", transcript=text, cfg=cfg,
        )
        return
    # Text-mode: publish the same event but with no audio bytes. The UI
    # surfaces it as a toast; the player short-circuits on audio_b64=None
    # AND the user wasn't listening anyway.
    from .server.events import SessionEvent
    store.publish(session_id, SessionEvent(kind="voice_ack", data={
        "kind": "notify",
        "transcript": text,
        "audio_b64": None,
        "audio_mime": "",
        "voice": "",
        "language": "",
        "speed": 1.0,
    }))
    log.warning("[voice_ack/notify] published TEXT-only (no audio) to sess=%s",
                session_id)


# Re-exported for chat_stream.py + tts route — single import surface.
__all__ = [
    "_AckTrigger",
    "_detect_lang_short",
    "emit_start_ack",
    "emit_completion_ack",
    "emit_user_notification",
    "summarize_long_text",
]
