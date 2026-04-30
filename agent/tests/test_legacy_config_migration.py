"""Migration of legacy ProviderConfig entries on load.

Older ``config.toml`` files don't carry the wizard-era fields
(``runtime_kind``, ``auth_kind``, ``catalog_id``). The parser fills
them in lazily so the rest of the codebase can rely on the new
shape without a forced file rewrite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus import config_file
from nexus.config_schema import ProviderConfig


def _write_legacy(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


def _load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str):
    p = _write_legacy(tmp_path, body)
    monkeypatch.setattr(config_file, "CONFIG_PATH", p)
    return config_file.load()


def test_legacy_openai_gets_runtime_kind_and_auth_kind_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _load(
        tmp_path,
        monkeypatch,
        body="""\
[providers.openai]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
type = "openai_compat"
""",
    )
    p = cfg.providers["openai"]
    assert p.runtime_kind == "openai_compat"
    assert p.auth_kind == "api"
    assert p.catalog_id == "openai"


def test_legacy_anthropic_keeps_anthropic_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _load(
        tmp_path,
        monkeypatch,
        body="""\
[providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
type = "anthropic"
""",
    )
    p = cfg.providers["anthropic"]
    assert p.runtime_kind == "anthropic"
    assert p.auth_kind == "api"
    assert p.catalog_id == "anthropic"


def test_legacy_ollama_migrates_to_anonymous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _load(
        tmp_path,
        monkeypatch,
        body="""\
[providers.ollama]
base_url = "http://localhost:11434"
type = "ollama"
""",
    )
    p = cfg.providers["ollama"]
    assert p.runtime_kind == "ollama"
    assert p.auth_kind == "anonymous"
    assert p.catalog_id == "ollama"


def test_custom_named_provider_does_not_adopt_catalog_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user-named provider that isn't a catalog id stays catalog_id=None."""
    cfg = _load(
        tmp_path,
        monkeypatch,
        body="""\
[providers.my_internal_proxy]
base_url = "https://internal.example.com/v1"
api_key_env = "INTERNAL_KEY"
type = "openai_compat"
""",
    )
    p = cfg.providers["my_internal_proxy"]
    assert p.catalog_id is None
    assert p.runtime_kind == "openai_compat"
    assert p.auth_kind == "api"


def test_save_round_trip_preserves_migrated_anonymous_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once a legacy ollama config is migrated and re-saved, the
    ``auth_kind = "anonymous"`` line must round-trip back on the next load."""
    cfg = _load(
        tmp_path,
        monkeypatch,
        body="""\
[providers.ollama]
base_url = "http://localhost:11434"
type = "ollama"
""",
    )
    config_file.save(cfg)
    cfg2 = config_file.load()
    assert cfg2.providers["ollama"].auth_kind == "anonymous"


def test_save_omits_default_wizard_fields_for_pristine_api_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vanilla openai_compat provider that hasn't touched OAuth/IAM
    must not sprout iam_extra={} / oauth_token_ref=null lines on save."""
    cfg = _load(
        tmp_path,
        monkeypatch,
        body="""\
[providers.openai]
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
type = "openai_compat"
""",
    )
    config_file.save(cfg)
    raw = (tmp_path / "config.toml").read_text()
    assert "iam_extra" not in raw
    assert "oauth_token_ref" not in raw
    assert "iam_profile" not in raw
    assert "auth_kind" not in raw      # default "api" not emitted


def test_explicit_wizard_fields_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bedrock-style entry with iam_kind set survives save+load."""
    cfg = _load(
        tmp_path,
        monkeypatch,
        body="""\
[providers.openai]
api_key_env = "OPENAI_API_KEY"
type = "openai_compat"
""",
    )
    cfg.providers["bedrock"] = ProviderConfig(
        type="openai_compat",
        runtime_kind="bedrock",
        auth_kind="iam",
        iam_profile="default",
        iam_region="us-east-1",
        iam_extra={"role_arn": "arn:aws:iam::123:role/x"},
        catalog_id="bedrock",
    )
    config_file.save(cfg)
    cfg2 = config_file.load()
    p = cfg2.providers["bedrock"]
    assert p.runtime_kind == "bedrock"
    assert p.auth_kind == "iam"
    assert p.iam_profile == "default"
    assert p.iam_region == "us-east-1"
    assert p.iam_extra == {"role_arn": "arn:aws:iam::123:role/x"}
    assert p.catalog_id == "bedrock"
