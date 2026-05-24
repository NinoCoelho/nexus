"""LLM provider port — OpenAI-compatible + Anthropic native adapters.

Re-exports all public symbols so ``from nexus.agent.llm import X`` keeps
working after the module was split into a package.
"""

from .anthropic import AnthropicProvider
from .auth import AuthStrategy, StaticBearerAuth
from .openai import OpenAIProvider
from .types import (
    ChatMessage,
    ChatResponse,
    ContentPart,
    LLMError,
    LLMProvider,
    LLMTransportError,
    MalformedOutputError,
    StopReason,
    StreamEvent,
    ToolCall,
)

# Re-export loom types that were previously re-exported from llm.py
from loom.types import Role, ToolSpec, Usage

__all__ = [
    "AnthropicProvider",
    "AuthStrategy",
    "OpenAIProvider",
    "StaticBearerAuth",
    "ChatMessage",
    "ChatResponse",
    "ContentPart",
    "LLMError",
    "LLMProvider",
    "LLMTransportError",
    "MalformedOutputError",
    "Role",
    "StopReason",
    "StreamEvent",
    "ToolCall",
    "ToolSpec",
    "Usage",
]
