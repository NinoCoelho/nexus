"""Tests for local_llm.hf_search using mocked HuggingFace Hub API calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nexus.local_llm.hf_search import (
    HfSearchError,
    _extract_quant_label,
    list_repo_ggufs,
    repo_card,
    search_gguf_repos,
)


# ---------------------------------------------------------------------------
# Quant label extraction (pure, no network)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("model-Q4_K_M.gguf", "Q4_K_M"),
    ("model-Q8_0.gguf", "Q8_0"),
    ("model.gguf", "Unknown"),
    ("llama-3-Q5_K_S.gguf", "Q5_K_S"),
    ("mistral-7b-instruct-Q4_0.gguf", "Q4_0"),
    ("no-quant-label.gguf", "Unknown"),
    ("Q2_K.gguf", "Q2_K"),
])
def test_extract_quant_label(filename: str, expected: str):
    assert _extract_quant_label(filename) == expected


# ---------------------------------------------------------------------------
# search_gguf_repos
# ---------------------------------------------------------------------------

def _make_model(model_id: str, downloads: int = 100, likes: int = 5, tags=None):
    m = MagicMock()
    m.id = model_id
    m.downloads = downloads
    m.likes = likes
    m.tags = tags or ["gguf"]
    return m


def test_search_gguf_repos_returns_list():
    fake_models = [
        _make_model("owner/model-A", downloads=500, likes=10, tags=["gguf", "text-generation"]),
        _make_model("owner/model-B", downloads=200, likes=3),
    ]
    mock_api = MagicMock()
    mock_api.list_models.return_value = iter(fake_models)

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        results = search_gguf_repos("qwen", limit=10)

    assert len(results) == 2
    assert results[0]["id"] == "owner/model-A"
    assert results[0]["downloads"] == 500
    assert results[0]["likes"] == 10
    assert "gguf" in results[0]["tags"]


def test_search_gguf_repos_passes_correct_args():
    mock_api = MagicMock()
    mock_api.list_models.return_value = iter([])

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        search_gguf_repos("llama", limit=5)

    # huggingface_hub 1.x dropped the ``direction`` kwarg; production code
    # tries the modern signature first and only falls back if TypeError.
    mock_api.list_models.assert_called_once_with(
        search="llama",
        filter="gguf",
        limit=5,
        sort="downloads",
    )


def test_search_gguf_repos_raises_hf_search_error_on_exception():
    mock_api = MagicMock()
    mock_api.list_models.side_effect = ConnectionError("network failure")

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        with pytest.raises(HfSearchError):
            search_gguf_repos("test")


# ---------------------------------------------------------------------------
# list_repo_ggufs
# ---------------------------------------------------------------------------

def _make_path_info(path: str, size: int):
    info = MagicMock()
    info.path = path
    info.size = size
    return info


def test_list_repo_ggufs_filters_gguf_only():
    all_files = [
        "README.md",
        "model-Q4_K_M.gguf",
        "model-Q8_0.gguf",
        "config.json",
    ]
    path_infos = [
        _make_path_info("model-Q4_K_M.gguf", 4_000_000_000),
        _make_path_info("model-Q8_0.gguf", 8_000_000_000),
    ]
    mock_api = MagicMock()
    mock_api.list_repo_files.return_value = iter(all_files)
    mock_api.get_paths_info.return_value = iter(path_infos)

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        results = list_repo_ggufs("owner/model")

    assert len(results) == 2
    filenames = [r["filename"] for r in results]
    assert "model-Q4_K_M.gguf" in filenames
    assert "model-Q8_0.gguf" in filenames
    assert "README.md" not in filenames


def test_list_repo_ggufs_quant_labels():
    files = ["qwen-Q4_K_M.gguf", "qwen-Q8_0.gguf", "qwen.gguf"]
    path_infos = [
        _make_path_info("qwen-Q4_K_M.gguf", 1000),
        _make_path_info("qwen-Q8_0.gguf", 2000),
        _make_path_info("qwen.gguf", 3000),
    ]
    mock_api = MagicMock()
    mock_api.list_repo_files.return_value = iter(files)
    mock_api.get_paths_info.return_value = iter(path_infos)

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        results = list_repo_ggufs("owner/qwen")

    label_map = {r["filename"]: r["quant_label"] for r in results}
    assert label_map["qwen-Q4_K_M.gguf"] == "Q4_K_M"
    assert label_map["qwen-Q8_0.gguf"] == "Q8_0"
    assert label_map["qwen.gguf"] == "Unknown"


def test_list_repo_ggufs_empty_repo():
    mock_api = MagicMock()
    mock_api.list_repo_files.return_value = iter(["README.md", "config.json"])
    mock_api.get_paths_info.return_value = iter([])

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        results = list_repo_ggufs("owner/no-gguf-repo")

    assert results == []


def test_list_repo_ggufs_raises_on_failure():
    mock_api = MagicMock()
    mock_api.list_repo_files.side_effect = RuntimeError("API down")

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        with pytest.raises(HfSearchError):
            list_repo_ggufs("owner/model")


# ---------------------------------------------------------------------------
# repo_card
# ---------------------------------------------------------------------------

def test_repo_card_returns_expected_fields():
    mock_info = MagicMock()
    mock_info.id = "owner/model"
    mock_info.downloads = 1234
    mock_info.likes = 56
    mock_info.tags = ["gguf", "chat"]
    mock_info.cardData = {"description": "A great model."}

    mock_api = MagicMock()
    mock_api.model_info.return_value = mock_info

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        result = repo_card("owner/model")

    assert result["id"] == "owner/model"
    assert result["downloads"] == 1234
    assert result["likes"] == 56
    assert "gguf" in result["tags"]
    assert result["description"] == "A great model."


def test_repo_card_no_card_data():
    mock_info = MagicMock()
    mock_info.id = "owner/minimal"
    mock_info.downloads = 0
    mock_info.likes = 0
    mock_info.tags = []
    mock_info.cardData = None

    mock_api = MagicMock()
    mock_api.model_info.return_value = mock_info

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        result = repo_card("owner/minimal")

    assert result["description"] == ""


def test_repo_card_raises_on_failure():
    mock_api = MagicMock()
    mock_api.model_info.side_effect = Exception("not found")

    with patch("huggingface_hub.HfApi", return_value=mock_api):
        with pytest.raises(HfSearchError):
            repo_card("owner/missing")
