"""Builtin entity extractor using spaCy NER + fastembed similarity.

Provides zero-config entity extraction for GraphRAG when no external
LLM is configured. Uses spaCy's ``en_core_web_sm`` model (~12 MB) for
named-entity recognition and the builtin fastembed model for entity-type
classification of entities that don't map cleanly from spaCy labels.

Implements the ``chat`` protocol expected by
:class:`~loom.store.graphrag.GraphRAGEngine` so it can be used as a
drop-in replacement for an LLM-based extractor.
"""

from ._constants import (
    RELATION_PROTOTYPES,
    SPACY_LABEL_MAP,
    TYPE_PROTOTYPES,
    _NUMERIC_RE,
    _SKIP_LABELS,
    _STOP_NOUNS,
)
from ._extractor import BuiltinExtractor

_instance: BuiltinExtractor | None = None


def get_builtin_extractor() -> BuiltinExtractor:
    global _instance
    if _instance is None:
        _instance = BuiltinExtractor()
    return _instance


__all__ = [
    "BuiltinExtractor",
    "get_builtin_extractor",
    "SPACY_LABEL_MAP",
    "TYPE_PROTOTYPES",
    "RELATION_PROTOTYPES",
    "_SKIP_LABELS",
    "_STOP_NOUNS",
    "_NUMERIC_RE",
]
