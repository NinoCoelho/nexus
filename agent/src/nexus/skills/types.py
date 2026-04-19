"""Skill model for Nexus."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Skill(BaseModel):
    """A single skill loaded from disk."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    body: str
    source_dir: Path
    trust: Literal["builtin", "user", "agent"] = "user"
