"""Lightweight cost estimation for tracked token usage.

Deliberately minimal vs. Hermes' ``agent/usage_pricing.py`` — Hermes
ships a pricing engine with custom-contract overrides, live fetches
from provider endpoints, fallback cascades, and per-request fees.
Nexus ships only what it *already* captures today:

* Per-session ``input_tokens`` / ``output_tokens`` counts.
* A single ``model`` slug chosen by the user or the router.

Given that scope, the right abstraction is a dict lookup over a
hard-coded pricing table with graceful ``None`` returns for unknown
models. When the richer signals land (cache read/write tokens,
per-provider billing modes, fetched pricing) this module grows into
something closer to Hermes' version — but not before.

The pricing numbers below are per-1M-token USD, sourced from each
provider's public docs at the time this file was written. They drift;
treat them as "good enough for a dashboard", not accounting-grade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PricingEntry:
    """Per-1M-token USD pricing for one model."""

    input_per_million: float
    output_per_million: float
    source: str = "official_docs_snapshot"


# Canonicalization: strip the provider prefix before lookup so both
# "openai/gpt-4o" and "gpt-4o" find the same entry. Keys here are the
# *suffix* form.
_PRICING: dict[str, PricingEntry] = {
    # OpenAI
    "gpt-4o": PricingEntry(2.50, 10.00),
    "gpt-4o-mini": PricingEntry(0.15, 0.60),
    "gpt-4-turbo": PricingEntry(10.00, 30.00),
    "gpt-3.5-turbo": PricingEntry(0.50, 1.50),
    "o1": PricingEntry(15.00, 60.00),
    "o1-mini": PricingEntry(3.00, 12.00),
    "o3-mini": PricingEntry(1.10, 4.40),
    # Anthropic
    "claude-opus-4-20250514": PricingEntry(15.00, 75.00),
    "claude-sonnet-4-20250514": PricingEntry(3.00, 15.00),
    "claude-3-5-sonnet-20241022": PricingEntry(3.00, 15.00),
    "claude-3-5-haiku-20241022": PricingEntry(0.80, 4.00),
    "claude-3-opus-20240229": PricingEntry(15.00, 75.00),
    "claude-3-sonnet-20240229": PricingEntry(3.00, 15.00),
    "claude-3-haiku-20240307": PricingEntry(0.25, 1.25),
    # Google (Gemini)
    "gemini-1.5-pro": PricingEntry(1.25, 5.00),
    "gemini-1.5-flash": PricingEntry(0.075, 0.30),
    "gemini-2.0-flash": PricingEntry(0.10, 0.40),
    # Groq (free-tier-ish but list prices)
    "llama-3.1-70b-versatile": PricingEntry(0.59, 0.79),
    "llama-3.1-8b-instant": PricingEntry(0.05, 0.08),
    # Mistral
    "mistral-large-latest": PricingEntry(2.00, 6.00),
    "mistral-small-latest": PricingEntry(0.20, 0.60),
    # DeepSeek
    "deepseek-chat": PricingEntry(0.27, 1.10),
    "deepseek-reasoner": PricingEntry(0.55, 2.19),
}


def _canonical(model: str) -> str:
    """Strip provider prefix (``openai/gpt-4o`` → ``gpt-4o``)."""
    if not model:
        return ""
    # Many clients send "provider/model" or "provider:model".
    for sep in ("/", ":"):
        if sep in model:
            model = model.rsplit(sep, 1)[-1]
    return model.strip()


def lookup_pricing(model: str) -> Optional[PricingEntry]:
    """Return the pricing entry for ``model`` or ``None`` if unknown.

    Unknown isn't a failure — it just means we can't price this session.
    Callers should render the cost column as ``N/A`` in that case.
    """
    if not model:
        return None
    return _PRICING.get(_canonical(model))


def estimate_cost(
    model: str, *, input_tokens: int, output_tokens: int
) -> tuple[Optional[float], str]:
    """Return ``(cost_usd, status)`` for one token-count pair.

    * ``status == "ok"`` when we had pricing and the math ran.
    * ``status == "unknown"`` when the model isn't in the pricing table.
    * ``status == "zero"`` when pricing is known but the token counts
      are zero (nothing to charge).

    The cost is computed at 1e-6 resolution from per-1M rates; callers
    should format to cents (``f"${cost:.4f}"`` or similar).
    """
    entry = lookup_pricing(model)
    if entry is None:
        return None, "unknown"
    if input_tokens <= 0 and output_tokens <= 0:
        return 0.0, "zero"
    cost = (
        input_tokens * entry.input_per_million
        + output_tokens * entry.output_per_million
    ) / 1_000_000.0
    return cost, "ok"


def has_known_pricing(model: str) -> bool:
    """Quick-check so callers can render "(no pricing)" hints."""
    return lookup_pricing(model) is not None
