"""Smoke + cross-lingual sanity tests for the builtin multilingual embedder.

The model download (~220 MB) happens on first call, so these tests are
slow on a cold cache. They're tagged ``slow`` so a default ``pytest`` run
still finishes fast — opt in with ``pytest -m slow``.
"""

from __future__ import annotations

import math

import pytest

from nexus.agent.builtin_embedder import BUILTIN_DIM, BuiltinEmbedder

pytestmark = pytest.mark.slow


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-8)


async def test_embed_returns_correct_dim() -> None:
    emb = BuiltinEmbedder()
    vecs = await emb.embed(["hello world"])
    assert len(vecs) == 1
    assert len(vecs[0]) == BUILTIN_DIM


async def test_embed_handles_batch() -> None:
    emb = BuiltinEmbedder()
    vecs = await emb.embed(["hello", "world", "tchau"])
    assert len(vecs) == 3
    assert all(len(v) == BUILTIN_DIM for v in vecs)


async def test_cross_lingual_similarity() -> None:
    """en/pt translations of the same concept should sit close in vector space.

    This is the whole point of the multilingual switch — without it, English
    prototypes can't classify Portuguese entity names.
    """
    emb = BuiltinEmbedder()
    pairs = [
        ("dog", "cachorro"),
        ("meeting", "reunião"),
        ("project", "projeto"),
    ]
    distractor = (await emb.embed(["pizza"]))[0]
    for en, pt in pairs:
        v_en, v_pt = await emb.embed([en, pt])
        sim_pair = _cos(v_en, v_pt)
        sim_distract = _cos(v_en, distractor)
        # Translation pair must be closer than an unrelated word.
        assert sim_pair > sim_distract, (
            f"expected {en!r}/{pt!r} closer than {en!r}/'pizza'; "
            f"pair={sim_pair:.3f}, distractor={sim_distract:.3f}"
        )
        # And reasonably high in absolute terms (loose lower bound).
        assert sim_pair > 0.55, f"weak translation similarity for {en}/{pt}: {sim_pair:.3f}"
