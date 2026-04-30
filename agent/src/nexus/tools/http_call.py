"""Simple HTTP tool for the agent to reach external APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ..agent.llm import ToolSpec

HTTP_CALL_TOOL = ToolSpec(
    name="http_call",
    description="Make an HTTP GET or POST request to an external URL.",
    parameters={
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": ["GET", "POST"], "description": "HTTP method."},
            "url": {"type": "string", "description": "Full URL to call."},
            "headers": {"type": "object", "description": "Optional request headers."},
            "body": {"type": "object", "description": "Optional JSON body for POST."},
        },
        "required": ["method", "url"],
    },
)


@dataclass
class HttpResult:
    status: int | None
    ok: bool
    body: str
    error: str | None = None

    def to_text(self) -> str:
        return json.dumps(
            {"status": self.status, "ok": self.ok, "body": self.body, "error": self.error},
            ensure_ascii=False,
        )


class HttpCallHandler:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=30.0)

    async def invoke(self, args: dict[str, Any]) -> HttpResult:
        # Tool-boundary substitution: ``$NAME`` placeholders in url, headers,
        # or body are resolved against env vars / secrets.toml right before
        # the request goes out. The LLM never sees the raw values.
        from ..secrets_substitute import resolve as _resolve_secrets

        args = _resolve_secrets(args)
        method = args.get("method", "GET").upper()
        url = args.get("url", "")
        headers = args.get("headers") or {}
        body = args.get("body")
        if not url:
            return HttpResult(status=None, ok=False, body="", error="`url` is required")
        try:
            if method == "POST":
                resp = await self._client.post(url, json=body, headers=headers)
            else:
                resp = await self._client.get(url, headers=headers)
            return HttpResult(
                status=resp.status_code,
                ok=resp.is_success,
                body=resp.text[:10_000],
            )
        except httpx.HTTPError as exc:
            return HttpResult(status=None, ok=False, body="", error=str(exc))

    async def aclose(self) -> None:
        await self._client.aclose()
