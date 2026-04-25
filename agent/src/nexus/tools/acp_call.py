"""ACP bridge — thin wrapper around :mod:`loom.acp`.

Preserves the Nexus env-var surface (``NEXUS_ACP_GATEWAY_URL``,
``NEXUS_ACP_TOKEN``, ``NEXUS_ACP_SIG_ENCODING``) while the actual
WebSocket / device-key logic lives in Loom so it's shared with other
Loom-based agents.
"""

from __future__ import annotations

import os

from loom.acp import AcpConfig, call_agent

from ..agent.llm import ToolSpec

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


def _config_from_env() -> AcpConfig:
    return AcpConfig(
        gateway_url=os.environ.get("NEXUS_ACP_GATEWAY_URL", ""),
        token=os.environ.get("NEXUS_ACP_TOKEN", ""),
        sig_encoding=os.environ.get("NEXUS_ACP_SIG_ENCODING", "hex"),
    )


def acp_is_configured() -> bool:
    """Return True iff ACP env vars are present so the tool will actually work."""
    return _config_from_env().configured


async def acp_call(agent_id: str, message: str) -> str:
    """Call an external agent over the ACP WebSocket gateway.

    Returns a human-readable string on success or any error — matches
    the Loom contract so the tool never raises across the agent boundary.
    """
    config = _config_from_env()
    if not config.configured:
        return _NOT_CONFIGURED
    return await call_agent(agent_id, message, config)
