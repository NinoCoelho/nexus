"""BuiltinExtractor class — spaCy NER + fastembed entity/relation extraction."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ._constants import (
    RELATION_PROTOTYPES,
    SPACY_LABEL_HINTS,
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
        max_tokens: int | None = None,
    ) -> Any:
        await self._ensure_loaded()

        # Multi-turn → glean pass; our first pass already got everything.
        if len(messages) > 1:
            return _make_response({"entities": [], "relations": []})

        prompt = messages[0].content if hasattr(messages[0], "content") else str(messages[0])
        entity_types, core_relations, text = _parse_prompt(prompt)
        if not text:
            return _make_response({"entities": [], "relations": []})

        snippet = text[:3000]
        lang = _detect_lang(snippet)
        nlp = self._nlps.get(lang) or self._nlps["en"]
        loop = asyncio.get_running_loop()
        doc = await loop.run_in_executor(None, lambda: nlp(snippet))

        entities = await self._extract_entities(doc, entity_types)
        relations = await self._extract_relations(doc, entities, core_relations)

        return _make_response({"entities": entities, "relations": relations})

    # -- entity extraction --------------------------------------------------

    async def _extract_entities(
        self, doc: Any, entity_types: list[str],
    ) -> list[dict[str, str]]:
        seen_spans: set[tuple[int, int]] = set()
        entity_map: dict[str, dict[str, str]] = {}
        # (name, spaCy label) pairs that need embedding classification.
        pending: list[tuple[str, str]] = []

        def _record(name: str, label: str, description: str) -> None:
            if name in entity_map:
                return
            direct_type = SPACY_LABEL_MAP.get(label, "")
            if direct_type and (not entity_types or direct_type in entity_types):
                entity_map[name] = {
                    "name": name,
                    "type": direct_type,
                    "description": description,
                }
            elif entity_types:
                pending.append((name, label))
            else:
                entity_map[name] = {
                    "name": name,
                    "type": direct_type or "concept",
                    "description": description,
                }

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

            _record(name, label, f"{label} mentioned in text")

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

            if name not in entity_map and name not in {n for n, _ in pending}:
                pending.append((name, ""))
                seen_spans.add(span)

        # Phase 3 — batch type classification for everything pending. One
        # embedder call for all names + one for any missing type prototypes;
        # the old per-name embed calls dominated extraction latency.
        if pending and entity_types:
            # .get() — labels outside the hints map (e.g. pt's MISC, or any
            # model-specific label) must fall through to a generic hint, not
            # crash the whole chunk's extraction.
            texts = [
                f"{name} — {SPACY_LABEL_HINTS.get(label) or (label.lower().replace('_', ' ') if label else '')}"
                if label else name
                for name, label in pending
            ]
            name_embs = await self._embedder.embed(texts)

            proto_embs: dict[str, list[float]] = {}
            missing = [t for t in entity_types if t not in self._type_embs]
            if missing:
                missing_embs = await self._embedder.embed(
                    [t.replace("_", " ") for t in missing]
                )
                for t, e in zip(missing, missing_embs):
                    self._type_embs[t] = e
            for t in entity_types:
                proto = self._type_embs.get(t)
                if proto is not None:
                    proto_embs[t] = proto

            for (name, label), name_emb in zip(pending, name_embs):
                etype = self._best_type(name_emb, proto_embs, threshold=0.35)
                if etype is None:
                    # Weak match — fall back to the legacy label mapping when
                    # it names a valid type, otherwise drop the entity.
                    legacy = SPACY_LABEL_MAP.get(label, "")
                    etype = legacy if legacy in entity_types else None
                if etype:
                    entity_map[name] = {
                        "name": name,
                        "type": etype,
                        "description": f"{label or 'entity'} mentioned in text",
                    }

        return list(entity_map.values())

    def _best_type(
        self,
        name_emb: list[float],
        proto_embs: dict[str, list[float]],
        *,
        threshold: float,
    ) -> str | None:
        if not proto_embs:
            return None
        best_type: str | None = None
        best_score = -1.0
        for etype, proto in proto_embs.items():
            score = _cosine_sim(name_emb, proto)
            if score > best_score:
                best_score = score
                best_type = etype
        return best_type if best_score >= threshold else None

    # -- type classification (single-entity convenience API) ----------------

    async def _classify_type(
        self, name: str, entity_types: list[str], hint: str,
    ) -> str | None:
        if not entity_types:
            return hint or "concept"
        if hint and hint in entity_types:
            return hint
        if not self._type_embs:
            return hint if hint in entity_types else entity_types[0]

        name_emb = (await self._embedder.embed([name]))[0]
        proto_embs: dict[str, list[float]] = {}
        for etype in entity_types:
            proto = self._type_embs.get(etype)
            if proto is None:
                proto = (await self._embedder.embed([etype.replace("_", " ")]))[0]
                self._type_embs[etype] = proto
            proto_embs[etype] = proto
        best = self._best_type(name_emb, proto_embs, threshold=0.35)
        if best is None:
            return hint if hint in entity_types else None
        return best

    # -- relation extraction ------------------------------------------------

    async def _extract_relations(
        self, doc: Any, entities: list[dict[str, str]],
        core_relations: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if len(entities) < 2:
            return []

        rel_set = set(core_relations) if core_relations else set()
        names = [e["name"] for e in entities]
        lower_names = {n.lower(): n for n in names}

        # Pair collection: entities co-occurring in a sentence, limited to a
        # proximity window (~30 tokens) so a long sentence listing many
        # entities doesn't produce a fully-connected clique. Sentence text
        # and co-occurrence count are kept for classification + strength.
        pair_context: dict[tuple[str, str], list[str]] = {}
        for sent in doc.sents:
            sent_lower = sent.text.lower()
            positions: list[tuple[int, str]] = []
            for ln, name in lower_names.items():
                idx = sent_lower.find(ln)
                if idx >= 0:
                    positions.append((idx, name))
            positions.sort()
            for i, (p1, n1) in enumerate(positions):
                for p2, n2 in positions[i + 1:]:
                    if p2 - p1 > 160:  # characters ≈ 30 tokens
                        break
                    if n1 == n2:
                        continue
                    key = (n1, n2) if n1 <= n2 else (n2, n1)
                    pair_context.setdefault(key, []).append(sent.text)

        if not pair_context:
            return []

        # Batch classification: embed each pair's shortest containing
        # sentence (the most focused context) and match against relation
        # prototypes. Embedding the sentence — not "{head} {tail}" — is the
        # whole fix: the verb/semantics of the sentence decide the relation.
        rels = core_relations if core_relations else list(self._rel_embs.keys())
        if not rels:
            rels = ["related_to"]

        contexts = [min(sents, key=len) for sents in pair_context.values()]
        ctx_embs = await self._embedder.embed(contexts)

        missing = [r for r in rels if r not in self._rel_embs]
        if missing:
            missing_embs = await self._embedder.embed([r.replace("_", " ") for r in missing])
            for r, e in zip(missing, missing_embs):
                self._rel_embs[r] = e

        relations: list[dict[str, Any]] = []
        for (head, tail), ctx_emb in zip(pair_context.keys(), ctx_embs):
            best_rel = "related_to"
            best_score = -1.0
            for rel in rels:
                proto = self._rel_embs.get(rel)
                if proto is None:
                    continue
                score = _cosine_sim(ctx_emb, proto)
                if score > best_score:
                    best_score = score
                    best_rel = rel

            # Strength grows with how often the pair co-occurs across
            # sentences in this chunk — repeated mentions mean a real
            # relationship, a single passing co-mention stays weak.
            mentions = len(pair_context[(head, tail)])
            strength = min(2.0 + mentions * 2.0, 10.0)

            relations.append({
                "head": head,
                "relation": best_rel,
                "tail": tail,
                "description": f"{head} {best_rel.replace('_', ' ')} {tail}",
                "strength": strength,
                "custom": bool(rel_set) and best_rel not in rel_set,
            })

        relations.sort(key=lambda r: -float(r["strength"]))
        return relations[:20]
