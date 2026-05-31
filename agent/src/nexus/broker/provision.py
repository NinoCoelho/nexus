from __future__ import annotations

import logging

from .client import BrokerClient
from .crypto import load_or_generate_private_key
from .models import BrokerWebhook

log = logging.getLogger(__name__)

_cache: dict[str, BrokerWebhook] = {}


async def ensure_broker_endpoint(
    client: BrokerClient,
    endpoint_type: str,
    endpoint_key: str,
    name: str,
    existing_broker_id: str | None = None,
    existing_broker_slug: str | None = None,
) -> BrokerWebhook | None:
    if not client.available:
        return None

    cache_key = f"{endpoint_type}:{endpoint_key}"
    if cache_key in _cache:
        return _cache[cache_key]

    if existing_broker_id:
        try:
            wh = await client.get_webhook(existing_broker_id)
            if wh and wh.is_active:
                _cache[cache_key] = wh
                log.info(
                    "broker: verified existing webhook %s (slug=%s) for %s %s",
                    wh.id, wh.slug, endpoint_type, endpoint_key,
                )
                return wh
            log.warning(
                "broker: webhook %s no longer active on broker for %s %s — refusing to replace",
                existing_broker_id, endpoint_type, endpoint_key,
            )
            return None
        except Exception:
            log.warning(
                "broker: failed to verify webhook %s for %s %s — refusing to replace",
                existing_broker_id, endpoint_type, endpoint_key,
                exc_info=True,
            )
            return None

    try:
        pub_pem, _ = load_or_generate_private_key()
        wh = await client.create_webhook(
            name=name,
            public_key_pem=pub_pem,
            key_type="rsa-2048",
        )
        _cache[cache_key] = wh
        log.info(
            "broker: created webhook %s (slug=%s) for %s %s",
            wh.id, wh.slug, endpoint_type, endpoint_key,
        )
        return wh
    except Exception:
        log.exception(
            "broker: failed to create webhook for %s %s",
            endpoint_type, endpoint_key,
        )
        return None


def clear_cache() -> None:
    _cache.clear()
