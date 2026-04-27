"""Vault-backed, user-editable GraphRAG ontology store.

The ontology — entity types, core relations, and the bilingual prototype
phrases used by the builtin extractor — lives as plain files under
``~/.nexus/vault/_system/ontology/``:

  entity_types.csv  — columns: type, description, prototypes_en, prototypes_pt
  relations.csv     — columns: relation, description, prototypes_en, prototypes_pt
  INSTRUCTIONS.md   — guidance the agent reads before proposing edits
  meta.json         — ``{"allow_custom_relations": bool, "version": int}``

The store seeds these files on first use from the static defaults shipped
with Nexus (``GraphRAGOntologyConfig`` + ``builtin_extractor._constants``).
After that, both the user and the agent (via ``ontology_manage``) own the
files. ``graphrag_manager.initialize`` reads through ``load()`` so config
changes only need a re-init, not a full restart.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ONTOLOGY_VAULT_DIR = "_system/ontology"
ENTITY_TYPES_FILE = "entity_types.csv"
RELATIONS_FILE = "relations.csv"
INSTRUCTIONS_FILE = "INSTRUCTIONS.md"
META_FILE = "meta.json"

_TYPE_HEADERS = ["type", "description", "prototypes_en", "prototypes_pt"]
_REL_HEADERS = ["relation", "description", "prototypes_en", "prototypes_pt"]

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PROTO_SEPARATOR = " | "


@dataclass(frozen=True)
class EntityType:
    type: str
    description: str
    prototypes_en: str
    prototypes_pt: str


@dataclass(frozen=True)
class Relation:
    relation: str
    description: str
    prototypes_en: str
    prototypes_pt: str


@dataclass(frozen=True)
class OntologySnapshot:
    entity_types: list[EntityType]
    relations: list[Relation]
    allow_custom_relations: bool
    instructions: str
    version: int

    def type_names(self) -> list[str]:
        return [t.type for t in self.entity_types]

    def relation_names(self) -> list[str]:
        return [r.relation for r in self.relations]

    def type_prototypes(self) -> dict[str, list[str]]:
        """Bilingual prototype phrases keyed by type name.

        Format matches what ``builtin_extractor._build_prototype_embeddings``
        expects: a list of strings; each string is embedded once and the
        first vector wins. en+pt are joined with ``" | "`` so a single
        embedding anchors both languages.
        """
        return {
            t.type: [_join_proto(t.prototypes_en, t.prototypes_pt)]
            for t in self.entity_types
        }

    def relation_prototypes(self) -> dict[str, list[str]]:
        return {
            r.relation: [_join_proto(r.prototypes_en, r.prototypes_pt)]
            for r in self.relations
        }


class OntologyStore:
    """Read/write the vault ontology folder.

    All writes go through :func:`nexus.vault.write_file` so the GraphRAG
    indexer picks up the change and re-embeds the new content on the next
    indexing pass — and so file moves/atomic writes share the same code
    path as the rest of the vault.
    """

    def __init__(self, vault_root: Path) -> None:
        self._vault_root = vault_root
        self._dir = vault_root / ONTOLOGY_VAULT_DIR

    @property
    def directory(self) -> Path:
        return self._dir

    def exists(self) -> bool:
        return (self._dir / META_FILE).is_file()

    # -- seed -----------------------------------------------------------------

    def seed_if_empty(
        self,
        entity_types: list[str],
        core_relations: list[str],
        allow_custom_relations: bool,
        type_prototypes: dict[str, list[str]] | None = None,
        relation_prototypes: dict[str, list[str]] | None = None,
    ) -> bool:
        """Create the ontology folder + default files if not already there.

        Returns True if seeded just now, False if it already existed.
        Idempotent on partial state — reseeds any missing file but never
        clobbers an existing CSV.
        """
        if self.exists():
            return False
        type_prototypes = type_prototypes or {}
        relation_prototypes = relation_prototypes or {}

        self._dir.mkdir(parents=True, exist_ok=True)

        if not (self._dir / ENTITY_TYPES_FILE).exists():
            type_rows = [
                _split_proto_row("type", t, type_prototypes.get(t, [""])[0])
                for t in entity_types
            ]
            self._write_csv_via_vault(ENTITY_TYPES_FILE, _TYPE_HEADERS, type_rows)

        if not (self._dir / RELATIONS_FILE).exists():
            rel_rows = [
                _split_proto_row("relation", r, relation_prototypes.get(r, [""])[0])
                for r in core_relations
            ]
            self._write_csv_via_vault(RELATIONS_FILE, _REL_HEADERS, rel_rows)

        if not (self._dir / INSTRUCTIONS_FILE).exists():
            self._write_text_via_vault(INSTRUCTIONS_FILE, _DEFAULT_INSTRUCTIONS)

        if not (self._dir / META_FILE).exists():
            meta = {"allow_custom_relations": allow_custom_relations, "version": 1}
            self._write_text_via_vault(
                META_FILE, json.dumps(meta, indent=2) + "\n",
            )

        log.info("[ontology] seeded default ontology to %s", self._dir)
        return True

    # -- read -----------------------------------------------------------------

    def load(self) -> OntologySnapshot:
        if not self.exists():
            raise FileNotFoundError(
                f"ontology not seeded at {self._dir}; call seed_if_empty first",
            )
        meta_raw = (self._dir / META_FILE).read_text(encoding="utf-8")
        try:
            meta = json.loads(meta_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid meta.json: {exc}") from exc

        instructions = (self._dir / INSTRUCTIONS_FILE).read_text(encoding="utf-8")
        type_rows = _read_csv(self._dir / ENTITY_TYPES_FILE, _TYPE_HEADERS)
        rel_rows = _read_csv(self._dir / RELATIONS_FILE, _REL_HEADERS)

        return OntologySnapshot(
            entity_types=[EntityType(**row) for row in type_rows],
            relations=[Relation(**row) for row in rel_rows],
            allow_custom_relations=bool(meta.get("allow_custom_relations", True)),
            instructions=instructions,
            version=int(meta.get("version", 1)),
        )

    # -- entity-type CRUD -----------------------------------------------------

    def add_type(
        self,
        type_name: str,
        description: str = "",
        prototypes_en: str = "",
        prototypes_pt: str = "",
    ) -> None:
        _validate_name(type_name, kind="type")
        snap = self.load()
        if any(t.type == type_name for t in snap.entity_types):
            raise ValueError(f"type {type_name!r} already exists; use update_type")
        rows = _read_csv(self._dir / ENTITY_TYPES_FILE, _TYPE_HEADERS)
        rows.append({
            "type": type_name,
            "description": description,
            "prototypes_en": prototypes_en,
            "prototypes_pt": prototypes_pt,
        })
        self._write_csv_via_vault(ENTITY_TYPES_FILE, _TYPE_HEADERS, rows)

    def update_type(
        self,
        type_name: str,
        *,
        description: str | None = None,
        prototypes_en: str | None = None,
        prototypes_pt: str | None = None,
    ) -> None:
        rows = _read_csv(self._dir / ENTITY_TYPES_FILE, _TYPE_HEADERS)
        for row in rows:
            if row["type"] == type_name:
                if description is not None:
                    row["description"] = description
                if prototypes_en is not None:
                    row["prototypes_en"] = prototypes_en
                if prototypes_pt is not None:
                    row["prototypes_pt"] = prototypes_pt
                self._write_csv_via_vault(ENTITY_TYPES_FILE, _TYPE_HEADERS, rows)
                return
        raise ValueError(f"type {type_name!r} not found")

    def remove_type(self, type_name: str) -> None:
        rows = _read_csv(self._dir / ENTITY_TYPES_FILE, _TYPE_HEADERS)
        kept = [r for r in rows if r["type"] != type_name]
        if len(kept) == len(rows):
            raise ValueError(f"type {type_name!r} not found")
        if not kept:
            raise ValueError("cannot remove the last entity type")
        self._write_csv_via_vault(ENTITY_TYPES_FILE, _TYPE_HEADERS, kept)

    # -- relation CRUD --------------------------------------------------------

    def add_relation(
        self,
        relation: str,
        description: str = "",
        prototypes_en: str = "",
        prototypes_pt: str = "",
    ) -> None:
        _validate_name(relation, kind="relation")
        snap = self.load()
        if any(r.relation == relation for r in snap.relations):
            raise ValueError(f"relation {relation!r} already exists; use update_relation")
        rows = _read_csv(self._dir / RELATIONS_FILE, _REL_HEADERS)
        rows.append({
            "relation": relation,
            "description": description,
            "prototypes_en": prototypes_en,
            "prototypes_pt": prototypes_pt,
        })
        self._write_csv_via_vault(RELATIONS_FILE, _REL_HEADERS, rows)

    def update_relation(
        self,
        relation: str,
        *,
        description: str | None = None,
        prototypes_en: str | None = None,
        prototypes_pt: str | None = None,
    ) -> None:
        rows = _read_csv(self._dir / RELATIONS_FILE, _REL_HEADERS)
        for row in rows:
            if row["relation"] == relation:
                if description is not None:
                    row["description"] = description
                if prototypes_en is not None:
                    row["prototypes_en"] = prototypes_en
                if prototypes_pt is not None:
                    row["prototypes_pt"] = prototypes_pt
                self._write_csv_via_vault(RELATIONS_FILE, _REL_HEADERS, rows)
                return
        raise ValueError(f"relation {relation!r} not found")

    def remove_relation(self, relation: str) -> None:
        rows = _read_csv(self._dir / RELATIONS_FILE, _REL_HEADERS)
        kept = [r for r in rows if r["relation"] != relation]
        if len(kept) == len(rows):
            raise ValueError(f"relation {relation!r} not found")
        if not kept:
            raise ValueError("cannot remove the last relation")
        self._write_csv_via_vault(RELATIONS_FILE, _REL_HEADERS, kept)

    def set_allow_custom_relations(self, allow: bool) -> None:
        meta = {"allow_custom_relations": bool(allow), "version": 1}
        if (self._dir / META_FILE).exists():
            try:
                existing = json.loads((self._dir / META_FILE).read_text(encoding="utf-8"))
                existing["allow_custom_relations"] = bool(allow)
                meta = existing
            except json.JSONDecodeError:
                pass
        self._write_text_via_vault(META_FILE, json.dumps(meta, indent=2) + "\n")

    # -- internal -------------------------------------------------------------

    def _vault_rel(self, filename: str) -> str:
        # nexus.vault uses forward slashes regardless of OS.
        return f"{ONTOLOGY_VAULT_DIR}/{filename}"

    def _write_csv_via_vault(
        self, filename: str, headers: list[str], rows: list[dict[str, str]],
    ) -> None:
        text = _rows_to_csv(headers, rows)
        self._write_text_via_vault(filename, text)

    def _write_text_via_vault(self, filename: str, content: str) -> None:
        # Lazy import keeps the store importable in tests without mounting
        # the full vault module (which spins up indexer state on first use).
        from .. import vault
        vault.write_file(self._vault_rel(filename), content)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_name(name: str, *, kind: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(
            f"invalid {kind} name: {name!r} (expected snake_case, "
            "starting with a lowercase letter, max 64 chars)",
        )


def _join_proto(en: str, pt: str) -> str:
    en = (en or "").strip()
    pt = (pt or "").strip()
    if en and pt:
        return f"{en}{_PROTO_SEPARATOR}{pt}"
    return en or pt


def _split_proto(combined: str) -> tuple[str, str]:
    if _PROTO_SEPARATOR in combined:
        en, pt = combined.split(_PROTO_SEPARATOR, 1)
        return en.strip(), pt.strip()
    return combined.strip(), ""


def _split_proto_row(name_col: str, name: str, proto: str) -> dict[str, str]:
    en, pt = _split_proto(proto)
    return {name_col: name, "description": "", "prototypes_en": en, "prototypes_pt": pt}


def _rows_to_csv(headers: list[str], rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # csv.DictWriter handles quoting for embedded commas / newlines;
        # callers don't need to escape anything themselves.
        writer.writerow({h: row.get(h, "") for h in headers})
    return buf.getvalue()


def _read_csv(path: Path, headers: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, str]] = []
        for raw in reader:
            row = {h: (raw.get(h) or "").strip() for h in headers}
            if not row.get(headers[0]):
                continue  # skip blank lines / header re-emissions
            rows.append(row)
    return rows


_DEFAULT_INSTRUCTIONS = """\
# Ontology — how to edit

