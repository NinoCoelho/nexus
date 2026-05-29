"""Background poll of nexus-model /api/status for tier / model changes.

Every ``poll_seconds`` seconds the watcher:
  1. Calls :func:`nexus_account.fetch_status` with the stored apiKey.
  2. Compares the returned ``models`` array against the last snapshot.
  3. On change, edits the in-memory provider registry so the new set of
     Nexus-tier model ids is exactly what the gateway will accept.
  4. Registers ``nexus`` (chat) and ``nexus-vision`` (vision/OCR) models
     when the status API lists them; auto-assigns ``nexus-vision`` as the
     vision role if no vision model is configured.
  5. Emits a ``nexus_tier_changed`` event onto the synthetic
     ``__nexus__`` session channel; the UI subscribes to the cross-session
     stream and uses the event to refresh gauges + show a toast.

The cached payload is also kept on the instance so :py:func:`status` (the
HTTP route) can return it cheaply without making an outbound call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import nexus_account

log = logging.getLogger(__name__)

_NEXUS_CHANNEL = "__nexus__"
_MIN_POLL_SECONDS = 60
_DEFAULT_POLL_SECONDS = 3600


class StatusWatcher:
    """Single-instance watcher tied to the FastAPI lifespan.

    Holds soft references back to the runtime objects (mutable cfg,
    provider registry, sessions, agent) so its tick can re-register
    models without rebuilding the whole app.
    """

    def __init__(
        self,
        *,
        mutable_state: dict[str, Any],
        agent: Any,
        sessions: Any,
        rebuild_registry,
        save_config,
    ) -> None:
        self._mutable_state = mutable_state
        self._agent = agent
        self._sessions = sessions
        self._rebuild_registry = rebuild_registry
        self._save_config = save_config
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_status: dict[str, Any] | None = None
        self._last_models: tuple[str, ...] = ()
        self._last_features: frozenset[str] = frozenset()

    @property
    def last_status(self) -> dict[str, Any] | None:
        return self._last_status

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="nexus-status-watcher")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    async def tick_once(self) -> dict[str, Any] | None:
        """Run a single poll on demand (used by /auth/nexus/refresh).

        Returns the freshly-fetched status payload, or None if the user
        isn't signed in. Re-raises ``NexusAccountError`` so callers can
        translate to HTTP error responses.
        """
        if not nexus_account.is_signed_in():
            self._last_status = None
            self._last_models = ()
            self._last_features = frozenset()
            return None
        cfg = self._cfg()
        base_url = cfg.nexus_account.base_url
        payload = await nexus_account.refresh_status(base_url=base_url)
        self._apply_status(payload)
        return payload

    async def _run(self) -> None:
        """Main polling loop. Errors are logged and the loop continues."""
        # Tiny initial delay so we don't race with config rebuilds at boot.
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=2.0)
            return
        except asyncio.TimeoutError:
            pass

        while not self._stop_event.is_set():
            cfg = self._cfg()
            poll_seconds = max(
                _MIN_POLL_SECONDS,
                int(getattr(cfg.nexus_account, "poll_seconds", _DEFAULT_POLL_SECONDS)),
            )

            if nexus_account.is_signed_in():
                try:
                    base_url = cfg.nexus_account.base_url
                    payload = await nexus_account.refresh_status(base_url=base_url)
                    self._apply_status(payload)
                except nexus_account.NexusAccountError as exc:
                    log.warning("[nexus_watcher] status fetch failed: %s", exc)
                except Exception:
                    log.exception("[nexus_watcher] unexpected status fetch error")
            else:
                # Not signed in — clear any stale cache so the route reports it.
                self._last_status = None
                self._last_models = ()
                self._last_features = frozenset()

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                pass

    # ── internal helpers ───────────────────────────────────────────────

    def _cfg(self) -> Any:
        cfg = self._mutable_state.get("cfg")
        if cfg is None:
            # Should never happen — main.py wires cfg before start.
            raise RuntimeError("nexus status_watcher: cfg not bound")
        return cfg

    def _apply_status(self, payload: dict[str, Any]) -> None:
        new_models: tuple[str, ...] = tuple(payload.get("models") or [])
        new_features: frozenset[str] = frozenset(payload.get("features") or [])
        prev_models = self._last_models
        prev_features = self._last_features
        self._last_status = payload
        self._last_models = new_models
        self._last_features = new_features

        log.info(
            "[nexus_watcher] status applied: tier=%s models=%s features=%s",
            payload.get("tier"), list(new_models), sorted(new_features),
        )

        if new_features != prev_features:
            from ..features import set_features
            set_features(set(new_features))

            if self._sessions is not None:
                try:
                    from ..server.events import SessionEvent

                    self._sessions.publish(
                        _NEXUS_CHANNEL,
                        SessionEvent(
                            kind="features_changed",
                            data={
                                "from": sorted(prev_features),
                                "to": sorted(new_features),
                            },
                        ),
                    )
                except Exception:
                    log.exception("[nexus_watcher] features_changed publish failed")

        if new_models == prev_models:
            # Models unchanged — still force nexus-vision as the vision
            # role whenever it is available.
            if "nexus-vision" in new_models:
                cfg = self._cfg()
                if cfg.agent.vision_model != "nexus-vision":
                    cfg.agent.vision_model = "nexus-vision"
                    try:
                        self._save_config(cfg)
                    except Exception:
                        log.exception("[nexus_watcher] save_config failed")
                    log.info("[nexus_watcher] auto-assigned vision role to nexus-vision")
            return

        cfg = self._cfg()
        prev_default = cfg.agent.default_model

        models_changed = self._reconcile_models(cfg, new_models)
        new_default = self._select_default(prev_default, new_models, cfg)
        default_changed = new_default != prev_default

        if default_changed:
            cfg.agent.default_model = new_default

        if default_changed or models_changed:
            try:
                self._save_config(cfg)
            except Exception:
                log.exception("[nexus_watcher] save_config failed")

        # Always rebuild the provider registry so model availability
        # reflects the new tier — even if the default didn't change
        # (e.g. user already on a non-Nexus model).
        try:
            self._rebuild_registry(cfg, self._mutable_state, self._agent)
        except Exception:
            log.exception("[nexus_watcher] registry rebuild failed")

        log.info(
            "[nexus_watcher] models changed %s -> %s; default %s -> %s",
            list(prev_models), list(new_models), prev_default, new_default,
        )

        if self._sessions is not None:
            try:
                from ..server.events import SessionEvent

                self._sessions.publish(
                    _NEXUS_CHANNEL,
                    SessionEvent(
                        kind="nexus_tier_changed",
                        data={
                            "from_models": list(prev_models),
                            "to_models": list(new_models),
                            "default_model_from": prev_default,
                            "default_model_to": new_default,
                            "tier": payload.get("tier"),
                        },
                    ),
                )
            except Exception:
                log.exception("[nexus_watcher] SSE publish failed")

    def _reconcile_models(self, cfg: Any, available: tuple[str, ...]) -> bool:
        """Reconcile Nexus provider models against the status API response.

        Driven by the ``/api/status.models`` array:

          * ``"nexus"`` in available → register the ``nexus`` chat model
          * ``"nexus-vision"`` in available → register the ``nexus-vision``
            vision model and auto-assign it as the vision role
          * If neither is listed → drop all Nexus models (key revoked)

        Only model entries belonging to a provider with ``runtime_kind ==
        "nexus"`` are touched; BYO models stay untouched. Returns True
        when the list or vision role changed so the caller persists.
        """
        from ..config_schema import ModelEntry

        nexus_provider_names = {
            name for name, p in cfg.providers.items()
            if getattr(p, "runtime_kind", "") == "nexus"
        }
        if not nexus_provider_names:
            return False

        primary = "nexus" if "nexus" in nexus_provider_names else sorted(
            nexus_provider_names,
        )[0]

        desired: list[ModelEntry] = []
        if "nexus" in available:
            desired.append(
                ModelEntry(
                    id="nexus",
                    provider=primary,
                    model_name="nexus",
                    tier="heavy",
                    tags=["nexus", "hosted", "pro"],
                ),
            )
        if "nexus-vision" in available:
            desired.append(
                ModelEntry(
                    id="nexus-vision",
                    provider=primary,
                    model_name="nexus-vision",
                    tier="balanced",
                    tags=["nexus", "hosted", "vision"],
                ),
            )

        desired_ids = {m.id for m in desired}
        existing_nexus_ids = {
            m.id for m in cfg.models if m.provider in nexus_provider_names
        }

        needs_vision = "nexus-vision" in desired_ids

        if existing_nexus_ids == desired_ids and not needs_vision:
            return False

        if existing_nexus_ids != desired_ids:
            cfg.models = [m for m in cfg.models if m.provider not in nexus_provider_names]
            cfg.models.extend(desired)

        if needs_vision:
            cfg.agent.vision_model = "nexus-vision"

        log.info(
            "[nexus_watcher] reconciled nexus models: %s -> %s",
            sorted(existing_nexus_ids), sorted(desired_ids),
        )
        return True

    def _select_default(
        self,
        prev_default: str,
        new_models: tuple[str, ...],
        cfg: Any,
    ) -> str:
        """Pick the default model after a model change.

        If the previous default was a Nexus model (``nexus``, ``demo``,
        or ``nexus-vision``), switch to ``nexus`` if available. BYO
        defaults are never touched.
        """
        nexus_model_ids = {"nexus", "demo", "nexus-vision"}
        was_on_nexus = prev_default in nexus_model_ids

        if not was_on_nexus and prev_default:
            return prev_default

        if "nexus" in new_models:
            return "nexus"
        return prev_default
