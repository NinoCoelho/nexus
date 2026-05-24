"""TTS engine protocol + shared types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class TTSError(RuntimeError):
    """Raised when synthesis fails for a recoverable reason (bad config,
    network error, missing voice). The route catches this and returns a
    structured 4xx/5xx so the UI can fall back to webspeech."""


@dataclass
class SynthResult:
    # None means "render client-side" (webspeech engine).
    audio: bytes | None
    mime: str  # "audio/wav", "audio/mpeg", or "" when audio is None


@dataclass
class Voice:
    id: str
    name: str
    language: str  # BCP-47 like "en-US", "pt-BR"


@runtime_checkable
class TTSEngine(Protocol):
    name: str

    async def synthesize(self, text: str, voice: str, speed: float) -> SynthResult: ...

    async def list_voices(self, language: str | None = None) -> list[Voice]: ...
