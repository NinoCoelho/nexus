"""Bridge between Nexus's LLM/tool interfaces and loom's contracts.

Nexus providers return a flat ChatResponse with dict-typed ToolCall.arguments.
Loom expects a wrapped ChatResponse(message=ChatMessage(...), ...) with
ToolCall.arguments as a JSON string, and all messages use the loom ChatMessage
schema.  This module provides:

* ``LoomProviderAdapter`` — wraps a Nexus LLMProvider to satisfy
  ``loom.llm.base.LLMProvider``.
* ``build_tool_registry`` — registers all Nexus tool handlers into a
  ``loom.tools.registry.ToolRegistry`` using loom's ToolHandler ABC.
"""

from .adapter import LoomProviderAdapter
from .message import (
    _loom_to_nexus_message,
    _nexus_stop_to_loom,
    _nexus_to_loom_message,
)
from .registry import AgentHandlers, _SimpleToolHandler, build_tool_registry

__all__ = [
    "LoomProviderAdapter",
    "AgentHandlers",
    "_SimpleToolHandler",
    "build_tool_registry",
    "_nexus_to_loom_message",
    "_loom_to_nexus_message",
    "_nexus_stop_to_loom",
]
