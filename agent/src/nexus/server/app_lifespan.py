from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

log = logging.getLogger(__name__)


def create_lifespan(state: dict[str, Any]):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        graphrag_cfg = state["graphrag_cfg"]
        nexus_cfg = state["nexus_cfg"]
        mutable_state = state["mutable_state"]
        agent = state["agent"]
        sessions = state["sessions"]
        multi_user = state["multi_user"]
        session_registry = state["session_registry"]
        store_proxy = state.get("store_proxy")
        job_tracker = state["job_tracker"]
        publish_job_event = state["publish_job_event"]

        log.info("Lifespan starting (graphrag_cfg=%s)", "present" if graphrag_cfg is not None else "None")

        _nexus_logger = logging.getLogger("nexus")
        if not _nexus_logger.handlers:
            _handler = logging.StreamHandler()
            _handler.setLevel(logging.INFO)
            _nexus_logger.addHandler(_handler)
        _nexus_logger.setLevel(logging.INFO)
        try:
            from . import event_bus
            event_bus.set_loop(asyncio.get_running_loop())
        except Exception:
            log.exception("event_bus setup failed")

        async def _vault_cache_listener() -> None:
            from . import event_bus as _eb
            from ..vault_datatable_index import invalidate_cache as _inv_dt
            q = _eb.subscribe()
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue
                    if ev.get("type") in ("vault.indexed", "vault.removed"):
                        _inv_dt()
                        try:
                            from ..server.routes.workflows import _CACHE as _wf_cache
                            _wf_cache.clear()
                        except Exception:
                            pass
            except asyncio.CancelledError:
                pass
            finally:
                _eb.unsubscribe(q)

        _vault_cache_task = asyncio.create_task(_vault_cache_listener())

        try:
            from ..tunnel import cloudflared_provider
            killed = cloudflared_provider.cleanup_orphans()
            if killed:
                log.info("cleaned up %d orphan cloudflared process(es) on startup", killed)
        except Exception:
            log.exception("cloudflared orphan cleanup failed")

        # Sweep orphaned import temp dirs. vault_import tracks these in an
        # in-memory dict that's lost on restart, so any crashed/abandoned
        # import leaves a full extracted tree under ~/.nexus/tmp/ forever.
        # The import flow recreates subdirs on demand, so removing the whole
        # tmp root is safe.
        try:
            import shutil
            from .. import home as _home
            _tmp_root = _home.root() / "tmp"
            if _tmp_root.exists():
                shutil.rmtree(_tmp_root, ignore_errors=True)
        except Exception:
            log.exception("tmp dir sweep failed")

        skip_local_llm = bool(os.environ.get("NEXUS_SKIP_LOCAL_LLM_RESTART"))

        if not skip_local_llm:
            try:
                from ..local_llm import manager as _local_mgr
                reaped = await asyncio.to_thread(_local_mgr.reap_orphans)
                if reaped:
                    log.info("[local_llm] reaped %d orphan(s): %s", len(reaped), reaped)
            except Exception:
                log.exception("local_llm orphan reap failed")

        if not skip_local_llm:
            try:
                from ..local_llm.binary_update import check_latest
                await asyncio.to_thread(check_latest)
            except Exception:
                pass

        if not skip_local_llm:
            try:
                from ..local_llm import manager as _local_mgr

                def _restart_blocking() -> int:
                    return _local_mgr.restart_local_models()

                started = await asyncio.to_thread(_restart_blocking)
                if started > 0:
                    from ..config_file import load as _load_cfg
                    from .routes.config import _rebuild_registry
                    _rebuild_registry(_load_cfg(), mutable_state, agent)
                    log.info("[local_llm] restarted %d local model(s) at startup", started)
                else:
                    _local_mgr.cleanup_stale_config()
            except Exception:
                log.exception("local_llm restart failed")

        try:
            from ..ocr_server import (
                migrate_from_local_llm,
                cleanup_config_entries,
                download_if_missing,
                start as ocr_start,
            )

            def _ocr_bootstrap() -> None:
                migrated = migrate_from_local_llm()
                if migrated:
                    cleanup_config_entries()
                download_if_missing()
                ocr_start()

            await asyncio.to_thread(_ocr_bootstrap)
        except Exception:
            log.exception("[ocr_server] bootstrap failed")

        try:
            from ..auth.status_watcher import StatusWatcher
            from ..config_file import save as _save_cfg
            from .routes.config import _rebuild_registry as _rebuild_reg

            watcher = StatusWatcher(
                mutable_state=mutable_state,
                agent=agent,
                sessions=sessions,
                rebuild_registry=_rebuild_reg,
                save_config=_save_cfg,
            )
            watcher.start()
            app.state.nexus_status_watcher = watcher
            log.info("[nexus] status watcher started")
        except Exception:
            log.exception("[nexus] status watcher start failed")
            app.state.nexus_status_watcher = None

        try:
            from .events import SessionEvent as _SE
            from datetime import datetime, timezone

            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            re_published = 0

            def _sweep_store(store: Any) -> None:
                nonlocal re_published
                for row in store.list_all_pending():
                    deadline = row.get("deadline_at")
                    if deadline and deadline < now_iso:
                        store.cancel_hitl_pending(
                            row["request_id"], reason="expired",
                        )
                        store.publish(
                            row["session_id"],
                            _SE(
                                kind="user_request_cancelled",
                                data={
                                    "request_id": row["request_id"],
                                    "reason": "expired",
                                },
                            ),
                        )
                        continue
                    ev_data: dict[str, Any] = {
                        "request_id": row["request_id"],
                        "prompt": row["prompt"],
                        "kind": row["kind"],
                        "choices": row.get("choices"),
                        "default": row.get("default"),
                        "timeout_seconds": row.get("timeout_seconds"),
                    }
                    if row.get("kind") == "form":
                        ev_data["fields"] = row.get("fields")
                        ev_data["form_title"] = row.get("form_title")
                        ev_data["form_description"] = row.get("form_description")
                    store.publish(
                        row["session_id"], _SE(kind="user_request", data=ev_data),
                    )
                    re_published += 1
                store.trim_hitl_pending(keep_days=30)
                store.trim_hitl_events()
                cleaned_errs = store.cleanup_old_llm_errors(keep_days=90)
                if cleaned_errs:
                    log.info("cleaned up %d old llm_errors rows (>90d)", cleaned_errs)

            if multi_user and session_registry is not None:
                for _uid, user_store_inst in session_registry.all_stores().items():
                    _sweep_store(user_store_inst)
            else:
                _sweep_store(sessions)
            if re_published:
                log.info(
                    "[hitl] re-published %d parked request(s) at startup",
                    re_published,
                )
        except Exception:
            log.exception("hitl parked sweep failed")

        if graphrag_cfg is not None:
            try:
                from ..agent.graphrag_manager import initialize

                log.info("Initializing GraphRAG engine...")
                await initialize(nexus_cfg)
                log.info("GraphRAG engine initialized")
            except Exception:
                log.exception("GraphRAG initialization failed")

            async def _prefetch_builtin() -> None:
                try:
                    from ..agent.builtin_embedder import get_builtin_embedder
                    await get_builtin_embedder().embed(["__warmup__"])
                    log.info("[startup] builtin embedder cache warm")
                except Exception:
                    log.warning("[startup] builtin embedder prefetch failed", exc_info=True)

            asyncio.create_task(_prefetch_builtin())

            try:
                from ..tts.voice_setup import bootstrap_default_voices
                asyncio.create_task(bootstrap_default_voices())
            except Exception:
                log.warning("[startup] piper voice prefetch failed", exc_info=True)

            def _warm_list_caches() -> None:
                try:
                    from ..vault_datatable_index import warm_cache as _warm_dt
                    _warm_dt()
                except Exception:
                    log.debug("[startup] datatable cache warm failed", exc_info=True)
                try:
                    from ..server.routes.workflows import _CACHE as _wf_cache
                    _wf_cache.get_all()
                except Exception:
                    log.debug("[startup] workflow cache warm failed", exc_info=True)

            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, _warm_list_caches)
            except Exception:
                pass

        scheduler = None
        from ..features import is_enabled as _feat_enabled
        if _feat_enabled("heartbeat"):
            try:
                from .. import vault_calendar
                from ..calendar_runtime import (
                    set_dispatcher as _set_cal_dispatcher,
                    set_notifier as _set_cal_notifier,
                    set_alarm_store as _set_cal_alarm_store,
                )
                from ..heartbeat_drivers import DRIVERS_DIR
                from pathlib import Path
                from loom.heartbeat import (
                    HeartbeatRegistry,
                    HeartbeatScheduler,
                    HeartbeatStore,
                )

                vault_calendar.ensure_default_calendar()
                vault_calendar.sweep_missed(grace_minutes=5)

                from .routes.vault_dispatch import _dispatch_impl as _cal_dispatch

                async def _calendar_dispatcher(
                    *,
                    path: str,
                    event_id: str,
                    mode: str = "background",
                    occurrence_start: str | None = None,
                ) -> dict:
                    import time as _time
                    from .. import vault_calendar as _vc
                    event_title = ""
                    try:
                        _cal = _vc.read_calendar(path)
                        _found = _vc.find_event(_cal, event_id)
                        if _found:
                            event_title = _found[0].title
                    except Exception:
                        pass
                    _log_id = log_store.log_fire(
                        heartbeat_id="calendar_trigger",
                        event_id=event_id,
                        event_title=event_title,
                        calendar_path=path,
                    )
                    _t0 = _time.monotonic()
                    _cal_job_id = job_tracker.start(
                        type="calendar",
                        label=event_title or "Calendar trigger",
                        session_id=None,
                        extra={"event_id": event_id, "calendar_path": path},
                        publish_fn=publish_job_event,
                    )
                    try:
                        result = await _cal_dispatch(
                            path=path, card_id=None, event_id=event_id,
                            mode=mode, a=agent, store=sessions,
                            occurrence_start=occurrence_start,
                        )
                        _sid = result.get("session_id")
                        if _sid:
                            log_store.update_session_id(_log_id, _sid)
                        log_store.update_status(
                            _log_id, status="done",
                            duration_ms=int((_time.monotonic() - _t0) * 1000),
                        )
                        return result
                    except Exception as _dispatch_err:
                        log_store.update_status(
                            _log_id, status="failed",
                            error=str(_dispatch_err),
                            duration_ms=int((_time.monotonic() - _t0) * 1000),
                        )
                        raise
                    finally:
                        job_tracker.done(_cal_job_id, publish_fn=publish_job_event)
                _set_cal_dispatcher(_calendar_dispatcher)

                from .events import SessionEvent

                def _calendar_notifier(payload: dict) -> None:
                    if multi_user and session_registry is not None:
                        for _uid, user_store_inst in session_registry.all_stores().items():
                            user_store_inst.publish(
                                "__calendar__",
                                SessionEvent(kind="calendar_alert", data=payload),
                            )
                    else:
                        sessions.publish(
                            "__calendar__",
                            SessionEvent(kind="calendar_alert", data=payload),
                        )
                _set_cal_notifier(_calendar_notifier)

                heartbeats_dir = Path("~/.nexus/heartbeats").expanduser()
                heartbeats_dir.mkdir(parents=True, exist_ok=True)
                db_path = Path("~/.nexus/heartbeat.db").expanduser()
                registry = HeartbeatRegistry(
                    heartbeats_dir=heartbeats_dir,
                    additional_dirs=[DRIVERS_DIR],
                )
                registry.scan()
                store = HeartbeatStore(db_path)

                from ..heartbeat_log import HeartbeatLogStore
                log_store = HeartbeatLogStore(db_path)

                from ..alarm_store import AlarmStore
                alarm_store = AlarmStore(db_path)
                _set_cal_alarm_store(alarm_store)

                async def _noop_run_fn(instructions: str, messages):  # noqa: ANN001
                    from loom.loop import AgentTurn
                    return AgentTurn(reply="", input_tokens=0, output_tokens=0, tool_calls=0)

                scheduler = HeartbeatScheduler(
                    registry, store, run_fn=_noop_run_fn, tick_interval=60.0,
                )
                scheduler.start()
                app.state.heartbeat_scheduler = scheduler
                app.state.heartbeat_registry = registry
                app.state.heartbeat_store = store
                app.state.heartbeat_log_store = log_store
                app.state.alarm_store = alarm_store

                from loom.heartbeat import HeartbeatManager

                def _hb_manager_getter():
                    return HeartbeatManager(registry=registry, store=store)

                agent._handlers.hb_manager_getter = _hb_manager_getter

                log.info("heartbeat scheduler started (calendar_trigger registered)")
            except Exception:
                log.exception("heartbeat / calendar bootstrap failed")

        if _feat_enabled("workflow"):
            try:
                from ..workflows.store import WorkflowStore
                from ..workflows.engine import WorkflowEngine
                from .. import home as _wf_home

                wf_db = str(_wf_home.workflow_runs_db())
                wf_store = WorkflowStore(wf_db)
                wf_engine = WorkflowEngine(wf_store)
                app.state.workflow_store = wf_store
                app.state.workflow_engine = wf_engine

                reconciled = wf_store.reconcile_stale_runs()
                if reconciled:
                    log.info("reconciled %d stale workflow runs", reconciled)
                cleaned = wf_store.cleanup_old_runs(30)
                if cleaned:
                    log.info("cleaned up %d old workflow runs (>30d)", cleaned)

                if agent is not None:
                    wf_engine._agent = agent
                if sessions is not None:
                    wf_engine._sessions = store_proxy or sessions

                from .routes.workflows import init as _wf_init
                _wf_init(wf_store, wf_engine)

                from ..workflows.triggers.webhook import WebhookTriggerDriver
                from ..workflows.triggers.event import EventTriggerListener, set_engine_ref as _set_evt_ref
                from ..workflows.triggers.fs_watch import FsWatchTriggerDriver, set_engine_ref as _set_fsw_ref
                from ..workflows.triggers.rss import RssTriggerDriver, set_engine_ref as _set_rss_ref

                webhook_driver = WebhookTriggerDriver(wf_store)
                event_listener = EventTriggerListener(wf_store)
                fsw_driver = FsWatchTriggerDriver(wf_store)
                rss_driver = RssTriggerDriver(wf_store)

                _set_evt_ref(wf_engine)
                _set_fsw_ref(wf_engine)
                _set_rss_ref(wf_engine)

                from ..workflows.triggers.event import set_engine_ref as _set_engine_evt
                _set_engine_evt(wf_engine)

                app.state.workflow_webhook_driver = webhook_driver
                app.state.workflow_event_listener = event_listener
                app.state.workflow_fsw_driver = fsw_driver
                app.state.workflow_rss_driver = rss_driver

                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(event_listener.start())
                except Exception:
                    log.exception("workflow event listener start failed")

                log.info("workflow engine initialised")
            except Exception:
                log.exception("workflow engine bootstrap failed")

        mcp_manager = None
        try:
            from ..mcp_lifecycle import build_mcp_manager, start_mcp
            mcp_mgr = build_mcp_manager(nexus_cfg)
            if mcp_mgr is not None:
                tool_reg = getattr(agent._loom, "_tools", None)
                if tool_reg is not None:
                    await start_mcp(mcp_mgr, tool_reg, agent=agent)
                    mcp_manager = mcp_mgr
                    app.state.mcp_manager = mcp_manager
        except Exception:
            log.exception("MCP bootstrap failed")

        try:
            from ..mcp_lifecycle import start_mcp_server
            tool_reg = getattr(agent._loom, "_tools", None)
            if tool_reg is not None:
                start_mcp_server(nexus_cfg, tool_reg)
        except Exception:
            log.exception("MCP server mode failed")

        try:
            from ..broker.client import BrokerClient
            from ..broker.poller import BrokerPoller
            from ..broker.registry import get_registry
            broker_client = BrokerClient()
            if broker_client.available:
                get_registry()
                wf_engine = getattr(app.state, "workflow_engine", None)
                poller = BrokerPoller(
                    broker_client,
                    agent=agent,
                    workflow_engine=wf_engine,
                )
                poller.start()
                app.state.broker_poller = poller
                log.info("broker poller started")
            else:
                log.info("broker poller skipped: no broker_api_key configured")
        except Exception:
            log.exception("broker poller start failed")

        try:
            yield
        finally:
            _vault_cache_task.cancel()
            watcher = getattr(app.state, "nexus_status_watcher", None)
            if watcher is not None:
                try:
                    await watcher.stop()
                except Exception:
                    log.exception("[nexus] status watcher stop failed")
            if scheduler is not None:
                try:
                    scheduler.stop()
                except Exception:
                    log.exception("heartbeat scheduler stop failed")
            try:
                from ..local_llm import manager as _local_mgr
                _local_mgr.stop_all()
            except Exception:
                log.exception("local_llm stop_all failed")
            try:
                from ..ocr_server import stop as _ocr_stop
                _ocr_stop()
            except Exception:
                log.exception("[ocr_server] stop failed")
            from ..agent.memory import close_memory_store
            close_memory_store()
            try:
                from ..dream.engine import close_store as _close_dream_store
                _close_dream_store()
            except Exception:
                log.exception("dream store close failed")
            try:
                wf_store = getattr(app.state, "workflow_store", None)
                if wf_store is not None:
                    wf_store.close()
            except Exception:
                log.exception("workflow store close failed")
            try:
                fsw = getattr(app.state, "workflow_fsw_driver", None)
                if fsw is not None:
                    fsw.stop_all()
            except Exception:
                log.exception("workflow fs_watch stop failed")
            try:
                evt = getattr(app.state, "workflow_event_listener", None)
                if evt is not None:
                    await evt.stop()
            except Exception:
                log.exception("workflow event listener stop failed")
            try:
                rss = getattr(app.state, "workflow_rss_driver", None)
                if rss is not None:
                    await rss.stop_all()
            except Exception:
                log.exception("workflow rss driver stop failed")
            if mcp_manager is not None:
                try:
                    from ..mcp_lifecycle import stop_mcp
                    await stop_mcp(mcp_manager)
                except Exception:
                    log.exception("MCP shutdown failed")
            try:
                broker_poller = getattr(app.state, "broker_poller", None)
                if broker_poller is not None:
                    await broker_poller.stop()
            except Exception:
                log.exception("broker poller stop failed")
            await agent.aclose()

    return lifespan
