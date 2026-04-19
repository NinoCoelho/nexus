"""ACP bridge stub — external agent calls via the ACP gateway."""

from __future__ import annotations

from ..agent.llm import ToolSpec

# TODO: port knowspace /lib/gateway.js — WS + ed25519 device auth

ACP_CALL_TOOL = ToolSpec(
    name="acp_call",
    description="Call an external agent over the ACP gateway.",
    parameters={
        "type": "object",
        "properties": {
            "agent_id": {"type": "string", "description": "Target agent ID."},
            "message": {"type": "string", "description": "Message to send."},
        },
        "required": ["agent_id", "message"],
    },
)

_NOT_CONFIGURED = (
    "ACP bridge not configured. "
    "Set NEXUS_ACP_GATEWAY_URL and NEXUS_ACP_TOKEN to enable external agent calls."
)


async def acp_call(agent_id: str, message: str) -> str:
    return _NOT_CONFIGURED
