"""User-preference settings — separate from the LLM ``NexusConfig``
(which holds provider / model state).

Lives at ``~/.nexus/settings.json``. No secrets in this file, so it's
safe for endpoints to read and write without going through the secret-
aware code path that guards provider keys.

Kept deliberately small: for every flag we add here, the UI gains a
toggle and the backend gains a code path. Only add entries the agent's
behavior actually reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field


def default_settings_path() -> Path:
    """Default on-disk location. Overridable by passing ``path=`` to
    :class:`SettingsStore` — used in tests to isolate a tmp file."""
    return Path.home() / ".nexus" / "settings.json"


class Settings(BaseModel):
    """Persisted user preferences."""

    model_config = ConfigDict(frozen=True)

    version: int = 1

    # When True, ``ask_user(kind='confirm')`` auto-answers "yes" without
    # opening the UI dialog. The event is still recorded in the session
    # trace for audit. Does NOT affect ``kind='choice'`` or ``kind='text'``
    # — those always require real input.
    yolo_mode: bool = Field(
        default=False,
        description=(
            "Auto-approve confirm-style prompts without showing the dialog. "
            "Audit trail is preserved in the session event stream."
        ),
    )


class SettingsStore:
    """Read/write ``settings.json`` with in-process caching.

    Reads are lock-guarded because the ``AskUserHandler`` hits this on
    every ``ask_user`` call from the async event loop, and the
    ``POST /settings`` handler may update concurrently. Writes are
    atomic (tmpfile + rename) so a crashed write never leaves a
    half-written file that breaks the next startup.
    """

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = path or default_settings_path()
        self._lock = Lock()
        self._cache: Settings | None = None

    def get(self) -> Settings:
        with self._lock:
            if self._cache is not None:
                return self._cache
            self._cache = self._load_locked()
            return self._cache

    def update(self, **changes: object) -> Settings:
        """Merge ``changes`` into the current settings and persist.

        Returns the new Settings so callers don't have to re-read.
        Unknown keys raise ``ValueError`` — better to fail loudly than
        silently drop a field the UI thought it was setting.
        """
        with self._lock:
            current = self._cache if self._cache is not None else self._load_locked()
            allowed = set(Settings.model_fields.keys())
            unknown = set(changes) - allowed
            if unknown:
                raise ValueError(f"unknown settings field(s): {sorted(unknown)}")
            updated = current.model_copy(update=changes)
            self._write_locked(updated)
            self._cache = updated
            return updated

    def _load_locked(self) -> Settings:
        if not self._path.exists():
            return Settings()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Corrupt or unreadable file: fall back to defaults rather
            # than crashing the server on startup. The next successful
            # write will overwrite the corruption.
            return Settings()
        try:
            return Settings.model_validate(raw)
        except Exception:
            # Schema drift — e.g. a future version field we don't know
            # how to interpret. Same fallback.
            return Settings()

    def _write_locked(self, settings: Settings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(settings.model_dump(), indent=2), encoding="utf-8"
        )
        tmp.replace(self._path)
