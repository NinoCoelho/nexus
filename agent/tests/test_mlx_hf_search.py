"""MLX repo search: the third pass + pasted-repo short-circuit.

Found when the user's shared link
(``nvythong/Qwen3.8-35B-A3B-Distill-mlx-4Bit``) was invisible to search:
HF's ``library=mlx`` filter keys off ``library_name`` from the model
card, which community conversions routinely leave as ``transformers``
(the ``mlx`` tag alone doesn't count). Passes 1 (author) and 2 (library)
both missed it; pass 3 is a plain search filtered client-side by
``_looks_mlx``. A pasted ``owner/name`` or HF URL short-circuits to
exactly that repo.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus.local_llm import mlx_hf_search
from nexus.local_llm.mlx_hf_search import (
    _looks_like_repo_id,
    _looks_mlx,
    search_mlx_repos,
)

# ---------------------------------------------------------------------------
# _looks_like_repo_id
# ---------------------------------------------------------------------------


def test_repo_id_from_bare_owner_name():
    assert _looks_like_repo_id("nvythong/Qwen3.8-4Bit") == "nvythong/Qwen3.8-4Bit"


def test_repo_id_from_url_variants():
    base = "nvythong/Qwen3.8-35B-A3B-Distill-mlx-4Bit"
    for url in (
        f"https://huggingface.co/{base}",
        f"https://huggingface.co/{base}?utm_source=chatgpt.com",
        f"https://www.huggingface.co/{base}",
        f"huggingface.co/{base}",
        f"https://huggingface.co/{base}/tree/main",
    ):
        assert _looks_like_repo_id(url) == base, url


def test_non_repo_queries_return_none():
    assert _looks_like_repo_id("qwen 35b distill") is None  # has a space
    assert _looks_like_repo_id("qwen") is None  # no slash
    assert _looks_like_repo_id("a/b/c") is None  # too many parts
    assert _looks_like_repo_id("https://github.com/a/b") is None  # foreign host
    assert _looks_like_repo_id("") is None


# ---------------------------------------------------------------------------
# _looks_mlx
# ---------------------------------------------------------------------------


def test_mlxness_by_name_and_tags():
    assert _looks_mlx("nvythong/Qwen3.8-35B-A3B-Distill-mlx-4Bit", ["safetensors"]) is True
    assert _looks_mlx("some/Model", ["mlx", "safetensors"]) is True
    assert _looks_mlx("some/Model", ["mlx-my-repo"]) is True
    assert _looks_mlx("empero-ai/Qwen3.8-Distill-GGUF", ["gguf"]) is False
    assert _looks_mlx("some/Model", []) is False


# ---------------------------------------------------------------------------
# search_mlx_repos with a fake API
# ---------------------------------------------------------------------------


class FakeApi:
    """Stand-in for HfApi: canned list_models + model_info."""

    def __init__(self, per_query, model_info=None, fail_list=False):
        self.per_query = per_query
        self.model_info = model_info
        self.fail_list = fail_list

    def list_models(self, **kwargs):
        if self.fail_list:
            raise TypeError("bad kwarg")
        return self.per_query.get(
            (kwargs.get("search", ""), kwargs.get("author"), kwargs.get("library")),
            [],
        )

    def model_info(self, repo_id):
        if self.model_info is None:
            raise RuntimeError("no such repo")
        return self.model_info


def _model(repo_id, downloads, tags):
    return SimpleNamespace(id=repo_id, downloads=downloads, likes=0, tags=tags)


@pytest.fixture
def patch_api(monkeypatch):
    def _patch(fake: FakeApi):
        monkeypatch.setattr(mlx_hf_search, "_api", lambda: fake)

    return _patch


def test_third_pass_finds_tagless_library_conversions(patch_api, monkeypatch):
    """The nvythong case: author≠mlx-community, library_name=transformers."""
    target = "nvythong/Qwen3.8-35B-A3B-Distill-mlx-4Bit"
    junk = _model("empero-ai/Qwen3.8-Distill-GGUF", 9999, ["gguf"])

    def fake_list(api, **kwargs):
        if kwargs.get("library") == "mlx":
            return [junk]  # pass 2 (library=mlx) returns junk, as HF really does
        if kwargs.get("author") is None and kwargs.get("library") is None:
            return [_model(target, 689, ["safetensors"])]
        return []

    monkeypatch.setattr(mlx_hf_search, "_list_models", fake_list)
    patch_api(FakeApi({}))

    results = search_mlx_repos("Qwen3.8-35B-A3B-Distill")
    ids = [r["id"] for r in results]
    assert target in ids
    assert "empero-ai/Qwen3.8-Distill-GGUF" not in ids  # junk filtered


def test_pasted_repo_short_circuits_search(patch_api):
    repo = "nvythong/Qwen3.8-35B-A3B-Distill-mlx-4Bit"
    info = SimpleNamespace(id=repo, downloads=689, likes=0, tags=["mlx"])
    fake = FakeApi({}, model_info=info)
    called = {"list": False}

    def spy_list(api, **kwargs):
        called["list"] = True
        return []

    import nexus.local_llm.mlx_hf_search as mod

    monkeypatch_setattr = None
    orig_list = mod._list_models
    url = "https://huggingface.co/nvythong/Qwen3.8-35B-A3B-Distill-mlx-4Bit?utm_source=chatgpt.com"
    mod._list_models = spy_list
    try:
        patch_api(fake)
        results = search_mlx_repos(url)
    finally:
        mod._list_models = orig_list
    assert called["list"] is False  # search passes never ran
    assert len(results) == 1
    assert results[0]["id"] == "nvythong/Qwen3.8-35B-A3B-Distill-mlx-4Bit"


def test_pasted_unknown_repo_still_listed(patch_api):
    patch_api(FakeApi({}, model_info=None))  # model_info raises
    results = search_mlx_repos("nvythong/DoesNotExist-mlx-4Bit")
    assert len(results) == 1
    assert results[0]["downloads"] == 0


def test_dedup_across_passes(patch_api, monkeypatch):
    """Same repo from two passes appears once; sort is by downloads."""
    shared = _model("mlx-community/Model-4bit", 500, ["mlx"])
    other = _model("mlx-community/Other-4bit", 900, ["mlx"])
    monkeypatch.setattr(
        mlx_hf_search,
        "_list_models",
        lambda api, **kwargs: ([shared] if kwargs.get("author") else [shared, other]),
    )
    patch_api(FakeApi({}))
    results = search_mlx_repos("model")
    assert [r["id"] for r in results] == ["mlx-community/Other-4bit", "mlx-community/Model-4bit"]
