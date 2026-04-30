"""Provider catalog package.

The catalog is a bundled JSON describing every provider the wizard knows
how to configure: display name, base URL, env-var hints, supported auth
methods (API key / OAuth / cloud IAM / anonymous), and default model
list. Loaded once via ``importlib.resources`` — no network at config
time. Mirrors the shape used by ``sst/opencode``'s plugin auth contract.
"""

from .catalog import (
    AuthMethod,
    Capability,
    CredentialPrompt,
    ModelInfo,
    OAuthSpec,
    ProviderCatalogEntry,
    find,
    load_catalog,
)

__all__ = [
    "AuthMethod",
    "Capability",
    "CredentialPrompt",
    "ModelInfo",
    "OAuthSpec",
    "ProviderCatalogEntry",
    "find",
    "load_catalog",
]
