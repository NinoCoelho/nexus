"""Tests for reading an MLX snapshot off disk — pure stdlib, any OS.

This is the code that makes MLX models need no capability probe: everything
(context window, vision-ness, tool support, size) is read from the snapshot
directory and written onto the `[[models]]` config entry, where both the
context-window and capability resolution chains already have first-priority
overrides.
"""

from __future__ import annotations

import json

import pytest

from nexus.local_llm.mlx_snapshot import is_mlx_snapshot, read_snapshot, slug_for


def make_snapshot(
    tmp_path,
    *,
    config: dict | None = None,
    tokenizer_config: dict | None = None,
    chat_template: str | None = None,
    weight_bytes: int = 1024,
):
    """Write a minimal but realistic snapshot directory."""
    (tmp_path / "model.safetensors").write_bytes(b"\0" * weight_bytes)
    (tmp_path / "config.json").write_text(json.dumps(config or {}), encoding="utf-8")
    if tokenizer_config is not None:
        (tmp_path / "tokenizer_config.json").write_text(
            json.dumps(tokenizer_config), encoding="utf-8"
        )
    if chat_template is not None:
        (tmp_path / "chat_template.jinja").write_text(chat_template, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# slug_for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo_id,expected",
    [
        ("mlx-community/Qwen3-30B-A3B-4bit", "qwen3-30b-a3b-4bit"),
        ("mlx-community/Mistral-7B-Instruct-v0.3-4bit", "mistral-7b-instruct-v0.3-4bit"),
        ("Qwen3-4bit", "qwen3-4bit"),
        ("owner/Weird__Name!!", "weird-name"),
        ("", "model"),
    ],
)
def test_slug_for(repo_id, expected):
    assert slug_for(repo_id) == expected


def test_slug_is_safe_as_a_config_key_and_dirname():
    slug = slug_for("mlx-community/Some Model (4bit)")
    assert " " not in slug and "(" not in slug and "/" not in slug


# ---------------------------------------------------------------------------
# is_mlx_snapshot
# ---------------------------------------------------------------------------


def test_is_mlx_snapshot_requires_config_and_weights(tmp_path):
    assert is_mlx_snapshot(tmp_path) is False
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    assert is_mlx_snapshot(tmp_path) is False
    (tmp_path / "model.safetensors").write_bytes(b"\0")
    assert is_mlx_snapshot(tmp_path) is True


def test_is_mlx_snapshot_false_for_missing_dir(tmp_path):
    assert is_mlx_snapshot(tmp_path / "nope") is False


# ---------------------------------------------------------------------------
# Context window
# ---------------------------------------------------------------------------


def test_context_window_from_max_position_embeddings(tmp_path):
    make_snapshot(tmp_path, config={"max_position_embeddings": 32768})
    assert read_snapshot(tmp_path).max_position_embeddings == 32768


def test_context_window_from_nested_text_config_for_vlm(tmp_path):
    """VLM configs nest the language model's window under `text_config`."""
    make_snapshot(
        tmp_path,
        config={"vision_config": {"image_size": 448}, "text_config": {"max_position_embeddings": 8192}},
    )
    info = read_snapshot(tmp_path)
    assert info.max_position_embeddings == 8192
    assert info.is_vlm is True


def test_context_window_applies_rope_scaling_factor(tmp_path):
    make_snapshot(
        tmp_path,
        config={"max_position_embeddings": 8192, "rope_scaling": {"factor": 4.0}},
    )
    assert read_snapshot(tmp_path).max_position_embeddings == 32768


def test_context_window_ignores_rope_factor_of_one(tmp_path):
    make_snapshot(
        tmp_path,
        config={"max_position_embeddings": 8192, "rope_scaling": {"rope_type": "default", "factor": 1.0}},
    )
    assert read_snapshot(tmp_path).max_position_embeddings == 8192


def test_context_window_zero_when_unknown(tmp_path):
    """0 means 'fall back to the shared resolution chain', not 'no limit'."""
    make_snapshot(tmp_path, config={})
    assert read_snapshot(tmp_path).max_position_embeddings == 0


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_tools_capability_from_template_variable(tmp_path):
    make_snapshot(tmp_path, chat_template="{% if tools %}...{% endif %}")
    info = read_snapshot(tmp_path)
    assert info.supports_tools is True
    assert "tools" in info.capabilities


