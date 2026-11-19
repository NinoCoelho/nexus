"""Calibrate team-router thresholds from real archived chat data.

Extracts labelled pairs (user message -> bot that actually answered) from
~/.nexus/sessions.sqlite project chats, embeds them with the real fastembed
model, simulates the router over the full bot roster, and grid-searches
THRESHOLD x MARGIN to maximise correct routing while minimising wrong-bot
routing (which is worse than falling back to the lead).

Outputs:
- Recommended [agent].team_router_threshold / _margin values
- A curated fixtures file (tests/fixtures/team_router_labels.json) for the
  offline unit test
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import sys
from pathlib import Path

DB = "file:/Users/nino/.nexus/sessions.sqlite?mode=ro"
OUT_FIXTURES = Path(__file__).parent / "fixtures" / "team_router_labels.json"

# Messages shorter than this are noise ("continuar", "ok", slash cmds)
MIN_LEN = 25
# Per-bot sample cap for the eval
PER_BOT_CAP = 60


def load_labelled() -> list[dict]:
    conn = sqlite3.connect(DB, uri=True)
    conn.row_factory = sqlite3.Row

    bots = {
        r["id"]: {"name": r["name"], "handle": r["handle"], "persona": r["persona"] or ""}
        for r in conn.execute("SELECT id, name, handle, persona FROM projects")
    }
    print(f"bots: {len(bots)}")

    # Visible project chats only (hidden = synthetic workflow sessions).
    # 'main' is the coordinator's internal id — exclude it (not a project bot).
    rows = conn.execute(
        """
        SELECT m.session_id, m.role, m.content, s.project_id, s.archived
        FROM messages m JOIN sessions s ON s.id = m.session_id
        WHERE s.project_id IS NOT NULL AND s.project_id != 'main' AND s.hidden = 0
        ORDER BY m.session_id, m.seq
        """
    ).fetchall()
    conn.close()

    # Keep USER messages; drop mentions/slash/short; label = chat owner.
    per_bot: dict[str, list[str]] = {}
    skipped_mention = 0
    skipped_short = 0
    for r in rows:
        if r["role"] != "user":
            continue
        content = r["content"]
        if not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if text.startswith(("<!--", "/", "From ")):
            continue
        if "@" in text:  # mention/emails — conservative: drop all
            skipped_mention += 1
            continue
        if len(text) < MIN_LEN:
            skipped_short += 1
            continue
        per_bot.setdefault(r["project_id"], []).append(text)

    labelled: list[dict] = []
    for pid, msgs in per_bot.items():
        random.Random(42).shuffle(msgs)
        for text in msgs[:PER_BOT_CAP]:
            labelled.append({"bot_id": pid, "message": text})
    print(f"labelled pairs: {len(labelled)} (skipped mentions={skipped_mention}, short={skipped_short})")
    for pid in per_bot:
        b = bots.get(pid, {})
        n = sum(1 for l in labelled if l["bot_id"] == pid)
        print(f"  @{b.get('handle', '?'):<18} {n} samples")
    return labelled, bots


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def main() -> None:
    labelled, bots = load_labelled()

    from nexus.agent.builtin_embedder import get_builtin_embedder

    emb = get_builtin_embedder()
    print(f"embedder: {emb.model}")

    # Persona text exactly as team_router builds it. Compare variants:
    # A) full persona (current router behaviour), B) description only,
    # C) description + first 400 chars of persona (router improvement).
    def persona_text(pid: str) -> str:
        b = bots[pid]
        name = b["name"] or b["handle"]
        handle = b["handle"]
        persona = b["persona"]
        return f"{name} (@{handle}) — {persona}".strip() or name or handle

    def desc_text(pid: str) -> str:
        b = bots[pid]
        return f"{b['name']} (@{b['handle']}) — {b.get('description', '')}".strip()

    def desc_plus_text(pid: str) -> str:
        b = bots[pid]
        head = (b.get("persona") or "")[:400]
        return f"{b['name']} (@{b['handle']}) — {b.get('description', '')} {head}".strip()

    pids = list(bots.keys())
    variants = {"persona": persona_text, "description": desc_text, "desc+400": desc_plus_text}
    pvecs_by_variant: dict[str, dict[str, list[float]]] = {}
    for vname, fn in variants.items():
        print(f"embedding personas ({vname})…")
        pvecs_by_variant[vname] = {
            pid: v for pid, v in zip(pids, emb.embed_sync([fn(p) for p in pids]))
        }

    print(f"embedding {len(labelled)} messages…")
    mvecs = emb.embed_sync([l["message"] for l in labelled])

    bots_desc = None
    try:
        conn = sqlite3.connect(DB, uri=True)
        conn.row_factory = sqlite3.Row
        bots_desc = {
            r["id"]: (r["description"] or "")
            for r in conn.execute("SELECT id, description FROM projects")
        }
        conn.close()
        for b_id, d in bots_desc.items():
            if b_id in bots:
                bots[b_id]["description"] = d
    except Exception:
        pass

    # Score each labelled message against the full roster, per variant.
    results_by_variant: dict[str, list] = {}
    for vname, pvecs in pvecs_by_variant.items():
        results = []  # (bot_id, top1_id, top1, top2, margin)
        for l, mv in zip(labelled, mvecs):
            scores = sorted(
                ((cosine(mv, pvecs[p]), p) for p in pids), key=lambda t: t[0], reverse=True
            )
            top1, p1 = scores[0]
            top2, p2 = scores[1] if len(scores) > 1 else (0.0, "")
            results.append(
                {
                    "bot_id": l["bot_id"],
                    "message": l["message"],
                    "top1": round(top1, 4),
                    "top2": round(top2, 4),
                    "margin": round(top1 - top2, 4),
                    "top1_is_owner": p1 == l["bot_id"],
                    "top2_is_owner": p2 == l["bot_id"],
                }
            )
        results_by_variant[vname] = results

    for vname, results in results_by_variant.items():
        n = len(results)
        top1_correct = sum(1 for r in results if r["top1_is_owner"])
        print(f"\n=== [{vname}] raw embedder quality (threshold-free) ===")
        print(f"  top-1 == owner: {top1_correct}/{n} ({top1_correct / n:.1%})")

    # Grid search per variant: prefer correct routing; penalise WRONG routing hard.
    for vname, results in results_by_variant.items():
        n = len(results)
        best = None
        rows_out = []
        for T in [0.26, 0.30, 0.34, 0.38, 0.42, 0.46, 0.50]:
            for M in [0.03, 0.04, 0.06, 0.08, 0.10, 0.14, 0.18]:
                routed_ok = 0
                wrong = 0
                several_ok = 0
                lead = 0
                for r in results:
                    if r["top1"] >= T and r["margin"] >= M:
                        if r["top1_is_owner"]:
                            routed_ok += 1
                        else:
                            wrong += 1
                    elif r["top1"] >= T and r["top2"] >= T and r["margin"] < M:
                        if r["top1_is_owner"] or r["top2_is_owner"]:
                            several_ok += 1
                        else:
                            wrong += 1
                    else:
                        lead += 1
                score = routed_ok + several_ok * 0.8 + lead * 0.5 - wrong * 3.0
                rows_out.append((T, M, routed_ok, several_ok, wrong, lead, score))
                if best is None or score > best[-1]:
                    best = (T, M, routed_ok, several_ok, wrong, lead, score)

        rows_out.sort(key=lambda t: -t[-1])
        print(f"\n=== [{vname}] grid search ===")
        print(f"{'T':>5} {'M':>5} {'ok':>5} {'sev':>5} {'WRONG':>6} {'lead':>5} {'score':>8}")
        for T, M, ok, sev, wrong, lead, score in rows_out[:10]:
            print(f"{T:>5} {M:>5} {ok:>5} {sev:>5} {wrong:>6} {lead:>5} {score:>8.1f}")
        T, M, ok, sev, wrong, lead, score = best
        print(
            f"  BEST [{vname}]: threshold={T} margin={M} — "
            f"{ok} correct single, {sev} correct several, {lead} lead, {wrong} WRONG ({wrong / n:.1%})"
        )

    results = results_by_variant["persona"]  # fixtures from current behaviour

    # Fixtures file for the calibration unit test: full roster + labelled
    # pairs, so the test can rebuild the scenario offline (skip when the
    # embedder model isn't already cached).
    random.Random(7).shuffle(results)
    fixtures = {
        "source": "real project-chat user messages (~/.nexus/sessions.sqlite)",
        "roster": [
            {
                "handle": bots[pid]["handle"],
                "name": bots[pid]["name"],
                "persona": bots[pid]["persona"],
            }
            for pid in pids
        ],
        "pairs": [],
    }
    seen: dict[str, int] = {}
    for r in results:
        seen[r["bot_id"]] = seen.get(r["bot_id"], 0) + 1
        if seen[r["bot_id"]] > 12:
            continue
        fixtures["pairs"].append(
            {
                "message": r["message"][:280],
                "expected_handle": bots[r["bot_id"]]["handle"],
            }
        )
    OUT_FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    OUT_FIXTURES.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2))
    print(f"\nfixtures written: {OUT_FIXTURES} ({len(fixtures['pairs'])} labelled pairs, roster of {len(fixtures['roster'])})")


if __name__ == "__main__":
    sys.exit(main())
