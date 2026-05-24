"""Routes for provider management: /providers, /providers/{name}/models, /providers/{name}/key."""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ...i18n import t
from ..deps import get_agent, get_app_state, get_locale
from .config import _rebuild_registry

router = APIRouter()

# Provider names: lowercase + digits + hyphen/underscore. Hyphens for
# catalog ids ("google-gemini"), underscores for legacy/custom names.
_PROVIDER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
# Credential names: UPPER_SNAKE_CASE, same constraint as the existing
# /credentials route.
_CREDENTIAL_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Auth methods that PR 2 supports. ``oauth_*`` arrives in PR 4, ``iam_*``
# in PR 5; until then the wizard endpoint refuses them with 422 so the UI
# can show a clear "not yet supported" message.
_SUPPORTED_AUTH_METHODS_PR2 = {"api", "anonymous"}

# Nexus subscription sign-in: the wizard runs the popup + idToken flow
# out-of-band via ``/auth/nexus/verify`` (which writes the apiKey into
# ``secrets.toml`` under ``nexus_api_key``). The wizard apply just binds
# a provider entry with ``runtime_kind="nexus"`` to that already-stored
# credential — no credentials field on the request body.
_NEXUS_AUTH_METHODS = {"nexus_signin"}

# OAuth-bundle methods: the upstream produces a refresh+access pair we
# store via ``secrets.set_oauth`` and reference through ``oauth_token_ref``.
# Includes Claude Code's local bundle since that file already carries
# refresh tokens.
_OAUTH_AUTH_METHODS = {
    "oauth_device",
    "oauth_redirect",
    "local_claude_code",
}

# Local-credential adoption methods that yield a plain API key (not an
# OAuth bundle). Behaves like the ``api`` auth path for storage purposes
# but the wizard step is different (no prompt, just claim from disk).
_LOCAL_API_AUTH_METHODS = {"local_codex"}

# IAM auth (cloud-provider native auth — AWS profiles, GCP service
# accounts, Azure resources). The wizard collects iam_profile + iam_region
# (+ iam_extra) and the runtime resolves credentials via the cloud SDK's
# default chain. Bedrock ships in PR 5; Vertex + Azure are stubs.
_IAM_AUTH_METHODS = {"iam_aws", "iam_gcp", "iam_azure"}


@router.get("/providers")
async def list_providers(app_state: dict[str, Any] = Depends(get_app_state)) -> list[dict[str, Any]]:
    from ...secrets import get as secrets_get, resolve as secrets_resolve
    cfg = app_state["cfg"]
    if not cfg:
        return []
    result = []
    for name, p in cfg.providers.items():
        key_source: str | None = None
        cred_ref = getattr(p, "credential_ref", None)
        if p.type == "ollama":
            key_source = "anonymous"
        elif cred_ref and secrets_resolve(cred_ref):
            key_source = "credential"
        elif p.use_inline_key and secrets_get(name):
            key_source = "inline"
        elif p.api_key_env and os.environ.get(p.api_key_env):
            key_source = "env"
        result.append({
            "name": name,
            "base_url": p.base_url,
            "has_key": key_source is not None,
            "key_source": key_source,
            "key_env": p.api_key_env,
            "credential_ref": cred_ref,
            "type": p.type,
        })
    return result


