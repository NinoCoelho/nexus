"""Delete malformed entities (and cascade their triples) from the GraphRAG DB.

Same predicate as ``loom.store.graphrag._engine._sanitize_entity_name``: any
entity whose ``name`` is multi-line, contains markdown table/link/URL/code
fragments, contains mermaid field tokens, has no letters, or is too long /
too short. Foreign keys are enabled so triples and entity_mentions cascade.

Usage:
    uv run python scripts/cleanup_graphrag_junk.py            # dry run
    uv run python scripts/cleanup_graphrag_junk.py --apply    # actually delete
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "graphrag" / "graphrag_entities.sqlite"

JUNK_PREDICATE = """
       instr(name, char(10)) > 0
    OR instr(name, char(13)) > 0
    OR instr(name, char(9))  > 0
    OR instr(name, '|') > 0
    OR instr(name, '](') > 0
    OR instr(name, '://') > 0
    OR instr(name, '```') > 0
    OR length(name) > 80
    OR length(name) < 2
    OR name NOT GLOB '*[A-Za-zÀ-ÿ]*'
    OR name GLOB '*[[:<:]]PK[[:>:]]*'
    OR name GLOB '*[[:<:]]FK[[:>:]]*'
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="commit deletes")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args()

    if not args.db.exists():
        print(f"DB not found: {args.db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA foreign_keys = ON")

    bad = con.execute(f"SELECT COUNT(*) FROM entities WHERE {JUNK_PREDICATE}").fetchone()[0]
    triples_cascade = con.execute(
        f"SELECT COUNT(*) FROM triples WHERE head_id IN "
        f"(SELECT id FROM entities WHERE {JUNK_PREDICATE}) "
        f"OR tail_id IN (SELECT id FROM entities WHERE {JUNK_PREDICATE})"
    ).fetchone()[0]
    total_entities = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    total_triples = con.execute("SELECT COUNT(*) FROM triples").fetchone()[0]

    print(f"DB: {args.db}")
    print(f"  entities: {total_entities}  (junk: {bad})")
    print(f"  triples:  {total_triples}  (cascading: {triples_cascade})")

    if not args.apply:
        print("\nDry run. Re-run with --apply to delete.")
        # Show a few samples
        rows = con.execute(
            f"SELECT id, type, name FROM entities WHERE {JUNK_PREDICATE} LIMIT 8"
        ).fetchall()
        if rows:
            print("\nSample entries that would be removed:")
            for eid, etype, name in rows:
                preview = name.replace("\n", "\\n").replace("\t", "\\t")[:80]
                print(f"  id={eid:6d}  {etype:12s}  {preview!r}")
        return 0

    con.execute(f"DELETE FROM entities WHERE {JUNK_PREDICATE}")
    con.commit()

    new_entities = con.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    new_triples = con.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
    print(f"\nDeleted. Now: entities={new_entities}, triples={new_triples}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
