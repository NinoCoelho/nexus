"""ACP bridge — external agent calls via the ACP gateway.

Environment variables
---------------------
NEXUS_ACP_GATEWAY_URL
    WebSocket URL of the ACP gateway (e.g. ``ws://gateway.internal/acp``).
    When unset the tool returns a ``_NOT_CONFIGURED`` message rather than
    failing loudly — the agent degrades gracefully without a gateway.

NEXUS_ACP_TOKEN
    Bearer token sent in the ``Authorization`` header on the initial WS
    upgrade. Leave unset for anonymous gateways.

NEXUS_ACP_SIG_ENCODING
    Encoding for the ed25519 device signature sent during the auth
    handshake.  ``"hex"`` (default) or ``"base64"``.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import websockets

from ..agent.llm import ToolSpec
from .acp_device import load_or_create_keypair, sign_challenge

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
    """Call an external agent over an ACP WebSocket gateway.

    Protocol
    --------
    1. Connect with an optional Bearer token.
    2. If the server sends ``{"type": "challenge", "nonce": str}``, respond
       with an ``auth`` frame containing the device public key and ed25519
       signature, then wait for ``{"type": "auth_ok"}``.
    3. Send a ``call`` frame.
    4. Collect ``delta`` frames (streaming text) until a ``done`` or ``error``
       frame is received.
    """
    url = os.environ.get("NEXUS_ACP_GATEWAY_URL")
    if not url:
        return _NOT_CONFIGURED

    try:
        keypair = load_or_create_keypair()
    except Exception as exc:
        return f"ACP device key error: {exc}"

    token = os.environ.get("NEXUS_ACP_TOKEN", "")
    request_id = uuid.uuid4().hex
    sig_encoding = os.environ.get("NEXUS_ACP_SIG_ENCODING", "hex")

    try:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with websockets.connect(
            url,
            additional_headers=headers,
            open_timeout=10,
        ) as ws:
            # Auth handshake — server sends {type: "challenge", nonce: str}
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            challenge = json.loads(raw)
            if challenge.get("type") == "challenge":
                sig = sign_challenge(keypair, challenge["nonce"], encoding=sig_encoding)
                await ws.send(json.dumps({
                    "type": "auth",
                    "device_id": keypair.public_hex,
                    "signature": sig,
                }))
                auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if auth_resp.get("type") != "auth_ok":
                    return f"ACP auth failed: {auth_resp.get('reason', 'unknown')}"
            # If no challenge frame, assume an anonymous gateway and proceed.

            # Send the call request.
            await ws.send(json.dumps({
                "type": "call",
                "agent_id": agent_id,
                "message": message,
                "request_id": request_id,
            }))

            # Collect streaming response frames.
            parts: list[str] = []
            async for raw in ws:
                frame = json.loads(raw)
                if frame.get("type") == "delta":
                    parts.append(frame.get("text", ""))
                elif frame.get("type") == "done":
                    parts.append(frame.get("result", ""))
                    break
                elif frame.get("type") == "error":
                    return f"ACP error: {frame.get('message', 'remote error')}"
            return "".join(parts) or "(empty response)"

    except asyncio.TimeoutError:
        return "ACP error: connection timed out"
    except Exception as exc:
        return f"ACP error: {exc}"
