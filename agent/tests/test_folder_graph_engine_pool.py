"""Engine pool cache + ontology-drift invalidation."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.agent.folder_graph import _engine_pool


class _FakeEngine:
    def __init__(self, ontology: dict) -> None:
        self.relations = sorted(ontology.get("relations") or [])
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_build(monkeypatch: pytest.MonkeyPatch):
    """Stub `_build_engine` so we don't need a real loom engine."""
    built: list[_FakeEngine] = []

    def _fake_build(folder, ontology, cfg, graphrag_cfg):
        engine = _FakeEngine(ontology)
        built.append(engine)
        return engine, "stub-emb", "stub-ext"

    monkeypatch.setattr(_engine_pool, "_build_engine", _fake_build)
    return built


@pytest.fixture(autouse=True)
def clear_pool():
    _engine_pool._pool.clear()
    yield
    for entry in list(_engine_pool._pool.values()):
        try:
            entry["engine"].close()
        except Exception:
            pass
        try:
            entry["manifest"].close()
        except Exception:
            pass
    _engine_pool._pool.clear()


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(graphrag=SimpleNamespace(enabled=True))


def test_repeated_open_with_same_ontology_returns_cached_engine(
    tmp_path: Path, fake_build
) -> None:
    ontology = {
        "entity_types": ["a"],
        "relations": ["mentions"],
        "allow_custom_relations": True,
    }
    e1 = _engine_pool.open_folder_engine(tmp_path, ontology, _cfg())
    e2 = _engine_pool.open_folder_engine(tmp_path, ontology, _cfg())

    assert e1 is e2
    assert len(fake_build) == 1


def test_open_with_drifted_ontology_rebuilds_engine(
    tmp_path: Path, fake_build
) -> None:
    """The cache must detect ontology drift and rebuild — otherwise loom's
    extraction prompt keeps using the old relation taxonomy after an edit."""
    ontology_a = {
        "entity_types": ["a"],
        "relations": ["mentions"],
        "allow_custom_relations": True,
    }
    ontology_b = {
        "entity_types": ["a"],
        "relations": ["mentions", "cites"],
        "allow_custom_relations": True,
    }

    entry_a = _engine_pool.open_folder_engine(tmp_path, ontology_a, _cfg())
    engine_a = entry_a["engine"]

    entry_b = _engine_pool.open_folder_engine(tmp_path, ontology_b, _cfg())
    engine_b = entry_b["engine"]

    assert engine_a is not engine_b
    assert engine_a.closed is True
    assert engine_b.relations == ["cites", "mentions"]
    assert len(fake_build) == 2
    assert _engine_pool.get_pool_keys() == [str(tmp_path.resolve())]


def test_drift_check_is_order_invariant(tmp_path: Path, fake_build) -> None:
    """Ontology hash is order-invariant, so reordering relations is a no-op."""
    ontology_a = {
        "entity_types": ["a", "b"],
        "relations": ["mentions", "cites"],
        "allow_custom_relations": True,
    }
    ontology_b = {
        "entity_types": ["b", "a"],
        "relations": ["cites", "mentions"],
        "allow_custom_relations": True,
    }

    e1 = _engine_pool.open_folder_engine(tmp_path, ontology_a, _cfg())
    e2 = _engine_pool.open_folder_engine(tmp_path, ontology_b, _cfg())

    assert e1 is e2
    assert len(fake_build) == 1