@router.get("/providers/{name}/models")
async def list_provider_models(
    name: str,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict[str, Any]:
    import httpx as _httpx
    from ...secrets import get as secrets_get

    cfg = app_state["cfg"]
    if not cfg or name not in cfg.providers:
        return {"models": [], "ok": False, "error": f"provider {name!r} not found"}

    p = cfg.providers[name]
    provider_type = p.type or ("anthropic" if name == "anthropic" else "openai_compat")

    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            if provider_type == "ollama":
                base = (p.base_url or "http://localhost:11434").rstrip("/")
                # Try /api/tags first (native Ollama endpoint)
                try:
                    r = await client.get(f"{base}/api/tags")
                    if r.status_code == 200:
                        data = r.json()
                        models = [m["name"] for m in data.get("models", [])]
                        return {"models": models, "ok": True, "error": None}
                    elif r.status_code == 404:
                        # Fall back to OpenAI-compat /v1/models
                        r2 = await client.get(f"{base}/v1/models")
                        if r2.status_code == 200:
                            data2 = r2.json()
                            models = [m["id"] for m in data2.get("data", [])]
                            return {"models": models, "ok": True, "error": None}
                        else:
                            return {"models": [], "ok": False, "error": f"HTTP {r2.status_code} from {base}/v1/models"}
                    else:
                        return {"models": [], "ok": False, "error": f"HTTP {r.status_code} from {base}/api/tags"}
                except _httpx.ConnectError as exc:
                    return {"models": [], "ok": False, "error": f"connection refused — is Ollama running? ({exc})"}

            elif provider_type == "anthropic":
                # Resolve key — credential_ref > inline > env, mirroring the
                # provider registry resolver.
                from ...secrets import resolve as secrets_resolve
                api_key = ""
                cred_ref = getattr(p, "credential_ref", None)
                if cred_ref:
                    api_key = secrets_resolve(cred_ref) or ""
                if not api_key and p.use_inline_key:
                    api_key = secrets_get(name) or ""
                if not api_key and p.api_key_env:
                    api_key = os.environ.get(p.api_key_env, "")
                if not api_key:
                    return {"models": [], "ok": False, "error": "no API key configured for anthropic — set ANTHROPIC_API_KEY or use nexus providers set-key"}
                r = await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
                if r.status_code != 200:
                    return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                data = r.json()
                models = [m["id"] for m in data.get("data", [])]
                return {"models": models, "ok": True, "error": None}

            else:
                # openai_compat
                if not p.base_url:
                    return {"models": [], "ok": False, "error": "base_url not configured for this provider"}
                from ...secrets import resolve as secrets_resolve
                api_key = ""
                cred_ref = getattr(p, "credential_ref", None)
                if cred_ref:
                    api_key = secrets_resolve(cred_ref) or ""
                if not api_key and p.use_inline_key:
                    api_key = secrets_get(name) or ""
                if not api_key and p.api_key_env:
                    api_key = os.environ.get(p.api_key_env, "")
                if not api_key:
                    return {"models": [], "ok": False, "error": f"no API key configured — set {p.api_key_env or 'an API key'} or use nexus providers set-key"}
                headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
                base = p.base_url.rstrip("/")
                r = await client.get(f"{base}/models", headers=headers)
                if r.status_code != 200:
                    return {"models": [], "ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
                data = r.json()
                models = [m["id"] for m in data.get("data", [])]
                return {"models": models, "ok": True, "error": None}

    except _httpx.TimeoutException:
        return {"models": [], "ok": False, "error": "request timed out (5s)"}
    except Exception as exc:
        return {"models": [], "ok": False, "error": str(exc)}


@router.post("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
async def set_provider_key(
    name: str,
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
    locale: str = Depends(get_locale),
) -> None:
    from ...config_file import load as load_cfg, save as save_cfg
    from ... import secrets as _secrets
    api_key = body.get("api_key", "")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("errors.providers.api_key_required", locale),
        )
    cfg = app_state["cfg"] or load_cfg()
    if name not in cfg.providers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.providers.not_found", locale, name=name),
        )
    _secrets.set(name, api_key, kind="provider")
    cfg.providers[name].use_inline_key = True
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)


@router.delete("/providers/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
async def clear_provider_key(
    name: str,
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
    locale: str = Depends(get_locale),
) -> None:
    from ...config_file import load as load_cfg, save as save_cfg
    from ... import secrets as _secrets
    cfg = app_state["cfg"] or load_cfg()
    if name not in cfg.providers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.providers.not_found", locale, name=name),
        )
    _secrets.delete(name)
    cfg.providers[name].use_inline_key = False
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)