def test_tools_capability_from_sentinel_without_variable(tmp_path):
    make_snapshot(tmp_path, chat_template="render <tool_call>x</tool_call>")
    assert read_snapshot(tmp_path).supports_tools is True


def test_no_tools_capability_for_plain_template(tmp_path):
    make_snapshot(tmp_path, chat_template="{{ messages }}")
    info = read_snapshot(tmp_path)
    assert info.supports_tools is False
    assert "tools" not in info.capabilities
    assert info.capabilities == ["chat"]


def test_vision_capability_from_vision_config(tmp_path):
    make_snapshot(tmp_path, config={"vision_config": {}})
    assert "vision" in read_snapshot(tmp_path).capabilities


def test_vision_capability_from_architecture(tmp_path):
    make_snapshot(tmp_path, config={"architectures": ["Qwen2VLForConditionalGeneration"]})
    info = read_snapshot(tmp_path)
    assert info.is_vlm is True
    assert "vision" in info.capabilities


def test_reasoning_capability_from_think_sentinel(tmp_path):
    make_snapshot(tmp_path, chat_template="...<think>...")
    assert "reasoning" in read_snapshot(tmp_path).capabilities


def test_chat_capability_always_present(tmp_path):
    make_snapshot(tmp_path, config={})
    assert read_snapshot(tmp_path).capabilities[0] == "chat"


# ---------------------------------------------------------------------------
# Chat template discovery
# ---------------------------------------------------------------------------


def test_chat_template_from_jinja_file_preferred(tmp_path):
    make_snapshot(
        tmp_path,
        chat_template="FROM JINJA {% if tools %}{% endif %}",
        tokenizer_config={"chat_template": "FROM TOKENIZER CONFIG"},
    )
    assert "FROM JINJA" in read_snapshot(tmp_path).chat_template


def test_chat_template_from_tokenizer_config_fallback(tmp_path):
    make_snapshot(tmp_path, tokenizer_config={"chat_template": "INLINE TEMPLATE"})
    assert read_snapshot(tmp_path).chat_template == "INLINE TEMPLATE"


def test_chat_template_list_form_is_flattened(tmp_path):
    """Some repos ship a list of named templates."""
    make_snapshot(
        tmp_path,
        tokenizer_config={
            "chat_template": [
                {"name": "default", "template": "MAIN {% if tools %}{% endif %}"},
                {"name": "rag", "template": "OTHER"},
            ]
        },
    )
    info = read_snapshot(tmp_path)
    assert "MAIN" in info.chat_template
    assert info.supports_tools is True


# ---------------------------------------------------------------------------
# Quantization, size, robustness
# ---------------------------------------------------------------------------


def test_quantization_bits_from_quantization_block(tmp_path):
    make_snapshot(tmp_path, config={"quantization": {"group_size": 64, "bits": 4}})
    assert read_snapshot(tmp_path).quantization_bits == 4


def test_total_bytes_sums_all_shards(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a.safetensors").write_bytes(b"\0" * 1000)
    (tmp_path / "b.safetensors").write_bytes(b"\0" * 2000)
    assert read_snapshot(tmp_path).total_bytes == 3000


def test_malformed_config_does_not_raise(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"\0")
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    info = read_snapshot(tmp_path)
    assert info.max_position_embeddings == 0
    assert info.capabilities == ["chat"]


def test_missing_directory_does_not_raise(tmp_path):
    info = read_snapshot(tmp_path / "absent")
    assert info.total_bytes == 0
    assert info.capabilities == ["chat"]


def test_repo_id_defaults_to_directory_name(tmp_path):
    d = tmp_path / "Qwen3-4bit"
    d.mkdir()
    make_snapshot(d, config={})
    assert read_snapshot(d).repo_id == "mlx-community/Qwen3-4bit"


def test_repo_id_override_is_recorded(tmp_path):
    make_snapshot(tmp_path, config={})
    info = read_snapshot(tmp_path, repo_id="someone/Custom-MLX")
    assert info.repo_id == "someone/Custom-MLX"
    assert info.slug == "custom-mlx"


def test_to_public_omits_chat_template(tmp_path):
    """The template can be tens of KB and no client needs it."""
    make_snapshot(tmp_path, chat_template="x" * 5000)
    public = read_snapshot(tmp_path).to_public()
    assert "chat_template" not in public
    assert public["context_window"] == 0
    assert "slug" in public and "total_gb" in public
