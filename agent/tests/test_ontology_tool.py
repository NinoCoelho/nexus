"""Tests for the ontology_manage tool handler."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from nexus.tools.ontology_tool import make_ontology_handler


def _fake_cfg() -> Any:
    cfg = MagicMock()
    cfg.graphrag.ontology.entity_types = ["person", "concept"]
    cfg.graphrag.ontology.core_relations = ["uses"]
    cfg.graphrag.ontology.allow_custom_relations = True
    return cfg


@pytest.fixture
def tmp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("nexus.vault._VAULT_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def reinit_spy(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    spy = AsyncMock()
    monkeypatch.setattr(
        "nexus.agent.graphrag_manager.initialize", spy,
    )
    return spy


async def test_view_seeds_lazily(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    handle = make_ontology_handler(
        ask_user=None, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    res = json.loads(await handle({"action": "view"}))
    assert res["ok"] is True
    assert "person" in [t["type"] for t in res["entity_types"]]
    assert "uses" in [r["relation"] for r in res["relations"]]
    assert "Ontology" in res["instructions"]


async def test_add_type_triggers_reinit(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    handle = make_ontology_handler(
        ask_user=None, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    # Seed first
    await handle({"action": "view"})
    reinit_spy.reset_mock()

    res = json.loads(await handle({
        "action": "add_type",
        "name": "meeting",
        "description": "syncs",
        "prototypes_en": "meeting sync",
        "prototypes_pt": "reunião",
    }))
    assert res["ok"] is True
    reinit_spy.assert_awaited_once()

    # Verify it actually persisted by re-viewing.
    after = json.loads(await handle({"action": "view"}))
    assert "meeting" in [t["type"] for t in after["entity_types"]]


async def test_add_type_invalid_name(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    handle = make_ontology_handler(
        ask_user=None, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    await handle({"action": "view"})
    res = json.loads(await handle({"action": "add_type", "name": "Bad-Name"}))
    assert res["ok"] is False
    assert "invalid" in res["error"]


async def test_propose_requires_ask_user(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    handle = make_ontology_handler(
        ask_user=None, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    await handle({"action": "view"})
    res = json.loads(await handle({
        "action": "propose_from_documents",
        "proposal": {
            "entity_types": [{"type": "meeting", "prototypes_en": "meeting"}],
            "relations": [],
            "rationale": "we have lots of standups",
        },
    }))
    assert res["ok"] is False
    assert "ask_user" in res["error"]


async def test_propose_applies_after_yes(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    ask_user = AsyncMock()
    ask_user.return_value = MagicMock(answer="yes", timed_out=False)

    handle = make_ontology_handler(
        ask_user=ask_user, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    await handle({"action": "view"})
    reinit_spy.reset_mock()

    res = json.loads(await handle({
        "action": "propose_from_documents",
        "proposal": {
            "entity_types": [
                {"type": "meeting", "description": "syncs", "prototypes_en": "meeting", "prototypes_pt": "reunião"},
                {"type": "person"},  # should be skipped (already exists)
            ],
            "relations": [
                {"relation": "approved_by", "prototypes_en": "approved by"},
            ],
            "rationale": "lots of meetings & approvals in vault",
        },
    }))
    assert res["ok"] is True
    assert res["applied_entity_types"] == ["meeting"]
    assert res["applied_relations"] == ["approved_by"]
    assert res["skipped_entity_types"] == ["person"]
    reinit_spy.assert_awaited_once()
    ask_user.assert_awaited_once()
    # Confirm prompt mentions the new type
    sent = ask_user.await_args[0][0]
    assert "meeting" in sent["prompt"]
    assert "approved_by" in sent["prompt"]


async def test_propose_skipped_when_user_declines(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    ask_user = AsyncMock()
    ask_user.return_value = MagicMock(answer="no", timed_out=False)

    handle = make_ontology_handler(
        ask_user=ask_user, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    await handle({"action": "view"})
    reinit_spy.reset_mock()

    res = json.loads(await handle({
        "action": "propose_from_documents",
        "proposal": {
            "entity_types": [{"type": "meeting", "prototypes_en": "meeting"}],
            "relations": [],
            "rationale": "x",
        },
    }))
    assert res["ok"] is False
    assert "declined" in res["error"]
    reinit_spy.assert_not_awaited()


async def test_propose_all_duplicates_short_circuits(
    tmp_vault: Path, reinit_spy: AsyncMock,
) -> None:
    ask_user = AsyncMock()
    handle = make_ontology_handler(
        ask_user=ask_user, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    await handle({"action": "view"})

    res = json.loads(await handle({
        "action": "propose_from_documents",
        "proposal": {
            "entity_types": [{"type": "person"}],  # already exists
            "relations": [{"relation": "uses"}],   # already exists
            "rationale": "redundant",
        },
    }))
    assert res["ok"] is False
    assert "already exist" in res["error"]
    ask_user.assert_not_awaited()


async def test_remove_relation(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    handle = make_ontology_handler(
        ask_user=None, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    await handle({"action": "view"})
    # Add a non-essential relation to remove.
    await handle({
        "action": "add_relation",
        "name": "approved_by",
        "prototypes_en": "approved by",
    })
    reinit_spy.reset_mock()

    res = json.loads(await handle({"action": "remove_relation", "name": "approved_by"}))
    assert res["ok"] is True
    reinit_spy.assert_awaited_once()


async def test_set_allow_custom_relations(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    handle = make_ontology_handler(
        ask_user=None, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    await handle({"action": "view"})
    reinit_spy.reset_mock()

    res = json.loads(await handle({
        "action": "set_allow_custom_relations",
        "allow_custom_relations": False,
    }))
    assert res["ok"] is True
    reinit_spy.assert_awaited_once()
    after = json.loads(await handle({"action": "view"}))
    assert after["allow_custom_relations"] is False


async def test_unknown_action(tmp_vault: Path, reinit_spy: AsyncMock) -> None:
    handle = make_ontology_handler(
        ask_user=None, cfg_loader=_fake_cfg, vault_root=tmp_vault,
    )
    res = json.loads(await handle({"action": "frobnicate"}))
    assert res["ok"] is False
    assert "frobnicate" in res["error"]
