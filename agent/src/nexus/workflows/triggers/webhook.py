"""Webhook trigger manager.

Registers webhook tokens on workflow save and unregisters on delete.
The actual webhook POST handler lives in server/routes/workflows.py.
This module provides helpers for token lifecycle management.
"""

from __future__ import annotations

import datetime
import logging
import secrets
from typing import Any

from ..models import TriggerType, WorkflowDef
from ..store import WorkflowStore
from .base import TriggerDriver

log = logging.getLogger(__name__)


class WebhookTriggerDriver(TriggerDriver):
    def __init__(self, store: WorkflowStore) -> None:
        self._store = store

    @property
    def trigger_type(self) -> TriggerType:
        return TriggerType.webhook

    async def start(self, workflow_path: str, wf: WorkflowDef, trigger_config: Any = None) -> None:
        for trigger in wf.triggers:
            if trigger.type != TriggerType.webhook:
                continue
            if not trigger.token:
                trigger.token = secrets.token_hex(16)
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._store.register_webhook_token(trigger.token, workflow_path, trigger.id, now)
            log.info("webhook trigger registered: %s -> %s", trigger.id, workflow_path)

    async def stop(self, workflow_path: str, trigger_id: str) -> None:
        self._store.remove_webhook_tokens(workflow_path)
        log.info("webhook triggers removed for %s", workflow_path)
