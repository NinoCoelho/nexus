"""Multimodal helpers — mime sniff, PDF text extraction, audio transcription.

Used by the LLM provider encoders when a chat message carries non-text
``ContentPart``s and the active model can't handle them natively. Each
helper degrades gracefully:

* ``sniff_mime(path)`` falls back to ``application/octet-stream`` instead
  of raising.
* ``extract_text_from_pdf(data)`` returns an empty string when ``pypdf``
  isn't installed and logs a warning — the encoder substitutes a
  breadcrumb so the conversation still proceeds.
* ``transcribe_bytes(data, suffix)`` reuses the existing transcription
  config (faster-whisper local or OpenAI-compat remote) and returns
  ``""`` on failure, again leaving the breadcrumb path.

Kept tiny on purpose: anything bigger should go in its own module.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import tempfile

log = logging.getLogger(__name__)


_TEXT_EXTRACTABLE_MIMES = frozenset(
    [
        "application/pdf",
        "text/plain",
        "text/markdown",
        "text/csv",
    ]
)

_AUDIO_MIME_PREFIX = "audio/"
_IMAGE_MIME_PREFIX = "image/"


def sniff_mime(path: str) -> str:
    """Best-effort mime guess from filename. Returns octet-stream on miss."""
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def is_image(mime: str) -> bool:
    return mime.startswith(_IMAGE_MIME_PREFIX)


def is_audio(mime: str) -> bool:
    return mime.startswith(_AUDIO_MIME_PREFIX)


def is_pdf(mime: str) -> bool:
    return mime == "application/pdf"


def extract_text_from_document(data: bytes, mime: str) -> str:
    """Extract a plaintext approximation of a document's content.

    Supports PDF (via ``pypdf``), plus ``text/*`` mimes which are decoded
    directly. Returns ``""`` on any failure — caller should treat empty
    as "fall back to a breadcrumb" rather than retry.
    """
    if mime.startswith("text/"):
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""
    if not is_pdf(mime):
        return ""
    try:
        import pypdf  # type: ignore[import-not-found]
    except ImportError:
        log.warning(
            "pypdf not installed; PDF attachments will not be summarized. "
            "Install via `uv pip install pypdf` (already in pyproject)."
        )
        return ""
    import io

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        chunks: list[str] = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        return "\n\n".join(c.strip() for c in chunks if c.strip())
    except Exception:  # noqa: BLE001
        log.warning("pypdf: failed to read PDF document", exc_info=True)
        return ""


async def transcribe_bytes(data: bytes, mime: str = "audio/wav") -> str:
    """Transcribe an audio blob using the configured TranscriptionConfig.

    Reuses ``server.transcribe._local_transcribe`` / ``_remote_transcribe``
    so the user doesn't double-configure. Returns ``""`` on failure.
    """
    if not data:
        return ""
    suffix = mimetypes.guess_extension(mime) or ".wav"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="nexus_audio_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        from .config_file import load as load_config
        from .server.transcribe import _local_transcribe, _remote_transcribe

        cfg = load_config().transcription
        if cfg.mode == "remote":
            return await _remote_transcribe(tmp_path, "attachment" + suffix, cfg)
        return await asyncio.to_thread(_local_transcribe, tmp_path, cfg)
    except Exception:  # noqa: BLE001
        log.warning("transcribe_bytes failed", exc_info=True)
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def read_vault_bytes(vault_path: str) -> bytes:
    """Read raw bytes from a vault-relative path. Raises FileNotFoundError
    if the path is missing or not a regular file."""
    from .vault import resolve_path

    full = resolve_path(vault_path)
    if not full.is_file():
        raise FileNotFoundError(f"vault: no such file {vault_path!r}")
    return full.read_bytes()


async def materialize_message(message, capabilities: set[str]):
    """Lower non-native ``ContentPart``s to text before provider encoding.

    The active model's ``capabilities`` (from
    ``providers.catalog.capabilities_for_model_name``) decide what stays
    as-is vs. what gets transcribed/extracted to a text breadcrumb:

    * ``image`` with ``"vision"`` capability  → pass through.
    * ``image`` without vision               → text breadcrumb.
    * ``audio`` with ``"audio"`` capability  → pass through.
    * ``audio`` without audio                → transcribe via faster-whisper
      and return a ``[transcribed audio]: ...`` text part.
    * ``document`` with ``"document"`` cap   → pass through (Anthropic).
    * ``document`` without that capability   → extract text (PDF/text) and
      return a ``[document attached]: ...`` text part.

    Non-multipart messages (``content: str``) are returned unchanged.
    """
    from .agent.llm.types import ChatMessage, ContentPart

    if not isinstance(message, ChatMessage):
        return message
    if not isinstance(message.content, list):
        return message

    new_parts: list[ContentPart] = []
    for part in message.content:
        if part.kind == "text":
            new_parts.append(part)
            continue
        path = part.vault_path or ""
        mime = part.mime_type or sniff_mime(path)
        label = path.rsplit("/", 1)[-1] if path else part.kind

        if part.kind == "image":
            if "vision" in capabilities:
                new_parts.append(part)
            else:
                new_parts.append(
                    ContentPart(
                        kind="text",
                        text=(
                            f"[image attached: {label} — current model does "
                            "not support vision]"
                        ),
                    )
                )
            continue

        if part.kind == "audio":
            if "audio" in capabilities:
                new_parts.append(part)
                continue
            try:
                data = read_vault_bytes(path)
                text = await transcribe_bytes(data, mime)
            except FileNotFoundError:
                new_parts.append(
                    ContentPart(
                        kind="text",
                        text=f"[audio attached: {label} — file missing]",
                    )
                )
                continue
            if text:
                new_parts.append(
                    ContentPart(kind="text", text=f"[transcribed audio]: {text}")
                )
            else:
                new_parts.append(
                    ContentPart(
                        kind="text",
                        text=(
                            f"[audio attached: {label} — transcription "
                            "unavailable]"
                        ),
                    )
                )
            continue

        if part.kind == "document":
            if "document" in capabilities:
                new_parts.append(part)
                continue
            try:
                data = read_vault_bytes(path)
            except FileNotFoundError:
                new_parts.append(
                    ContentPart(
                        kind="text",
                        text=f"[document attached: {label} — file missing]",
                    )
                )
                continue
            text = extract_text_from_document(data, mime)
            if text:
                new_parts.append(
                    ContentPart(
                        kind="text",
                        text=f"[document attached: {label}]\n{text}",
                    )
                )
            else:
                new_parts.append(
                    ContentPart(
                        kind="text",
                        text=(
                            f"[document attached: {label} — text extraction "
                            "unavailable]"
                        ),
                    )
                )
            continue

    return message.model_copy(update={"content": new_parts})


async def materialize_messages(messages, capabilities: set[str]):
    """Apply :func:`materialize_message` to every entry in a list. Returns
    a new list; the input is not mutated."""
    import asyncio as _asyncio

    return await _asyncio.gather(
        *[materialize_message(m, capabilities) for m in messages]
    )