@router.put("/providers/{name}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def set_provider_credential(
    name: str,
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
    locale: str = Depends(get_locale),
) -> None:
    """Point a provider at a named entry in the credential store.

    Body: ``{"credential_ref": "<NAME>" | null}``. Passing ``null`` clears
    the link; the provider falls back to legacy inline/env paths.

    When a non-null ``credential_ref`` is set we also clear ``use_inline_key``
    and ``api_key_env`` — the user explicitly chose the credential path, and
    leaving the legacy fields populated would silently mask configuration
    drift (e.g. an unset env var hiding behind a credential ref the user
    later deletes).
    """
    from ...config_file import load as load_cfg, save as save_cfg

    cfg = app_state["cfg"] or load_cfg()
    if name not in cfg.providers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.providers.not_found", locale, name=name),
        )
    raw_ref = body.get("credential_ref")
    if raw_ref is not None and (not isinstance(raw_ref, str) or not raw_ref):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=t("errors.providers.credential_ref_invalid", locale),
        )
    p = cfg.providers[name]
    p.credential_ref = raw_ref or None
    if raw_ref:
        p.use_inline_key = False
        p.api_key_env = ""
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)


@router.delete("/providers/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    name: str,
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
    locale: str = Depends(get_locale),
) -> None:
    """Remove a provider + its model entries from the config.

    Stored credentials are NOT deleted — they may be reused by another
    provider or skill, and a name like ``OPENAI_API_KEY`` outlives any
    one provider row. Use ``DELETE /credentials/{name}`` to drop the
    credential itself.
    """
    from ...config_file import load as load_cfg, save as save_cfg

    cfg = app_state["cfg"] or load_cfg()
    if name not in cfg.providers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=t("errors.providers.not_found", locale, name=name),
        )
    del cfg.providers[name]
    cfg.models = [m for m in cfg.models if m.provider != name]
    if cfg.agent.default_model and not any(m.id == cfg.agent.default_model for m in cfg.models):
        cfg.agent.default_model = ""
    save_cfg(cfg)
    _rebuild_registry(cfg, app_state, a)


# ── wizard: atomic create-or-update + connection test ───────────────────────


def _snapshot_secrets(names: list[str]) -> dict[str, tuple[str, dict[str, Any]] | None]:
    """Capture current value + meta for each name so a wizard rollback
    can restore exactly what was there. ``None`` = name was unset.
    """
    from ... import secrets as _secrets

    snap: dict[str, tuple[str, dict[str, Any]] | None] = {}
    raw = _secrets._load_raw()
    for n in names:
        cur = raw["keys"].get(n)
        meta = raw["meta"].get(n)
        if cur is None:
            snap[n] = None
        else:
            snap[n] = (cur, dict(meta) if meta else {})
    return snap


def _restore_secrets(snap: dict[str, tuple[str, dict[str, Any]] | None]) -> None:
    from ... import secrets as _secrets

    for name, prev in snap.items():
        if prev is None:
            _secrets.delete(name)
        else:
            value, meta = prev
            kind = (meta.get("kind") if meta else None) or "generic"
            skill = meta.get("skill") if meta else None
            _secrets.set(name, value, kind=kind, skill=skill)


