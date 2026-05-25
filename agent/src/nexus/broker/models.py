from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BrokerWebhook:
    id: str
    name: str
    slug: str
    url: str
    key_type: str = "rsa-2048"
    is_active: bool = True
    description: str | None = None


@dataclass
class BrokerMessage:
    id: str
    webhook_id: str
    sequence_num: int
    encrypted_body: str
    encryption_iv: str
    encryption_tag: str | None = None
    encryption_ephemeral_pk: str | None = None
    encrypted_key: str | None = None
    status: str = "locked"
    content_type: str = "application/json"
    size_bytes: int = 0
