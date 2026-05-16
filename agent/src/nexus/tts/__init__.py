"""Text-to-speech — bundled Piper engine, no third-party providers.

Two default voices auto-download to ~/.nexus/tts/piper on first daemon
start: ``en_GB-northern_english_male-medium`` and ``pt_BR-faber-medium``.
Language detection (via ``langdetect``) picks between them per-utterance.
"""

from __future__ import annotations

from .base import SynthResult, TTSError
from .dispatch import (
    DEFAULT_VOICES,
    list_voices,
    resolve_voice_for_text,
    synthesize,
)

__all__ = [
    "DEFAULT_VOICES",
    "SynthResult",
    "TTSError",
    "list_voices",
    "resolve_voice_for_text",
    "synthesize",
]
