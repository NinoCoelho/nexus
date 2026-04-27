"""``ontology_manage`` tool — vault-backed ontology CRUD + propose flow.

The ontology lives at ``~/.nexus/vault/_system/ontology/`` and is the source
of truth for entity types and relations used by GraphRAG. This tool is the
agent's editing surface; manual users can also just edit the CSVs directly
and run ``uv run nexus graphrag reindex``.

Every successful write triggers a GraphRAG re-initialization so the engine
picks up the new ontology without a server restart. The first time anyone
calls ``view``, the store is seeded from the static defaults shipped in
``GraphRAGOntologyConfig`` (lazy seed mirrors how skills bootstrap).

The ``propose_from_documents`` action is collaborative: the agent (after
reading vault docs and reasoning about gaps) supplies a structured proposal
of new types/relations; the tool renders a human-readable diff via
``ask_user`` and only applies the change on confirmation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..agent.llm import ToolSpec

log = logging.getLogger(__name__)

ONTOLOGY_MANAGE_TOOL = ToolSpec(
    name="ontology_manage",
    description=(
        "Inspect or update the GraphRAG ontology stored as plain CSV/MD at "
        "~/.nexus/vault/_system/ontology/. The ontology controls which entity "
        "types and relations the extractor uses to populate the knowledge "
        "graph. Always start with action='view' to read the current state and "
        "INSTRUCTIONS.md before proposing changes — adding too many types "
        "dilutes precision.\n\n"
        "Actions:\n"
        "  view — return the current ontology + INSTRUCTIONS.md.\n"
        "  add_type / update_type / remove_type — manage entity types.\n"
        "  add_relation / update_relation / remove_relation — manage relations.\n"
        "  set_allow_custom_relations — toggle whether the extractor may emit "
        "relations outside the configured list (default: true).\n"
        "  propose_from_documents — submit a structured proposal of new types "
        "and relations after analyzing vault documents; the tool shows the user "
        "a diff via ask_user and only applies on approval.\n\n"
        "Every successful write triggers GraphRAG re-init automatically; "
        "existing extractions keep their old type labels but new content uses "
        "the updated ontology. For a full re-extraction across the vault, the "
        "user should run `uv run nexus graphrag reindex`."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "view",
                    "add_type", "update_type", "remove_type",
                    "add_relation", "update_relation", "remove_relation",
                    "set_allow_custom_relations",
                    "propose_from_documents",
                ],
            },
            "name": {
                "type": "string",
                "description": (
                    "snake_case identifier of the entity type or relation "
                    "for add_/update_/remove_ actions."
                ),
            },
            "description": {
                "type": "string",
                "description": "Human-readable note for the row (optional).",
            },
            "prototypes_en": {
                "type": "string",
                "description": (
                    "Short English noun phrase listing synonyms/hints used as "
                    "an embedding anchor (e.g. 'meeting gathering session "
                    "discussion conversation'). Used by add_/update_."
                ),
            },
            "prototypes_pt": {
                "type": "string",
                "description": (
                    "Same as prototypes_en, in Portuguese — concatenated with "
                    "the English side as '<en> | <pt>' before embedding."
                ),
            },
            "allow_custom_relations": {
                "type": "boolean",
                "description": "Boolean flag for set_allow_custom_relations.",
            },
            "proposal": {
                "type": "object",
                "description": (
                    "For propose_from_documents only. Shape: {entity_types: "
                    "[{type, description, prototypes_en, prototypes_pt}, ...], "
                    "relations: [{relation, description, prototypes_en, "
                    "prototypes_pt}, ...], rationale: '...one-paragraph summary "
                    "of WHY these additions help'}."
                ),
            },
        },
        "required": ["action"],
    },
)


def make_ontology_handler(
    *,
    ask_user: Callable[[dict], Awaitable[Any]] | None,
    cfg_loader: Callable[[], Any],
    vault_root: Path | None = None,
) -> Callable[[dict], Awaitable[str]]:
    """Build the async handler the registry will register.

    ``ask_user`` is the same callable the ``ask_user`` tool wraps — only
    needed for ``propose_from_documents`` (and only when actually called
    from a chat session). ``cfg_loader`` is invoked after every successful
    write so the GraphRAG engine can be re-initialized with the fresh
    ontology without restarting the server.
    """

    async def _handle(args: dict) -> str:
        action = args.get("action", "")
        store = _make_store(vault_root)

        try:
            if action == "view":
                return _view(store, cfg_loader)

            if action == "add_type":
                store.add_type(
                    type_name=args.get("name", ""),
                    description=args.get("description", ""),
                    prototypes_en=args.get("prototypes_en", ""),
                    prototypes_pt=args.get("prototypes_pt", ""),
                )
                await _trigger_reinit(cfg_loader)
                return _ok(f"added entity type {args.get('name')!r}")

            if action == "update_type":
                store.update_type(
                    type_name=args.get("name", ""),
                    description=args.get("description"),
                    prototypes_en=args.get("prototypes_en"),
                    prototypes_pt=args.get("prototypes_pt"),
                )
                await _trigger_reinit(cfg_loader)
                return _ok(f"updated entity type {args.get('name')!r}")

            if action == "remove_type":
                store.remove_type(type_name=args.get("name", ""))
                await _trigger_reinit(cfg_loader)
                return _ok(f"removed entity type {args.get('name')!r}")

            if action == "add_relation":
                store.add_relation(
                    relation=args.get("name", ""),
                    description=args.get("description", ""),
                    prototypes_en=args.get("prototypes_en", ""),
                    prototypes_pt=args.get("prototypes_pt", ""),
                )
                await _trigger_reinit(cfg_loader)
                return _ok(f"added relation {args.get('name')!r}")

            if action == "update_relation":
                store.update_relation(
                    relation=args.get("name", ""),
                    description=args.get("description"),
                    prototypes_en=args.get("prototypes_en"),
                    prototypes_pt=args.get("prototypes_pt"),
                )
                await _trigger_reinit(cfg_loader)
                return _ok(f"updated relation {args.get('name')!r}")

            if action == "remove_relation":
                store.remove_relation(relation=args.get("name", ""))
                await _trigger_reinit(cfg_loader)
                return _ok(f"removed relation {args.get('name')!r}")

            if action == "set_allow_custom_relations":
                allow = args.get("allow_custom_relations")
                if not isinstance(allow, bool):
                    return _err("`allow_custom_relations` must be a boolean")
                store.set_allow_custom_relations(allow)
                await _trigger_reinit(cfg_loader)
                return _ok(f"allow_custom_relations set to {allow}")

            if action == "propose_from_documents":
                return await _propose(args, store, ask_user, cfg_loader)

            return _err(f"unknown action: {action!r}")

        except FileNotFoundError as exc:
            # Lazy seed if anyone hits the store before initialize() ran.
            if "not seeded" in str(exc):
                _bootstrap(store, cfg_loader)
                return _err("ontology was not seeded — seeded now, please retry")
            return _err(str(exc))
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:
            log.exception("[ontology] tool failed: %s", action)
            return _err(f"internal error: {exc}")

    return _handle


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


def _view(store: Any, cfg_loader: Callable[[], Any]) -> str:
    if not store.exists():
        _bootstrap(store, cfg_loader)
    snap = store.load()
    return json.dumps(
        {
            "ok": True,
            "directory": str(store.directory),
            "version": snap.version,
            "allow_custom_relations": snap.allow_custom_relations,
            "entity_types": [
                {
                    "type": t.type,
                    "description": t.description,
                    "prototypes_en": t.prototypes_en,
                    "prototypes_pt": t.prototypes_pt,
                }
                for t in snap.entity_types
            ],
            "relations": [
                {
                    "relation": r.relation,
                    "description": r.description,
                    "prototypes_en": r.prototypes_en,
                    "prototypes_pt": r.prototypes_pt,
                }
                for r in snap.relations
            ],
            "instructions": snap.instructions,
        },
        ensure_ascii=False,
    )


async def _propose(
    args: dict,
    store: Any,
    ask_user: Callable[[dict], Awaitable[Any]] | None,
    cfg_loader: Callable[[], Any],
) -> str:
    proposal = args.get("proposal") or {}
    new_types = [_clean(t) for t in (proposal.get("entity_types") or []) if isinstance(t, dict)]
    new_rels = [_clean(r) for r in (proposal.get("relations") or []) if isinstance(r, dict)]
    rationale = (proposal.get("rationale") or "").strip()

    if not new_types and not new_rels:
        return _err("proposal is empty — provide at least one entity_type or relation")

    if ask_user is None:
        return _err(
            "ask_user is unavailable — propose_from_documents needs an active "
            "chat session to confirm with the user. Call from a /chat turn.",
        )

    if not store.exists():
        _bootstrap(store, cfg_loader)
    snap = store.load()
    existing_types = {t.type for t in snap.entity_types}
    existing_rels = {r.relation for r in snap.relations}

    # Filter out duplicates and surface them in the diff so the user sees
    # nothing was silently merged in.
    fresh_types = [t for t in new_types if t.get("type") and t["type"] not in existing_types]
    fresh_rels = [r for r in new_rels if r.get("relation") and r["relation"] not in existing_rels]
    skipped_types = [t["type"] for t in new_types if t.get("type") in existing_types]
    skipped_rels = [r["relation"] for r in new_rels if r.get("relation") in existing_rels]

    if not fresh_types and not fresh_rels:
        return _err(
            "all proposed types/relations already exist; nothing to apply "
            f"(skipped: types={skipped_types}, relations={skipped_rels})",
        )

    summary = _format_proposal(fresh_types, fresh_rels, skipped_types, skipped_rels, rationale)
    result = await ask_user({
        "prompt": summary,
        "kind": "confirm",
        "default": "no",
    })
    answer = getattr(result, "answer", None) if not isinstance(result, dict) else result.get("answer")
    answer_str = (answer or "").strip().lower() if isinstance(answer, str) else ""
    timed_out = (
        getattr(result, "timed_out", False)
        if not isinstance(result, dict)
        else result.get("timed_out", False)
    )
    if timed_out:
        return _err("user did not respond in time; proposal cancelled")
    if answer_str not in {"yes", "y", "ok", "approve", "confirm", "true", "sim"}:
        return _err(f"user declined ({answer_str!r}); no changes made")

    applied_types: list[str] = []
    applied_rels: list[str] = []
    for t in fresh_types:
        try:
            store.add_type(
                type_name=t.get("type", ""),
                description=t.get("description", ""),
                prototypes_en=t.get("prototypes_en", ""),
                prototypes_pt=t.get("prototypes_pt", ""),
            )
            applied_types.append(t["type"])
        except ValueError as exc:
            log.warning("[ontology] skipped type %r: %s", t.get("type"), exc)
    for r in fresh_rels:
        try:
            store.add_relation(
                relation=r.get("relation", ""),
                description=r.get("description", ""),
                prototypes_en=r.get("prototypes_en", ""),
                prototypes_pt=r.get("prototypes_pt", ""),
            )
            applied_rels.append(r["relation"])
        except ValueError as exc:
            log.warning("[ontology] skipped relation %r: %s", r.get("relation"), exc)

    if applied_types or applied_rels:
        await _trigger_reinit(cfg_loader)

    return json.dumps({
        "ok": True,
        "message": "proposal applied",
        "applied_entity_types": applied_types,
        "applied_relations": applied_rels,
        "skipped_entity_types": skipped_types,
        "skipped_relations": skipped_rels,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(vault_root: Path | None) -> Any:
    from ..agent.ontology_store import OntologyStore
    root = vault_root or (Path.home() / ".nexus" / "vault")
    return OntologyStore(root)


def _bootstrap(store: Any, cfg_loader: Callable[[], Any]) -> None:
    """Seed defaults from the current config if the store is missing."""
    from ..agent.builtin_extractor import RELATION_PROTOTYPES, TYPE_PROTOTYPES

    cfg = cfg_loader()
    cfg_ont = cfg.graphrag.ontology
    store.seed_if_empty(
        entity_types=cfg_ont.entity_types,
        core_relations=cfg_ont.core_relations,
        allow_custom_relations=cfg_ont.allow_custom_relations,
        type_prototypes=TYPE_PROTOTYPES,
        relation_prototypes=RELATION_PROTOTYPES,
    )


async def _trigger_reinit(cfg_loader: Callable[[], Any]) -> None:
    from ..agent import graphrag_manager
    try:
        cfg = cfg_loader()
        await graphrag_manager.initialize(cfg)
    except Exception as exc:
        log.warning("[ontology] re-init after change failed: %s", exc, exc_info=True)


def _format_proposal(
    new_types: list[dict],
    new_rels: list[dict],
    skipped_types: list[str],
    skipped_rels: list[str],
    rationale: str,
) -> str:
    lines: list[str] = ["**Ontology proposal — confirm to apply**", ""]
    if rationale:
        lines += [f"_Rationale:_ {rationale}", ""]
    if new_types:
        lines.append("**New entity types:**")
        for t in new_types:
            lines.append(
                f"- `{t.get('type', '')}` — {t.get('description', '') or '(no description)'}"
            )
            if t.get("prototypes_en"):
                lines.append(f"    en: {t['prototypes_en']}")
            if t.get("prototypes_pt"):
                lines.append(f"    pt: {t['prototypes_pt']}")
        lines.append("")
    if new_rels:
        lines.append("**New relations:**")
        for r in new_rels:
            lines.append(
                f"- `{r.get('relation', '')}` — {r.get('description', '') or '(no description)'}"
            )
            if r.get("prototypes_en"):
                lines.append(f"    en: {r['prototypes_en']}")
            if r.get("prototypes_pt"):
                lines.append(f"    pt: {r['prototypes_pt']}")
        lines.append("")
    if skipped_types or skipped_rels:
        lines.append(
            f"_Skipping (already present): types={skipped_types}, relations={skipped_rels}_",
        )
        lines.append("")
    lines.append("Apply these additions? Reply 'yes' to confirm.")
    return "\n".join(lines)


def _clean(row: dict) -> dict:
    return {k: (v if isinstance(v, str) else "") for k, v in row.items()}


def _ok(message: str) -> str:
    return json.dumps({"ok": True, "message": message}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)
