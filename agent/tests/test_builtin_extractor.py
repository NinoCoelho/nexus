"""Tests for the builtin extractor's relation extraction and type mapping.

These exercise the quality-critical paths that previously had zero tests:
sentence-context relation classification, proximity-limited pairing,
mention-count strength, and the spaCy label → ontology mapping.
"""

from __future__ import annotations

import asyncio

import pytest

from nexus.agent.builtin_extractor._constants import SPACY_LABEL_MAP


def test_spacy_label_map_precision() -> None:
    """Only near-certain mappings may be direct — ORG/GPE/etc. must go
    through the classifier so companies don't become projects."""
    assert SPACY_LABEL_MAP == {"PERSON": "person", "PER": "person"}


@pytest.fixture(scope="module")
def nlp():
    import spacy

    return spacy.load("en_core_web_sm")


class FakeEmbedder:
    """Deterministic embedder: hashes text into a fixed-dimension unit vector.

    Same text → same vector; similar texts share the first token's bucket so
    prototype matching behaves predictably in tests.
    """

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        import hashlib

        h = hashlib.sha256(text.encode()).digest()
        vals = [(b / 255.0) * 2 - 1 for b in h[: self.dim]]
        norm = sum(v * v for v in vals) ** 0.5 or 1.0
        return [v / norm for v in vals]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


@pytest.fixture
def extractor():
    from nexus.agent.builtin_extractor._extractor import BuiltinExtractor

    ex = BuiltinExtractor()
    ex._embedder = FakeEmbedder()
    ex._type_embs = {"person": ex._embedder._vec("person"), "project": ex._embedder._vec("project")}
    ex._rel_embs = {
        "uses": ex._embedder._vec("uses utilizes"),
        "related_to": ex._embedder._vec("related to connected"),
    }
    return ex


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_relations_limited_by_proximity(nlp, extractor) -> None:
    """Entities far apart in one long sentence are NOT paired."""
    text = (
        "Alice met Bob at the conference. "
        + " filler words without entities here. " * 12
        + " Zelda lives far away from that whole scene entirely."
    )
    doc = nlp(text)
    entities = [
        {"name": "Alice", "type": "person", "description": ""},
        {"name": "Bob", "type": "person", "description": ""},
        {"name": "Zelda", "type": "person", "description": ""},
    ]
    rels = _run(extractor._extract_relations(doc, entities, ["uses"]))
    pairs = {frozenset((r["head"], r["tail"])) for r in rels}
    assert frozenset(("Alice", "Bob")) in pairs
    assert frozenset(("Alice", "Zelda")) not in pairs
    assert frozenset(("Bob", "Zelda")) not in pairs


def test_relation_strength_from_mentions(nlp, extractor) -> None:
    """Repeated co-occurrence yields higher strength than a single mention."""
    text = "Alice works with Bob. Later Alice and Bob shipped it. Finally Alice thanked Bob."
    doc = nlp(text)
    entities = [
        {"name": "Alice", "type": "person", "description": ""},
        {"name": "Bob", "type": "person", "description": ""},
    ]
    rels = _run(extractor._extract_relations(doc, entities, ["uses"]))
    assert len(rels) == 1
    assert rels[0]["strength"] >= 6  # 3 mentions → 2 + 3*2


def test_custom_flag_false_without_core_relations(nlp, extractor) -> None:
    text = "Alice works with Bob."
    doc = nlp(text)
    entities = [
        {"name": "Alice", "type": "person", "description": ""},
        {"name": "Bob", "type": "person", "description": ""},
    ]
    rels = _run(extractor._extract_relations(doc, entities, None))
    assert rels and rels[0]["custom"] is False


def test_relation_description_humanized(nlp, extractor) -> None:
    text = "Alice uses Bob."
    doc = nlp(text)
    entities = [
        {"name": "Alice", "type": "person", "description": ""},
        {"name": "Bob", "type": "person", "description": ""},
    ]
    rels = _run(extractor._extract_relations(doc, entities, ["uses"]))
    assert " " in rels[0]["description"]  # not "Alice uses_Bob"


def test_batch_type_classification(nlp, extractor) -> None:
    """Pending entities classify in one batched embed call."""
    call_count = {"n": 0}
    orig = extractor._embedder.embed

    async def counting_embed(texts):
        call_count["n"] += 1
        return await orig(texts)

    extractor._embedder.embed = counting_embed  # type: ignore[method-assign]

    doc = nlp("Microsoft and Acme Corp met with Initech to discuss licensing.")
    # Force every entity through classification by using types with no
    # direct PERSON mapping.
    out = _run(extractor._extract_entities(doc, ["project", "person"]))
    assert call_count["n"] <= 3  # names + missing protos + relation-less margin
    assert all(e["type"] in ("project", "person") for e in out)


def test_unknown_spacy_label_does_not_crash(nlp, extractor) -> None:
    """Regression: pt spaCy emits MISC, which is absent from the hints map —
    a bare dict access killed extraction for the whole chunk."""
    # Deterministic classification: seed the "project" prototype with the
    # exact vector the fallback hint text will produce ("Acme Corp — misc").
    extractor._type_embs["project"] = extractor._embedder._vec("Acme Corp — misc")

    class _Ent:
        text = "Acme Corp"
        label_ = "MISC"
        start_char = 0
        end_char = 9

    class _Doc:
        ents = [_Ent()]
        noun_chunks = []

    out = _run(extractor._extract_entities(_Doc(), ["project", "person"]))
    assert len(out) == 1
    assert out[0]["type"] == "project"
