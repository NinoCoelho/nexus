"""Builtin entity extractor using spaCy NER + fastembed similarity.

Provides zero-config entity extraction for GraphRAG when no external
LLM is configured. Uses spaCy's ``en_core_web_sm`` model (~12 MB) for
named-entity recognition and the builtin fastembed model for entity-type
classification of entities that don't map cleanly from spaCy labels.

Implements the ``chat`` protocol expected by
:class:`~loom.store.graphrag.GraphRAGEngine` so it can be used as a
drop-in replacement for an LLM-based extractor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# spaCy NER label → ontology entity type (direct, no embedding needed)
# ---------------------------------------------------------------------------

SPACY_LABEL_MAP: dict[str, str] = {
    "PERSON": "person",
    "ORG": "project",
    "PRODUCT": "technology",
    "GPE": "resource",
    "LOC": "resource",
    "FAC": "resource",
    "EVENT": "concept",
    "WORK_OF_ART": "concept",
    "LAW": "concept",
    "NORP": "concept",
    "LANGUAGE": "concept",
}

# spaCy labels that are NOT knowledge-graph entities — skip entirely
_SKIP_LABELS: frozenset[str] = frozenset({
    "CARDINAL", "DATE", "MONEY", "QUANTITY", "ORDINAL", "PERCENT", "TIME",
})

# ---------------------------------------------------------------------------
# Prototype phrases for embedding-similarity fallback (noun phrases only)
# ---------------------------------------------------------------------------

TYPE_PROTOTYPES: dict[str, list[str]] = {
    "person": ["person individual human being someone"],
    "project": ["project initiative task program undertaking plan"],
    "concept": ["concept idea theory principle notion abstraction topic"],
    "technology": ["technology tool framework software system platform language library"],
    "decision": ["decision choice conclusion judgment determination resolution"],
    "resource": ["resource document material asset reference data source"],
}

RELATION_PROTOTYPES: dict[str, list[str]] = {
    "uses": ["uses utilizes employs leverages applies"],
    "depends_on": ["depends on requires needs relies on necessitates"],
    "part_of": ["part of component of subset of belongs to contained in member of"],
    "created_by": ["created by made by built by developed by authored by designed by"],
    "related_to": ["related to connected to associated with linked to involves"],
}

# Short / generic noun phrases to skip
_STOP_NOUNS: frozenset[str] = frozenset({
    "this", "that", "these", "those", "it", "they", "we", "you", "he", "she",
    "i", "me", "him", "her", "us", "them", "my", "your", "his", "its",
    "our", "their", "what", "which", "who", "whom", "whose",
    "everyone", "everything", "someone", "something", "anyone", "anything",
    "nothing", "nobody", "none", "all", "some", "many", "few", "much",
    "more", "most", "other", "another", "such", "way", "thing", "things",
    "point", "lot", "bit", "part", "rest", "kind", "sort", "case",
    "matter", "reason", "sense", "idea", "fact", "question", "issue",
    "problem", "place", "time", "day", "today", "week", "month", "year",
    "work", "job", "need", "use", "end", "start", "change", "move",
    "look", "try", "help", "call", "talk", "step", "test", "run",
    "example", "data", "info", "list", "note", "notes", "text", "file",
    "content", "line", "section", "name", "number", "key", "value",
    "set", "group", "type", "form", "field", "result", "results",
    "first", "second", "third", "next", "last", "new", "old",
    "good", "bad", "great", "right", "best", "better", "different",
    "important", "main", "simple", "possible", "real", "true",
})

# Regex: skip noun phrases that are purely numeric / money-like
_NUMERIC_RE = re.compile(r'^[\$\€\£]?[\d.,]+%?$')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb + 1e-8)


def _make_response(data: dict[str, Any]) -> Any:
    """Build a :class:`loom.types.ChatResponse` with JSON content."""
    from loom.types import ChatMessage, ChatResponse, Role, StopReason, Usage

    return ChatResponse(
        message=ChatMessage(role=Role.ASSISTANT, content=json.dumps(data)),
        usage=Usage(),
        stop_reason=StopReason.STOP,
        model="builtin-extractor",
    )


# ---------------------------------------------------------------------------
# Prompt parsing
# ---------------------------------------------------------------------------

_TYPES_RE = re.compile(r"Entity types to look for:\s*(.+?)(?:\n\n|\r\n\r\n)", re.DOTALL)
_TEXT_RE = re.compile(r"Text:\n(.+)", re.DOTALL)
_RESPOND_MARKER = "\n\nRespond with ONLY"


def _parse_prompt(prompt: str) -> tuple[list[str], str]:
    """Return ``(entity_types, text)`` from the extraction prompt."""
    entity_types: list[str] = []
    text = ""

    m = _TYPES_RE.search(prompt)
    if m:
        entity_types = [t.strip().lower() for t in m.group(1).split(",") if t.strip()]

    m = _TEXT_RE.search(prompt)
    if m:
        raw = m.group(1)
        idx = raw.find(_RESPOND_MARKER)
        text = (raw[:idx] if idx > 0 else raw).strip()

    return entity_types, text


def _has_capitalized_token(name: str) -> bool:
    """True if the name contains at least one capitalized non-stopword token."""
    for tok in name.split():
        if tok and tok[0].isupper() and tok.lower() not in _STOP_NOUNS:
            return True
    return False


def _is_quality_entity(name: str) -> bool:
    """Gate: reject noise entities before type classification."""
    # Too short
    if len(name) < 3:
        return False
    # Purely numeric / money
    if _NUMERIC_RE.match(name.replace(" ", "")):
        return False
    # Known stop word
    if name.lower() in _STOP_NOUNS:
        return False
    return True


# ---------------------------------------------------------------------------
# BuiltinExtractor
# ---------------------------------------------------------------------------

_instance: BuiltinExtractor | None = None


class BuiltinExtractor:
    """Zero-config entity extractor: spaCy NER + fastembed similarity.

    Implements ``async chat(messages, **kw) -> ChatResponse`` so it can be
    passed directly as ``llm_provider`` to :class:`GraphRAGEngine`.
    """

    def __init__(self) -> None:
        self._nlp: Any = None
        self._embedder: Any = None
        self._type_embs: dict[str, list[float]] = {}
        self._rel_embs: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    # -- lazy init ----------------------------------------------------------

    async def _ensure_loaded(self) -> None:
        if self._nlp is not None:
            return
        async with self._lock:
            if self._nlp is not None:
                return
            loop = asyncio.get_running_loop()

            log.info("[builtin-extractor] loading spaCy en_core_web_sm …")
            self._nlp = await loop.run_in_executor(None, self._load_spacy)

            from .builtin_embedder import get_builtin_embedder
            self._embedder = get_builtin_embedder()

            await self._build_prototype_embeddings()
            log.info("[builtin-extractor] ready (spacy + fastembed)")

    @staticmethod
    def _load_spacy() -> Any:
        import spacy

        # Try loading from our cache first
        cache = Path.home() / ".nexus" / "models" / "spacy" / "en_core_web_sm"
        if cache.is_dir():
            try:
                return spacy.load(str(cache))
            except Exception:
                pass

        try:
            return spacy.load("en_core_web_sm")
        except OSError:
            log.info("[builtin-extractor] downloading en_core_web_sm (~12 MB) …")
            spacy.cli.download("en_core_web_sm")  # type: ignore[attr-defined]
            nlp = spacy.load("en_core_web_sm")
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                nlp.to_disk(str(cache))
            except Exception:
                pass
            return nlp

    async def _build_prototype_embeddings(self) -> None:
        texts: list[str] = []
        keys: list[tuple[str, str]] = []

        for name, phrases in {**TYPE_PROTOTYPES, **RELATION_PROTOTYPES}.items():
            for phrase in phrases:
                keys.append(("type" if name in TYPE_PROTOTYPES else "rel", name))
                texts.append(phrase)

        embs = await self._embedder.embed(texts)
        for (cat, name), emb in zip(keys, embs):
            if cat == "type":
                self._type_embs[name] = emb
            else:
                self._rel_embs[name] = emb

    # -- chat protocol ------------------------------------------------------

    async def chat(
        self,
        messages: list[Any],
        *,
        tools: list[Any] | None = None,
        model: str | None = None,
    ) -> Any:
        await self._ensure_loaded()

        # Multi-turn → glean pass; our first pass already got everything.
        if len(messages) > 1:
            return _make_response({"entities": [], "relations": []})

        prompt = messages[0].content if hasattr(messages[0], "content") else str(messages[0])
        entity_types, text = _parse_prompt(prompt)
        if not text:
            return _make_response({"entities": [], "relations": []})

        # spaCy NLP is CPU-bound
        loop = asyncio.get_running_loop()
        doc = await loop.run_in_executor(None, lambda: self._nlp(text[:3000]))

        entities = await self._extract_entities(doc, entity_types)
        relations = await self._extract_relations(doc, entities)

        return _make_response({"entities": entities, "relations": relations})

    # -- entity extraction --------------------------------------------------

    async def _extract_entities(
        self, doc: Any, entity_types: list[str],
    ) -> list[dict[str, str]]:
        seen_spans: set[tuple[int, int]] = set()
        entity_map: dict[str, dict[str, str]] = {}

        # Phase 1 — spaCy named entities (high confidence)
        for ent in doc.ents:
            span = (ent.start_char, ent.end_char)
            if span in seen_spans:
                continue
            seen_spans.add(span)

            name = ent.text.strip()
            if not _is_quality_entity(name):
                continue

            label = ent.label_

            # Skip non-knowledge-graph entity types
            if label in _SKIP_LABELS:
                continue

            # Direct mapping from spaCy label → ontology type
            direct_type = SPACY_LABEL_MAP.get(label, "")
            if direct_type and direct_type in entity_types:
                etype = direct_type
            elif entity_types:
                # Fallback: embedding similarity
                etype = await self._classify_type(name, entity_types, direct_type)
            else:
                etype = direct_type or "concept"

            if etype:
                entity_map[name] = {
                    "name": name,
                    "type": etype,
                    "description": f"{label} mentioned in text",
                }

        # Phase 2 — noun phrases with proper nouns (only capitalized ones)
        for chunk in doc.noun_chunks:
            span = (chunk.start_char, chunk.end_char)
            # Skip if already covered by a named entity
            if any(s <= span[0] < e or s < span[1] <= e for s, e in seen_spans):
                continue

            name = chunk.text.strip()
            if not _is_quality_entity(name):
                continue

            # MUST have at least one capitalized token to be considered
            if not _has_capitalized_token(name):
                continue

            # Must contain a proper noun or a noun root
            has_content = any(
                t.pos_ in ("PROPN",) and not t.is_stop
                for t in chunk
                if not t.is_punct
            )
            if not has_content:
                continue

            etype = await self._classify_type(name, entity_types, "")
            if etype:
                seen_spans.add(span)
                entity_map[name] = {
                    "name": name,
                    "type": etype,
                    "description": "entity mentioned in text",
                }

        return list(entity_map.values())

    # -- type classification ------------------------------------------------

    async def _classify_type(
        self, name: str, entity_types: list[str], hint: str,
    ) -> str | None:
        if not entity_types:
            return hint or "concept"

        # Direct hint match
        if hint and hint in entity_types:
            return hint

        # Embedding similarity against type prototypes
        if not self._type_embs:
            return hint if hint in entity_types else entity_types[0]

        name_emb = (await self._embedder.embed([name]))[0]

        best_type: str | None = None
        best_score = -1.0

        for etype in entity_types:
            proto = self._type_embs.get(etype)
            if proto is None:
                proto = (await self._embedder.embed([etype.replace("_", " ")]))[0]
                self._type_embs[etype] = proto
            score = _cosine_sim(name_emb, proto)
            if score > best_score:
                best_score = score
                best_type = etype

        # Higher threshold — reject weak matches
        if best_score < 0.35:
            return hint if hint in entity_types else None

        return best_type

    # -- relation extraction ------------------------------------------------

    async def _extract_relations(
        self, doc: Any, entities: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        if len(entities) < 2:
            return []

        relations: list[dict[str, Any]] = []
        seen_pairs: set[frozenset[str]] = set()

        for sent in doc.sents:
            sent_lower = sent.text.lower()
            found = [
                e for e in entities
                if e["name"].lower() in sent_lower
            ]

            for i, e1 in enumerate(found):
                for e2 in found[i + 1:]:
                    pair = frozenset((e1["name"], e2["name"]))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    rel = await self._classify_relation(e1["name"], e2["name"])
                    relations.append({
                        "head": e1["name"],
                        "relation": rel,
                        "tail": e2["name"],
                        "description": f"{e1['name']} {rel} {e2['name']}",
                        "strength": 5,
                        "custom": rel not in {
                            "uses", "depends_on", "part_of",
                            "created_by", "related_to",
                        },
                    })

        return relations[:20]

    async def _classify_relation(self, head: str, tail: str) -> str:
        if not self._rel_embs:
            return "related_to"

        combined = f"{head} {tail}"
        emb = (await self._embedder.embed([combined]))[0]

        best_rel = "related_to"
        best_score = -1.0
        for rel, proto in self._rel_embs.items():
            score = _cosine_sim(emb, proto)
            if score > best_score:
                best_score = score
                best_rel = rel

        return best_rel


def get_builtin_extractor() -> BuiltinExtractor:
    global _instance
    if _instance is None:
        _instance = BuiltinExtractor()
    return _instance
