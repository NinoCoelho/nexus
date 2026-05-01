"""Provider catalog: schema + loader.

The catalog ships as ``catalog.json`` next to this module. It is the
source of truth for the provider wizard — the UI fetches it via
``GET /catalog/providers`` and renders the steps from it. Each entry
carries:

* ``id`` / ``display_name`` / ``category`` — index + grouping
* ``runtime_kind`` — picks the LLM client class at runtime
* ``base_url`` / ``base_url_template`` — default endpoint, optionally
  templated for IAM providers (Azure {resource}/{deployment}, Vertex
  {project}/{region})
* ``env_var_names`` — names the wizard offers as defaults when
  creating a credential entry in ``secrets.toml``
* ``auth_methods`` — one or more ways to sign in. Each method has
  ``prompts: [CredentialPrompt]`` (mapped to the form renderer) or an
  ``oauth: OAuthSpec`` (device / redirect flow).
* ``default_models`` — chips shown in step 4 before any /models call.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class CredentialPrompt(BaseModel):
    """One form field shown in the wizard's "Enter credentials" step.

    Mirrors opencode's ``TextPrompt | SelectPrompt`` shape so the
    front-end FormRenderer can drive the entire step from the catalog.
    """

    name: str
    label: str
    kind: Literal["text", "password", "select"] = "text"
    placeholder: str = ""
    help: str = ""
    help_url: str = ""
    required: bool = True
    secret: bool = False
    choices: list[str] | None = None
    default: str = ""
    # Conditional show. {"endpoint_kind": "custom"} means: only show
    # this field when the form value of "endpoint_kind" equals "custom".
    when: dict[str, str] | None = None


class OAuthSpec(BaseModel):
    """OAuth configuration for an ``oauth_device`` / ``oauth_redirect`` method.

    PR 1 ships the schema; PR 4 wires the device + redirect flows.
    ``client_id`` may be empty in the bundled catalog when we don't
    have a registered app yet — those entries surface in the wizard
    but the OAuth method is filtered out at runtime.
    """

    flavor: Literal["device", "redirect"]
    client_id: str = ""
    auth_url: str = ""
    token_url: str = ""
    device_url: str = ""        # device flavor only
    scopes: list[str] = Field(default_factory=list)
    redirect_path: str = "/auth/callback"
    pkce: bool = True


AuthMethodId = Literal[
    "api",
    "oauth_device",
    "oauth_redirect",
    "iam_aws",
    "iam_gcp",
    "iam_azure",
    "anonymous",
    # Local-credential adoption: read from another tool's stored auth on
    # this machine (e.g. Claude Code's keychain entry, Codex's auth.json)
    # instead of running a fresh OAuth round-trip.
    "local_claude_code",
    "local_codex",
]


class AuthMethod(BaseModel):
    id: AuthMethodId
    label: str
    # Lower is shown first. opencode uses this so "Sign in with Pro/Max"
    # comes above "API key" for Anthropic. Default 100 = neutral.
    priority: int = 100
    prompts: list[CredentialPrompt] = Field(default_factory=list)
    oauth: OAuthSpec | None = None
    # When set, the runtime needs an optional dependency that may not
    # be installed (boto3 for iam_aws, google-auth for iam_gcp). The UI
    # surfaces this as an install hint instead of failing silently.
    requires_extra: str = ""


RuntimeKind = Literal[
    "openai_compat",
    "anthropic",
    "ollama",
    "bedrock",
    "vertex",
    "azure_openai",
]

Category = Literal["frontier", "open", "cloud", "local", "aggregator", "other"]

# Capability tags drive the wizard's "show only X" filter and (later) the
# chat model picker. Keep this list short and orthogonal — every tag should
# answer a yes/no question a user would actually filter on.
Capability = Literal[
    "chat",       # standard chat-completion (default for any chat model)
    "tools",      # supports function/tool calling
    "reasoning",  # thinking models (o1, o3, deepseek-r1, glm-thinking, …)
    "vision",     # accepts image inputs
    "audio",      # speech in or out (whisper, tts) — usually NOT a chat model
    "embedding",  # embeddings only — never a chat model
    "image",      # generates image OUTPUT (gpt-image-1, gemini-2.5-flash-image)
    "document",   # accepts native document (PDF) input blocks
]


class ModelInfo(BaseModel):
    """One model entry inside a catalog provider's ``default_models`` list.

    Accepts either a bare string (legacy / quick entry) or an object with
    explicit capability tags. Bare strings are normalised to
    ``{id: name, capabilities: ["chat", "tools"]}`` — a safe default for
    "this is a chat-tuned LLM" entries.
    """

    id: str
    capabilities: list[Capability] = Field(
        default_factory=lambda: ["chat", "tools"]
    )
    # Optional context window in tokens. Surfaced in the UI later; not
    # required at catalog level.
    context_window: int = 0

    @model_validator(mode="before")
    @classmethod
    def _coerce_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"id": value, "capabilities": ["chat", "tools"]}
        return value


class ProviderCatalogEntry(BaseModel):
    id: str
    display_name: str
    category: Category
    runtime_kind: RuntimeKind
    base_url: str = ""
    # Templated form for IAM providers — {region}, {resource}, etc. The
    # wizard fills it in from prompt answers before saving.
    base_url_template: str = ""
    env_var_names: list[str] = Field(default_factory=list)
    auth_methods: list[AuthMethod]
    default_models: list[ModelInfo] = Field(default_factory=list)
    docs_url: str = ""
    icon: str = ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_catalog() -> list[ProviderCatalogEntry]:
    """Load + validate ``catalog.json``. Cached for the process lifetime.

    The cache is intentional: the catalog is bundled (read-only) and
    parsing pydantic models for ~25 entries on every request is wasteful.
    Tests that need a fresh load can call ``load_catalog.cache_clear()``.
    """
    raw = json.loads(files(__package__).joinpath("catalog.json").read_text("utf-8"))
    if not isinstance(raw, list):
        raise ValueError("catalog.json must be a JSON array of provider entries")
    entries = [ProviderCatalogEntry.model_validate(item) for item in raw]
    seen: set[str] = set()
    for e in entries:
        if e.id in seen:
            raise ValueError(f"duplicate provider id in catalog.json: {e.id!r}")
        seen.add(e.id)
        if not e.auth_methods:
            raise ValueError(f"catalog entry {e.id!r} has no auth_methods")
    return entries


def find(provider_id: str) -> ProviderCatalogEntry | None:
    for e in load_catalog():
        if e.id == provider_id:
            return e
    return None


@lru_cache(maxsize=1)
def _model_name_to_capabilities() -> dict[str, set[str]]:
    """Walk the catalog and build a flat ``{model_name: {capabilities}}`` map.

    Cached for the process lifetime; invalidated only by
    ``load_catalog.cache_clear()`` in tests. The same model id can appear
    under multiple providers (e.g. ``gemini-2.5-flash`` is also exposed by
    OpenRouter); we union their capabilities so the lookup is conservative
    — if any provider tags it ``"vision"``, the encoder treats it as
    vision-capable.
    """
    out: dict[str, set[str]] = {}
    for entry in load_catalog():
        for model in entry.default_models:
            out.setdefault(model.id, set()).update(model.capabilities)
    return out


_KNOWN_CAPABILITY_TAGS: frozenset[str] = frozenset(
    [
        "chat",
        "tools",
        "reasoning",
        "vision",
        "audio",
        "embedding",
        "image",
        "document",
    ]
)


def _user_config_capabilities(model_name: str) -> set[str]:
    """Pull capability-like tags from the user's ``~/.nexus/config.toml``
    ``[[models]]`` entries whose ``model_name`` (or trailing-path id)
    matches.

    This is the escape hatch for local GGUFs and any model the bundled
    catalog doesn't know about: add ``tags = ["vision"]`` to a model
    entry and the encoder will start sending image parts through it.
    The user owns the assertion — if their llama-server isn't actually
    set up with ``--mmproj``, the upstream call will fail loudly,
    which is better than us silently dropping the bytes.
    """
    try:
        from ..config_file import load as load_config

        cfg = load_config()
    except Exception:  # noqa: BLE001 — startup races / missing file
        return set()
    out: set[str] = set()
    for m in cfg.models:
        candidates = {m.model_name, m.id}
        if "/" in m.id:
            candidates.add(m.id.rsplit("/", 1)[-1])
        if model_name in candidates:
            for tag in m.tags:
                if tag in _KNOWN_CAPABILITY_TAGS:
                    out.add(tag)
            break
    return out


def capabilities_for_model_name(model_name: str) -> set[str]:
    """Return the capability tags for a resolved upstream model name.

    Used by the LLM provider encoders to decide whether to pass image /
    audio / document parts through natively or fall back to text. Sources
    (unioned, in order):

    1. The bundled provider catalog (``catalog.json``) — covers the
       cloud providers we ship metadata for.
    2. The user's ``~/.nexus/config.toml`` ``[[models]]`` ``tags`` —
       overrides for local GGUFs and unknown models.

    Returns an empty set for fully-unknown models — the encoder then
    conservatively drops non-text parts with a breadcrumb so we never
    send shapes the upstream rejects with HTTP 400.

    Strips a few known prefixes (``openrouter/``, ``models/``) so e.g.
    ``models/gemini-2.5-flash`` and ``gemini-2.5-flash`` resolve the same.
    """
    if not model_name:
        return set()
    table = _model_name_to_capabilities()
    caps: set[str] = set()
    if model_name in table:
        caps = set(table[model_name])
    else:
        for prefix in ("models/", "openrouter/"):
            if model_name.startswith(prefix):
                stripped = model_name[len(prefix):]
                if stripped in table:
                    caps = set(table[stripped])
                    break
        if not caps and "/" in model_name:
            tail = model_name.rsplit("/", 1)[-1]
            if tail in table:
                caps = set(table[tail])
    caps |= _user_config_capabilities(model_name)
    return caps
