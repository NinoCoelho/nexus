"""Trigger driver base class and discovery utilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import TriggerType, WorkflowDef


class TriggerDriver(ABC):
    @abstractmethod
    async def start(self, workflow_path: str, wf: WorkflowDef, trigger_config: Any) -> None:
        ...

    @abstractmethod
    async def stop(self, workflow_path: str, trigger_id: str) -> None:
        ...

    @property
    @abstractmethod
    def trigger_type(self) -> TriggerType:
        ...
