"""Auth strategies for outgoing LLM requests.

Different auth modes (static API key, OAuth refresh, AWS SigV4, GCP
service account, …) all reduce to the same thing from the LLM
client's perspective: produce a set of headers (and optionally a
signed request) for each call. Decoupling the auth mode from the
provider class lets us plug in OAuth (PR 4) and IAM (PR 5) without
fragmenting ``OpenAIProvider`` and ``AnthropicProvider``.

PR 2 ships the abstraction + the static Bearer strategy used by every
existing OpenAI-compat provider. ``OAuthRefreshAuth`` / ``AwsSigV4Auth``
/ ``GcpServiceAccountAuth`` / ``AzureKeyAuth`` are added in later PRs
when the corresponding HTTP routes and runtime classes ship.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AuthStrategy(ABC):
    """Source of per-request auth headers.

    ``headers()`` is async so refreshing strategies (OAuth) can do a
    token round-trip without blocking the event loop. Static strategies
    just return a precomputed dict.
    """

    @abstractmethod
    async def headers(self) -> dict[str, str]:
        """Return the auth headers to merge into each outgoing request."""

    async def aclose(self) -> None:
        """Release any resources held by the strategy. Default: no-op."""
        return None


class StaticBearerAuth(AuthStrategy):
    """``Authorization: Bearer <key>`` from a fixed API key.

    Used by every OpenAI-compatible provider that authenticates with a
    static key — OpenAI, OpenRouter, Groq, DeepSeek, Together, Mistral,
    NVIDIA NIM, etc. Anonymous local servers (Ollama, llama.cpp) pass
    a sentinel string ("ollama") to keep wire format consistent — the
    server ignores it.
    """

    __slots__ = ("_api_key",)

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}
