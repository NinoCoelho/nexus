"""MLX config registration, and the `local-*` cleanup landmine.

The landmine: ``local_llm.manager.cleanup_stale_config()`` runs at startup and
deletes **every** provider named ``local`` or prefixed ``local-`` that is not in
its ``_servers`` process table, prunes the matching ``[[models]]`` rows, and
clears a ``local/``/``local-``-prefixed ``default_model``. An in-process MLX
provider is never in that table, so naming it ``local-<slug>`` would make the
user's model silently disappear from config on every single boot.

These tests pin the `mlx-` prefix and prove coexistence with llama.cpp entries.

Note on isolation: ``manager._load_config`` builds its path inline from
``Path.home()`` rather than from a patchable module constant, so these tests
replace ``_load_config`` / ``_save_config`` with an in-memory pair. Never let a
test touch the real ``~/.nexus/config.toml``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus.local_llm import mlx_manager
from nexus.local_llm.mlx_snapshot import MlxModelInfo


@pytest.fixture
def fake_config(monkeypatch, tmp_path):
    """In-memory config.toml shared by mlx_manager and manager."""
    state: dict = {"raw": {}}

    def _load():
        return tmp_path / "config.toml", state["raw"]

    def _save(_path, raw):
        state["raw"] = raw

    monkeypatch.setattr(mlx_manager, "_load_config", _load)
    monkeypatch.setattr(mlx_manager, "_save_config", _save)
    return state


def make_info(tmp_path, *, slug="qwen3-4bit", supports_tools=True, window=32768, caps=None):
    directory = tmp_path / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text("{}", encoding="utf-8")
    (directory / "model.safetensors").write_bytes(b"\0")
    return MlxModelInfo(
        repo_id=f"mlx-community/{slug}",
        local_dir=directory,
        architectures=["Qwen3ForCausalLM"],
        model_type="qwen3",
        max_position_embeddings=window,
        quantization_bits=4,
        supports_tools=supports_tools,
        capabilities=(
            caps if caps is not None
            else (["chat", "tools"] if supports_tools else ["chat"])
        ),
    )


# ---------------------------------------------------------------------------
# The landmine
# ---------------------------------------------------------------------------


def test_provider_name_is_never_local_prefixed(tmp_path):
    """The whole point. See the module docstring."""
    name = mlx_manager.provider_name("qwen3-4bit")
    assert name == "mlx-qwen3-4bit"
    assert not name.startswith("local")
    assert not mlx_manager.model_id_for("qwen3-4bit").startswith(("local/", "local-"))


def test_mlx_entries_survive_llama_cpp_cleanup(fake_config, tmp_path, monkeypatch):
    """Register an MLX model, then run the llama.cpp sweep over the same config."""
    from nexus.local_llm import manager

    info = make_info(tmp_path)
    model_id = mlx_manager.add_to_config(info)

    monkeypatch.setattr(
        manager, "_load_config", lambda: (tmp_path / "config.toml", fake_config["raw"])
    )
    monkeypatch.setattr(
        manager, "_save_config", lambda _p, raw: fake_config.__setitem__("raw", raw)
    )
    # No llama-server processes running — the worst case for the sweep.
    monkeypatch.setattr(manager, "_servers", {})

    manager.cleanup_stale_config()

    raw = fake_config["raw"]
    assert "mlx-qwen3-4bit" in raw["providers"], "MLX provider was deleted by the llama.cpp sweep"
    assert any(m["id"] == model_id for m in raw["models"]), "MLX model row was pruned"
    assert raw["agent"]["default_model"] == model_id, "default_model was cleared"


def test_llama_cpp_cleanup_still_removes_its_own_stale_entries(fake_config, tmp_path, monkeypatch):
    """Guard against 'fixing' the sweep by making it a no-op."""
    from nexus.local_llm import manager

    fake_config["raw"] = {
        "providers": {
            "local-ghost": {"base_url": "http://127.0.0.1:9999", "type": "ollama"},
            "mlx-keeper": {"runtime_kind": "mlx", "model_path": str(tmp_path)},
        },
        "models": [
            {"id": "local-ghost/ghost", "provider": "local-ghost"},
            {"id": "mlx-keeper/keeper", "provider": "mlx-keeper"},
        ],
        "agent": {},
    }
    monkeypatch.setattr(
        manager, "_load_config", lambda: (tmp_path / "config.toml", fake_config["raw"])
    )
    monkeypatch.setattr(
        manager, "_save_config", lambda _p, raw: fake_config.__setitem__("raw", raw)
    )
    monkeypatch.setattr(manager, "_servers", {})

    manager.cleanup_stale_config()

    providers = fake_config["raw"]["providers"]
    assert "local-ghost" not in providers
    assert "mlx-keeper" in providers


# ---------------------------------------------------------------------------
# add_to_config
# ---------------------------------------------------------------------------


def test_add_writes_model_path_and_no_base_url(fake_config, tmp_path):
    info = make_info(tmp_path)
    mlx_manager.add_to_config(info)
    provider = fake_config["raw"]["providers"]["mlx-qwen3-4bit"]
    assert provider["runtime_kind"] == "mlx"
    assert provider["model_path"] == str(info.local_dir)
    assert "base_url" not in provider, "an in-process provider has no endpoint"


def test_add_writes_context_window_onto_the_model_entry(fake_config, tmp_path):
    """This is what bypasses the KNOWN_WINDOWS substring trap."""
    info = make_info(tmp_path, window=262144)
    mlx_manager.add_to_config(info)
    entry = fake_config["raw"]["models"][0]
    assert entry["context_window"] == 262144


def test_add_writes_capability_tags(fake_config, tmp_path):
    info = make_info(tmp_path, caps=["chat", "tools", "vision", "reasoning"])
    mlx_manager.add_to_config(info)
    tags = fake_config["raw"]["models"][0]["tags"]
    assert "mlx" in tags and "vision" in tags and "reasoning" in tags
    assert "chat" not in tags, "'chat' is implied; only distinguishing caps become tags"


def test_add_is_idempotent(fake_config, tmp_path):
    info = make_info(tmp_path)
    mlx_manager.add_to_config(info)
    mlx_manager.add_to_config(info)
    assert len(fake_config["raw"]["models"]) == 1


def test_add_preserves_user_edited_context_window(fake_config, tmp_path):
    info = make_info(tmp_path, window=32768)
    mlx_manager.add_to_config(info)
    fake_config["raw"]["models"][0]["context_window"] = 8192
    mlx_manager.add_to_config(info)
    assert fake_config["raw"]["models"][0]["context_window"] == 8192


def test_add_preserves_unrelated_providers_and_models(fake_config, tmp_path):
    fake_config["raw"] = {
        "providers": {"openai": {"api_key_env": "OPENAI_API_KEY"}},
        "models": [{"id": "openai/gpt-4o", "provider": "openai"}],
        "agent": {"default_model": "openai/gpt-4o"},
    }
    mlx_manager.add_to_config(make_info(tmp_path))
    raw = fake_config["raw"]
    assert "openai" in raw["providers"]
    assert any(m["id"] == "openai/gpt-4o" for m in raw["models"])
    assert raw["agent"]["default_model"] == "openai/gpt-4o", "an existing default must be kept"


def test_toolless_model_never_becomes_default(fake_config, tmp_path):
    """The agent loop is tool-driven; a tool-less chat model is not an agent."""
    mlx_manager.add_to_config(make_info(tmp_path, slug="vlm-4bit", supports_tools=False))
    assert fake_config["raw"]["agent"].get("default_model", "") == ""


def test_set_default_true_overrides(fake_config, tmp_path):
    info = make_info(tmp_path)
    model_id = mlx_manager.add_to_config(info, set_default=True)
    assert fake_config["raw"]["agent"]["default_model"] == model_id


def test_set_default_false_never_sets(fake_config, tmp_path):
    mlx_manager.add_to_config(make_info(tmp_path), set_default=False)
    assert fake_config["raw"]["agent"].get("default_model", "") == ""


# ---------------------------------------------------------------------------
# remove / cleanup
# ---------------------------------------------------------------------------


def test_remove_clears_provider_model_and_role_references(fake_config, tmp_path):
    info = make_info(tmp_path)
    model_id = mlx_manager.add_to_config(info)
    fake_config["raw"]["agent"]["vision_model"] = model_id
    fake_config["raw"]["agent"]["last_used_model"] = model_id

    mlx_manager.remove_from_config(info.slug)

    raw = fake_config["raw"]
    assert "mlx-qwen3-4bit" not in raw["providers"]
    assert raw["models"] == []
    agent_cfg = raw["agent"]
    assert agent_cfg["default_model"] == ""
    assert agent_cfg["vision_model"] == ""
    assert agent_cfg["last_used_model"] == ""


def test_cleanup_drops_entries_whose_snapshot_vanished(fake_config, tmp_path):
    info = make_info(tmp_path)
    mlx_manager.add_to_config(info)

    import shutil

    shutil.rmtree(info.local_dir)
    mlx_manager.cleanup_stale_config()

    raw = fake_config["raw"]
    assert raw["providers"] == {}
    assert raw["models"] == []
    assert raw["agent"]["default_model"] == ""


def test_cleanup_keeps_entries_whose_snapshot_exists(fake_config, tmp_path):
    info = make_info(tmp_path)
    model_id = mlx_manager.add_to_config(info)
    mlx_manager.cleanup_stale_config()
    assert any(m["id"] == model_id for m in fake_config["raw"]["models"])


def test_cleanup_ignores_non_mlx_providers(fake_config, tmp_path):
    fake_config["raw"] = {
        "providers": {"anthropic": {"api_key_env": "ANTHROPIC_API_KEY"}},
        "models": [{"id": "anthropic/claude", "provider": "anthropic"}],
        "agent": {"default_model": "anthropic/claude"},
    }
    mlx_manager.cleanup_stale_config()
    assert "anthropic" in fake_config["raw"]["providers"]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_list_installed_reads_snapshots(monkeypatch, tmp_path):
    root = tmp_path / "mlx"
    for slug in ("a-4bit", "b-4bit"):
        d = root / slug
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "model.safetensors").write_bytes(b"\0")
    (root / "not-a-model").mkdir()

    monkeypatch.setattr(mlx_manager, "MLX_MODELS_DIR", root)
    slugs = {i.slug for i in mlx_manager.list_installed()}
    assert slugs == {"a-4bit", "b-4bit"}


def test_list_installed_empty_when_root_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(mlx_manager, "MLX_MODELS_DIR", tmp_path / "absent")
    assert mlx_manager.list_installed() == []


def test_find_installed_returns_none_for_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(mlx_manager, "MLX_MODELS_DIR", tmp_path)
    assert mlx_manager.find_installed("nope") is None


def test_delete_installed_removes_dir_and_config(fake_config, monkeypatch, tmp_path):
    root = tmp_path / "mlx"
    info = make_info(root)
    monkeypatch.setattr(mlx_manager, "MLX_MODELS_DIR", root)
    mlx_manager.add_to_config(info)

    assert mlx_manager.delete_installed(info.slug) is True
    assert not Path(info.local_dir).exists()
    assert fake_config["raw"]["providers"] == {}


def test_delete_installed_false_when_absent(fake_config, monkeypatch, tmp_path):
    monkeypatch.setattr(mlx_manager, "MLX_MODELS_DIR", tmp_path)
    assert mlx_manager.delete_installed("ghost") is False
