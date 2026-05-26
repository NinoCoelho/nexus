from __future__ import annotations

import logging

import httpx

from .. import secrets
from ..config_file import load as load_config
from .models import BrokerMessage, BrokerWebhook

log = logging.getLogger(__name__)

_TIMEOUT = 15.0


def _get_api_key() -> str | None:
    return secrets.get("broker_api_key")


def _get_base_url() -> str:
    try:
        cfg = load_config()
        return cfg.broker.url.rstrip("/")
    except Exception:
        return "https://nexus-broker.dev"


class BrokerClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self._base_url = base_url or _get_base_url()
        self._api_key = api_key or _get_api_key() or ""

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def create_webhook(
        self, name: str, public_key_pem: str, key_type: str = "rsa-2048",
    ) -> BrokerWebhook:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base_url}/api/webhooks",
                headers=self._headers(),
                json={
                    "name": name,
                    "publicKey": public_key_pem,
                    "keyType": key_type,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return BrokerWebhook(
                id=data["id"],
                name=data["name"],
                slug=data["slug"],
                url=data.get("url", f"{self._base_url}/wh/{data['slug']}"),
                key_type=data.get("keyType", key_type),
            )

    async def get_webhook(self, webhook_id: str) -> BrokerWebhook | None:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{self._base_url}/api/webhooks/{webhook_id}",
                headers=self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            outer = resp.json()
            data = outer.get("webhook") or outer
            return self._parse_webhook(data)

    async def list_webhooks(self) -> list[BrokerWebhook]:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{self._base_url}/api/webhooks",
                headers=self._headers(),
            )
            resp.raise_for_status()
            body = resp.json()
            items = body.get("webhooks") or body if isinstance(body, list) else []
            return [self._parse_webhook(w) for w in items]

    def _parse_webhook(self, data: dict) -> BrokerWebhook:
        return BrokerWebhook(
            id=data["id"],
            name=data["name"],
            slug=data["slug"],
            url=data.get("url", f"{self._base_url}/wh/{data['slug']}"),
            key_type=data.get("keyType", "rsa-2048"),
            is_active=data.get("isActive", True),
            message_count=data.get("messageCount", 0),
        )

    async def delete_webhook(self, webhook_id: str) -> bool:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.delete(
                f"{self._base_url}/api/webhooks/{webhook_id}",
                headers=self._headers(),
            )
            return resp.status_code in (200, 204)

    async def dequeue(self, webhook_id: str) -> BrokerMessage | None:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base_url}/api/webhooks/{webhook_id}/dequeue",
                headers=self._headers(),
            )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            outer = resp.json()
            data = outer.get("message") or outer
            if data is None:
                return None
            return BrokerMessage(
                id=data["id"],
                webhook_id=data["webhookId"],
                sequence_num=data["sequenceNum"],
                encrypted_body=data["encryptedBody"],
                encryption_iv=data["encryptionIv"],
                encryption_tag=data.get("encryptionTag"),
                encryption_ephemeral_pk=data.get("encryptionEphemeralPk"),
                encrypted_key=data.get("encryptedKey"),
                status=data.get("status", "locked"),
                content_type=data.get("contentType", "application/json"),
                size_bytes=data.get("sizeBytes", 0),
            )

    async def commit(self, webhook_id: str, message_id: str) -> bool:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base_url}/api/webhooks/{webhook_id}/messages/{message_id}/commit",
                headers=self._headers(),
            )
            return resp.status_code in (200, 204)

    async def error(
        self, webhook_id: str, message_id: str, reason: str = "processing failed",
    ) -> bool:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._base_url}/api/webhooks/{webhook_id}/messages/{message_id}/error",
                headers=self._headers(),
                json={"reason": reason[:1000]},
            )
            return resp.status_code in (200, 204)
