"""Skill model for Nexus."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict


class KeyRequirement(BaseModel):
    """A credential a skill needs to function.

    Drives the first-use prompt: when the agent runs ``skill_view`` and a
    declared key is neither in the env nor in ``~/.nexus/secrets.toml``,
    the user is prompted via a masked HITL form. ``help`` and ``url``
    customize the prompt copy and the "Get it here →" link.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    help: str | None = None
    url: str | None = None


class DerivedFromSource(BaseModel):
    """A single upstream source consulted while building a derived skill."""

    model_config = ConfigDict(frozen=True)

    slug: str = ""
    url: str = ""
    title: str = ""


class DerivedFrom(BaseModel):
    """Provenance of a skill built by the capability wizard.

    Captures the user's plain-language ask plus the upstream sources the
    agent reviewed when synthesizing the SKILL.md. Surfaced in the UI as
    "Built from your request: '…'" so the lineage stays visible without
    putting GitHub URLs front-and-center.
    """

    model_config = ConfigDict(frozen=True)

    wizard_ask: str = ""
    wizard_built_at: str = ""
    sources: tuple[DerivedFromSource, ...] = ()


class Skill(BaseModel):
    """A single skill loaded from disk."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    body: str
    source_dir: Path
    trust: Literal["builtin", "user", "agent"] = "user"
    requires_keys: tuple[KeyRequirement, ...] = ()
    derived_from: DerivedFrom | None = None
    python_version: str | None = None
    has_requirements: bool = False