def _wizard_apply_provider_changes(
    *,
    cfg: Any,
    name: str,
    catalog_id: str | None,
    auth_method_id: str,
    runtime_kind: str,
    auth_kind: str,
    base_url: str,
    credential_ref: str | None,
    oauth_token_ref: str | None,
    iam_profile: str,
    iam_region: str,
    iam_extra: dict[str, str],
    type_for_legacy: str,
) -> None:
    """Mutate ``cfg.providers[name]`` in place — create when absent, edit
    when present. Mirrors the field set the wizard sends."""
    from ...config_file import ProviderConfig

    existing = cfg.providers.get(name)
    if existing is None:
        cfg.providers[name] = ProviderConfig(
            base_url=base_url,
            type=type_for_legacy,
            runtime_kind=runtime_kind,
            auth_kind=auth_kind,
            credential_ref=credential_ref,
            oauth_token_ref=oauth_token_ref,
            catalog_id=catalog_id,
            iam_profile=iam_profile,
            iam_region=iam_region,
            iam_extra=dict(iam_extra),
            api_key_env="",
            use_inline_key=False,
        )
        return
    existing.base_url = base_url
    existing.type = type_for_legacy
    existing.runtime_kind = runtime_kind
    existing.auth_kind = auth_kind
    existing.credential_ref = credential_ref
    existing.oauth_token_ref = oauth_token_ref
    existing.catalog_id = catalog_id
    existing.iam_profile = iam_profile
    existing.iam_region = iam_region
    existing.iam_extra = dict(iam_extra)
    # When the wizard binds any credential (api or oauth), drop the legacy
    # paths so a later cleanup doesn't silently fall back to a stale env
    # var or inline key.
    if credential_ref or oauth_token_ref:
        existing.api_key_env = ""
        existing.use_inline_key = False


def _wizard_apply_models(cfg: Any, provider_name: str, models: list[str]) -> None:
    """Replace the model list for ``provider_name`` with ``models``.

    Existing entries whose ``provider != provider_name`` are kept. We
    intentionally do a full replace rather than merge so the wizard's
    chip selections are authoritative.
    """
    from ...config_file import ModelEntry

    cfg.models = [m for m in cfg.models if m.provider != provider_name]
    seen: set[str] = set()
    for model_name in models:
        model_name = (model_name or "").strip()
        if not model_name or model_name in seen:
            continue
        seen.add(model_name)
        model_id = f"{provider_name}/{model_name}"
        cfg.models.append(
            ModelEntry(id=model_id, provider=provider_name, model_name=model_name)
        )
    # If the agent has no default yet and we just added a model, adopt it
    # so the chat view becomes immediately usable. Mirrors POST /models.
    if not cfg.agent.default_model and cfg.models:
        vision = getattr(getattr(cfg, "agent", None), "vision_model", "") or ""
        non_vision = [m for m in cfg.models if m.id != vision]
        cfg.agent.default_model = (non_vision or cfg.models)[0].id


