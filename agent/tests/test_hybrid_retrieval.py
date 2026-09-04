"""Tests for hybrid retrieval merge (vector + FTS reciprocal-rank fusion)."""

from __future__ import annotations

from nexus.server.routes.graph import _hybrid_merge


def _vec(path: str, score: float = 0.9) -> dict:
    return {
        "chunk_id": f"c:{path}",
        "source_path": path,
        "heading": "",
        "content": "...",
        "score": score,
        "source": "vector",
        "related_entities": [],
    }


def test_fts_only_files_appended() -> None:
    vec = [_vec("a.md"), _vec("b.md")]
    fts = [{"path": "z.md", "snippet": "exact <mark>match</mark>", "score": -1}]
    merged, _ = _hybrid_merge(vec, fts, 10)
    paths = [r["source_path"] for r in merged]
    assert "z.md" in paths
    z = next(r for r in merged if r["source_path"] == "z.md")
    assert z["source"] == "fts"
    assert z["chunk_id"] == "fts:z.md"


def test_file_ranked_high_in_both_surfaces_first() -> None:
    # a.md is rank 2 in vector but rank 1 in FTS; b.md is rank 1 in vector only.
    vec = [_vec("b.md"), _vec("a.md")]
    fts = [{"path": "a.md", "snippet": "", "score": -1}]
    merged, _ = _hybrid_merge(vec, fts, 10)
    assert merged[0]["source_path"] == "a.md"


def test_limit_respected() -> None:
    vec = [_vec(f"v{i}.md") for i in range(10)]
    fts = [{"path": f"f{i}.md", "snippet": "", "score": -1} for i in range(10)]
    merged, _ = _hybrid_merge(vec, fts, 5)
    assert len(merged) == 5


def test_empty_inputs() -> None:
    merged, order = _hybrid_merge([], [], 10)
    assert merged == []
    assert order == []


def test_vector_chunk_order_kept_within_file() -> None:
    vec = [_vec("a.md", 0.9), _vec("a.md", 0.5), _vec("b.md", 0.7)]
    merged, _ = _hybrid_merge(vec, [], 10)
    a_chunks = [r for r in merged if r["source_path"] == "a.md"]
    assert [c["score"] for c in a_chunks] == [0.9, 0.5]
