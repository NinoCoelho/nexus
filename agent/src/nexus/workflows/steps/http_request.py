from __future__ import annotations

from typing import Any

from ..expressions import resolve_templates
from ..models import StepConfig


async def execute_step(
    engine: Any,
    step: StepConfig,
    ctx: dict[str, Any],
    resolved_input: dict[str, Any] | None,
) -> Any:
    import aiohttp
    from ...secrets import resolve as resolve_secret

    url = resolve_templates(step.url or "", ctx)
    method = step.method.upper()
    headers = resolve_templates(step.headers or {}, ctx)
    body = resolve_templates(step.body, ctx) if step.body else None

    if step.custom_headers:
        resolved_custom = resolve_templates(step.custom_headers, ctx)
        headers.update(resolved_custom)

    if step.auth_type == "basic":
        user = resolve_templates(step.auth_username or "", ctx)
        pwd_name = step.auth_password_credential
        pwd = resolve_secret(pwd_name) if pwd_name else ""
        import base64

        token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    elif step.auth_type == "apikey":
        key_val = resolve_secret(step.auth_credential) if step.auth_credential else ""
        prefix = step.auth_prefix or "Bearer"
        if step.auth_location == "query":
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{step.auth_query_name or 'api_key'}={key_val}"
        elif step.auth_location == "header" and step.auth_header_name:
            headers[step.auth_header_name] = f"{prefix} {key_val}" if prefix else key_val
        else:
            headers["Authorization"] = f"{prefix} {key_val}"

    elif step.auth_type == "oauth":
        token_val = resolve_secret(step.auth_credential) if step.auth_credential else ""
        headers["Authorization"] = f"Bearer {token_val}"

    async with aiohttp.ClientSession() as session:
        kwargs: dict[str, Any] = {"headers": headers}
        if body is not None and method in ("POST", "PUT", "PATCH"):
            if isinstance(body, (dict, list)):
                kwargs["json"] = body
            else:
                kwargs["data"] = str(body)
        async with session.request(method, url, **kwargs) as resp:
            text = await resp.text()
            return {
                "status": resp.status,
                "body": text[:10000],
                "headers": dict(resp.headers),
            }