@router.post("/providers/wizard")
async def wizard_apply(
    body: dict[str, Any],
    app_state: dict[str, Any] = Depends(get_app_state),
    a=Depends(get_agent),
    locale: str = Depends(get_locale),
) -> dict[str, Any]:
    """Atomic create-or-update of a provider + its credentials + its models.

    Body shape (PR 2 — api / anonymous only)::

        {
          "name": "openai",                  # provider name (key in cfg.providers)
          "catalog_id": "openai" | null,     # optional pointer back to catalog entry
          "auth_method_id": "api",           # "api" | "anonymous" (PR 2)
          "runtime_kind": "openai_compat",   # "openai_compat" | "anthropic" | "ollama"
          "base_url": "https://...",
          "credential_ref": "OPENAI_API_KEY" | null,
          "credentials": { "OPENAI_API_KEY": "sk-..." },  # values to write
          "iam_profile": "", "iam_region": "", "iam_extra": {},
          "models": ["gpt-4o", "gpt-4o-mini"]
        }

    Atomicity: secrets are written first, then the config + registry are
    updated. If the config save raises, the secrets snapshot is restored
    so the user is never left with orphan credentials.
    """
    from ...config_file import load as load_cfg, save as save_cfg
    from ... import secrets as _secrets

    name = (body.get("name") or "").strip()
    if not _PROVIDER_NAME_RE.match(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"invalid provider name {name!r}",
        )

    auth_method_id = body.get("auth_method_id") or ""
    if (
        auth_method_id not in _SUPPORTED_AUTH_METHODS_PR2
        and auth_method_id not in _OAUTH_AUTH_METHODS
        and auth_method_id not in _LOCAL_API_AUTH_METHODS
        and auth_method_id not in _IAM_AUTH_METHODS
        and auth_method_id not in _NEXUS_AUTH_METHODS
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"auth method {auth_method_id!r} not yet supported",
        )

    # PR 5: only iam_aws is implemented at runtime so far.
    if auth_method_id in _IAM_AUTH_METHODS and auth_method_id != "iam_aws":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"IAM method {auth_method_id!r} ships in a later release "
            "(only iam_aws is wired so far)",
        )

    runtime_kind = (body.get("runtime_kind") or "openai_compat").strip()
    if runtime_kind not in {"openai_compat", "anthropic", "ollama", "nexus"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unsupported runtime_kind {runtime_kind!r}",
        )
    base_url = (body.get("base_url") or "").strip()
    catalog_id = body.get("catalog_id") or None

    credentials_in = body.get("credentials") or {}
    if not isinstance(credentials_in, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="credentials must be an object",
        )
    for cred_name, cred_value in credentials_in.items():
        if not isinstance(cred_name, str) or not _CREDENTIAL_NAME_RE.match(cred_name):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid credential name {cred_name!r} (must be UPPER_SNAKE_CASE)",
            )
        if not isinstance(cred_value, str) or not cred_value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"credential {cred_name!r} requires a non-empty string value",
            )

    credential_ref_in = body.get("credential_ref")
    if credential_ref_in is not None:
        if not isinstance(credential_ref_in, str) or not credential_ref_in:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="credential_ref must be a non-empty string or null",
            )

    if auth_method_id == "api" or auth_method_id in _LOCAL_API_AUTH_METHODS:
        # API auth requires a credential to bind. Either it's pre-existing
        # in the secret store (the local_* path always lands here, since
        # the claim endpoint wrote the key before the wizard submitted)
        # or it's being written this turn.
        if not credential_ref_in:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="api auth requires credential_ref",
            )
        if credential_ref_in not in credentials_in and not _secrets.exists(credential_ref_in):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"credential_ref {credential_ref_in!r} is not provided in credentials and not already stored",
            )
        if not base_url and runtime_kind == "openai_compat":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="base_url is required for openai_compat providers",
            )

    if auth_method_id == "anonymous":
        if credential_ref_in:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="anonymous auth must not specify credential_ref",
            )
        if not base_url and runtime_kind != "ollama":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="base_url is required for anonymous providers",
            )

    if auth_method_id in _OAUTH_AUTH_METHODS:
        # Wizard ran the flow via /auth/oauth/* and is submitting with the
        # credential name returned by the poll. The bundle is already in
        # secrets.toml; we just bind the provider to it.
        if not credential_ref_in:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="OAuth auth requires credential_ref (returned by /auth/oauth/poll)",
            )
        if not _secrets.get_oauth(credential_ref_in):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"OAuth bundle {credential_ref_in!r} not found in secrets store",
            )

    if auth_method_id in _NEXUS_AUTH_METHODS:
        # Nexus subscription: the apiKey was already written by
        # /auth/nexus/verify (it ran when the popup posted the idToken).
        # The wizard MUST NOT pass any credential values here — and the
        # runtime must be the dedicated ``nexus`` kind.
        if credentials_in:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="nexus_signin must not include a credentials body — "
                "the apiKey is provisioned by /auth/nexus/verify",
            )
        if runtime_kind != "nexus":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="nexus_signin requires runtime_kind=nexus",
            )
        cred_ref_resolved = credential_ref_in or "nexus_api_key"
        if not _secrets.get(cred_ref_resolved):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"nexus credential {cred_ref_resolved!r} not in store — "
                "complete the popup sign-in before applying the wizard",
            )
        # Carry the resolved name forward so the provider config binds
        # against it explicitly.
        credential_ref_in = cred_ref_resolved
        if not base_url:
            base_url = "https://llm.nexus-model.us/v1"

    if auth_method_id == "anonymous":
        auth_kind = "anonymous"
    elif auth_method_id in _OAUTH_AUTH_METHODS:
        auth_kind = "oauth"
    elif auth_method_id in _IAM_AUTH_METHODS:
        auth_kind = "iam"
    else:
        # ``api`` / ``_LOCAL_API_AUTH_METHODS`` (e.g. local_codex) /
        # ``nexus_signin`` all end with a plain API key bound via
        # credential_ref.
        auth_kind = "api"
    iam_profile = (body.get("iam_profile") or "").strip()
    iam_region = (body.get("iam_region") or "").strip()
    iam_extra = body.get("iam_extra") or {}
    if not isinstance(iam_extra, dict):
        iam_extra = {}
    models = body.get("models") or []
    if not isinstance(models, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="models must be an array of strings",
        )

    # 1. Snapshot secrets we're about to overwrite, then write the new values.
    snap = _snapshot_secrets(list(credentials_in.keys()))
    try:
        for cred_name, cred_value in credentials_in.items():
            _secrets.set(cred_name, cred_value, kind="provider")

        # 2. Apply config changes (load fresh to avoid stomping concurrent edits).
        cfg = load_cfg()
        _wizard_apply_provider_changes(
            cfg=cfg,
            name=name,
            catalog_id=catalog_id,
            auth_method_id=auth_method_id,
            runtime_kind=runtime_kind,
            auth_kind=auth_kind,
            base_url=base_url,
            credential_ref=credential_ref_in if auth_kind == "api" else None,
            oauth_token_ref=credential_ref_in if auth_kind == "oauth" else None,
            iam_profile=iam_profile,
            iam_region=iam_region,
            iam_extra=iam_extra,
            type_for_legacy=runtime_kind,
        )
        _wizard_apply_models(cfg, name, [str(m) for m in models])

        save_cfg(cfg)
    except HTTPException:
        # Validation failure surfaces unchanged; we already wrote secrets,
        # so revert them.
        _restore_secrets(snap)
        raise
    except Exception as exc:
        _restore_secrets(snap)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"wizard apply failed: {exc!s}",
        ) from exc

    # 3. Rebuild registry so the new provider is usable on the next request.
    _rebuild_registry(cfg, app_state, a)

    p = cfg.providers[name]
    return {
        "name": name,
        "catalog_id": p.catalog_id,
        "runtime_kind": p.runtime_kind,
        "auth_kind": p.auth_kind,
        "base_url": p.base_url,
        "credential_ref": p.credential_ref,
        "oauth_token_ref": p.oauth_token_ref,
        "models": [m.model_name for m in cfg.models if m.provider == name],
    }


@router.post("/providers/{name}/test")
async def test_provider_connection(
    name: str,
    app_state: dict[str, Any] = Depends(get_app_state),
) -> dict[str, Any]:
    """Live round-trip against the provider's model-list endpoint.

    A green response from this endpoint means the configured credential
    successfully authenticates. Returns ``{ok, error, latency_ms}``.

    Reuses the discovery code in ``list_provider_models`` rather than
    duplicating per-runtime probing — anything that lets us list models
    is sufficient evidence the provider is reachable + authenticated.
    """
    import time

    cfg = app_state.get("cfg")
    if not cfg or name not in cfg.providers:
        return {"ok": False, "error": f"provider {name!r} not found", "latency_ms": 0}

    t0 = time.monotonic()
    res = await list_provider_models(name, app_state=app_state)
    latency_ms = int((time.monotonic() - t0) * 1000)
    return {
        "ok": bool(res.get("ok")),
        "error": res.get("error"),
        "latency_ms": latency_ms,
    }
