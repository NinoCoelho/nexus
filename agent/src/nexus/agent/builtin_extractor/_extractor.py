"""BuiltinExtractor class — spaCy NER + fastembed entity/relation extraction."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ._constants import (
    RELATION_PROTOTYPES,
    SPACY_LABEL_MAP,
    TYPE_PROTOTYPES,
    _SKIP_LABELS,
)
from ._helpers import (
    _cosine_sim,
    _has_capitalized_token,
    _is_quality_entity,
    _make_response,
    _parse_prompt,
)

log = logging.getLogger(__name__)

# Languages we ship spaCy NER pipelines for. en is the historical default;
# pt was added when the embedder switched to multilingual.
_SPACY_MODELS: dict[str, str] = {
    "en": "en_core_web_sm",
    "pt": "pt_core_news_sm",
}


def _detect_lang(text: str) -> str:
    """Best-effort language tag — returns one of _SPACY_MODELS keys.

    Why: routes a chunk to the right spaCy NER pipeline. Falls back to ``en``
    on any langdetect failure (empty text, unsupported language, missing
    optional dep) so extraction never breaks because of detection.
    """
    snippet = text.strip()[:600]
    if not snippet:
        return "en"
    try:
        from langdetect import DetectorFactory, detect
        # langdetect is non-deterministic by default; pin the seed once at
        # module level so the same chunk always resolves to the same tag.
        DetectorFactory.seed = 0
        tag = detect(snippet)
    except Exception:
        return "en"
    return tag if tag in _SPACY_MODELS else "en"


class BuiltinExtractor:
    """Zero-config entity extractor: spaCy NER + fastembed similarity.

    Implements ``async chat(messages, **kw) -> ChatResponse`` so it can be
    passed directly as ``llm_provider`` to :class:`GraphRAGEngine`.
    """

    def __init__(self) -> None:
        self._nlps: dict[str, Any] = {}
        self._embedder: Any = None
        self._type_embs: dict[str, list[float]] = {}
        self._rel_embs: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    # -- lazy init ----------------------------------------------------------

    async def _ensure_loaded(self) -> None:
        if self._nlps:
            return
        async with self._lock:
            if self._nlps:
                return
            loop = asyncio.get_running_loop()

            for lang, model_name in _SPACY_MODELS.items():
                log.info("[builtin-extractor] loading spaCy %s …", model_name)
                self._nlps[lang] = await loop.run_in_executor(
                    None, self._load_spacy, model_name,
                )

            from nexus.agent.builtin_embedder import get_builtin_embedder
            self._embedder = get_builtin_embedder()

            await self._build_prototype_embeddings()
            log.info("[builtin-extractor] ready (spacy=%s + fastembed)", list(self._nlps))

    @staticmethod
    def _load_spacy(model_name: str) -> Any:
        import spacy

        # Try loading from our cache first
        cache = Path.home() / ".nexus" / "models" / "spacy" / model_name
        if cache.is_dir():
            try:
                return spacy.load(str(cache))
            except Exception:
                pass

        try:
            return spacy.load(model_name)
        except OSError:
            # Nexus pins en_core_web_sm + pt_core_news_sm as wheels in its
            # pyproject so a normal install picks them up. We only reach
            # this fallback when the install env is stale — typically an
            # old `uv tool install nexus` from before pt was added. The
            # default spacy.cli.download shells out to pip; under a uv
            # tool env the pip wrapper bails with the confusing
            # "No virtual environment found" before eventually recovering.
            log.warning(
                "[builtin-extractor] %s missing from current env — attempting "
                "spacy.cli.download. If this errors with `No virtual environment "
                "found`, run `uv tool upgrade nexus` (or `uv sync` from agent/) "
                "to refresh the install with the bundled wheel.",
                model_name,
            )
            try:
                spacy.cli.download(model_name)  # type: ignore[attr-defined]
            except SystemExit as exc:
                raise RuntimeError(
                    f"failed to install spaCy model {model_name!r}: "
                    f"pip exit code {exc.code}. The model is bundled with "
                    "Nexus — your install env is out of date. Run "
                    "`uv tool upgrade nexus` (if installed via uv tool) or "
                    "`uv sync` from agent/ (if running from source).",
                ) from exc
            nlp = spacy.load(model_name)
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                nlp.to_disk(str(cache))
            except Exception:
                pass
            return nlp

    async def _build_prototype_embeddings(self) -> None:
        # Vault ontology is the source of truth when present; the constants
        # are kept only as the bootstrap fallback for the very first run
        # before ``graphrag_manager.initialize`` has had a chance to seed
        # the vault folder.
        type_protos: dict[str, list[str]] = TYPE_PROTOTYPES
        rel_protos: dict[str, list[str]] = RELATION_PROTOTYPES
        try:
            from nexus.agent.ontology_store import OntologyStore
            store = OntologyStore(Path.home() / ".nexus" / "vault")
            if store.exists():
                snap = store.load()
                type_protos = snap.type_prototypes()
                rel_protos = snap.relation_prototypes()
        except Exception as exc:
            log.warning("[builtin-extractor] using constant prototypes: %s", exc)

        texts: list[str] = []
        keys: list[tuple[str, str]] = []
        for name, phrases in type_protos.items():
            for phrase in phrases:
                keys.append(("type", name))
                texts.append(phrase)
        for name, phrases in rel_protos.items():
            for phrase in phrases:
                keys.append(("rel", name))
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

        # spaCy NLP is CPU-bound. Pick the per-language pipeline once per
        # chunk; en is the safe default if detection fails or returns a
        # language we don't ship a model for.
        snippet = text[:3000]
        lang = _detect_lang(snippet)
        nlp = self._nlps.get(lang) or self._nlps["en"]
        loop = asyncio.get_running_loop()
        doc = await loop.run_in_executor(None, lambda: nlp(snippet))

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
