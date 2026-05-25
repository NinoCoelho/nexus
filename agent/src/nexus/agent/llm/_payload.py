"""Shared payload-construction helpers for LLM provider adapters."""

from __future__ import annotations

from .types import ChatMessage, LLMError


def resolve_model(model: str | None, default_model: str) -> str:
    resolved = model or default_model
    if not resolved:
        raise LLMError("No model specified: pass model= or set a default at construction")
    return resolved


async def prepare_messages(model: str, messages: list[ChatMessage]) -> list[ChatMessage]:
    from ...multimodal import materialize_messages
    from ...providers.catalog import capabilities_for_model_name

    caps = capabilities_for_model_name(model)
    return await materialize_messages(messages, caps)