This folder is the source of truth for the GraphRAG ontology used by Nexus's
builtin extractor. Both you and the agent can edit it. Changes take effect on
the next GraphRAG initialization (the agent's `ontology_manage` tool triggers
that automatically; manual edits require a server restart or
`uv run nexus graphrag reindex`).

## Files

- **entity_types.csv** — every row defines one entity category the extractor
  may assign to a noun phrase or named entity it finds in the vault.
  Columns:
  - `type` — snake_case name (e.g. `person`, `project`, `decision`).
  - `description` — one-line note for human readers; not used by the model.
  - `prototypes_en` — short English noun phrase listing synonyms / hints
    (e.g. `"person individual human being someone"`). Embedded and used as
    a similarity anchor.
  - `prototypes_pt` — same, but Portuguese tokens. Joined with the English
    side as `"<en> | <pt>"` and embedded once with the multilingual model.

- **relations.csv** — same shape, for predicates connecting two entities
  (e.g. `uses`, `depends_on`, `part_of`). Be conservative — adding too
  many relations dilutes precision.

- **meta.json** — `{"allow_custom_relations": bool, "version": int}`.
  When `allow_custom_relations` is true, the extractor may emit relations
  that aren't in `relations.csv`; when false, anything not in the list is
  rejected and replaced with `related_to`.

## When to add a new type

Add a new entity type only when notes contain a category that the existing
types can't represent without distortion. Symptoms that justify a new type:
- Several entities are getting classified as `concept` (the catch-all) when
  they share a more specific shape (e.g. `meeting`, `customer`).
- You want to query the graph by that shape (e.g. "show all decisions about
  technology X"). If you'd never filter by it, don't add it.

When in doubt, prefer richer prototype phrases on existing types over
inventing new ones.

## When to add a new relation

Add a new relation only if you'd write a vault note like
"X <relation> Y" naturally and want to retrieve that pattern later. The
default `related_to` covers everything else.
"""
