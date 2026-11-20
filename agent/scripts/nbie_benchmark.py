"""NBIE A/B token benchmark — DOM-heavy vs text-first perception.

Quantifies the token cost of the two browser-perception styles on
representative sessions, using the app's canonical estimator
(``nexus.agent.loop.overflow.estimate_tokens`` — the same function the
context meter and soft-compact trigger use):

* **A (legacy)** — the pre-NBIE loop: every action returns ``{url, title}``
  and the agent re-reads the full 8k page (plus 80 links) after each click
  to learn what changed; screenshots for verification.
* **B (NBIE, text-first)** — ``read`` once (text + links + fields +
  contentHash), ``find_element`` for targets, actions that carry post-action
  verification (url_changed/state_changed/dialogs) so no re-read is needed,
  ``page_signature`` for cheap change checks, screenshots only as a last
  resort.

Payload shapes mirror the actual tool contracts (old ``_READ_JS`` output vs
the probe JSON), so the numbers describe what the LLM would really ingest.
No browser is launched — this scores serialized result payloads.

Run: ``uv run python scripts/nbie_benchmark.py [--pages N]``
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys

from nexus.agent.loop.overflow import estimate_tokens

# ── synthetic page content ──────────────────────────────────────────────────
#
# A mid-sized article-plus-form page: ~8k chars of prose, 80 links, 6 form
# fields. Deterministic (seeded) so runs are comparable.

random.seed(20261119)

_WORDS = [
    "browser", "page", "content", "token", "perception", "element", "action",
    "verify", "signal", "progressive", "inspection", "budget", "click",
    "selector", "form", "field", "dialog", "toast", "navigate", "search",
    "results", "article", "section", "paragraph", "heading", "link", "anchor",
    "button", "submit", "email", "password", "login", "checkout", "cart",
    "price", "shipping", "total",
]


def _prose(n_chars: int) -> str:
    out: list[str] = []
    total = 0
    while total < n_chars:
        line = " ".join(random.choice(_WORDS) for _ in range(random.randint(8, 14)))
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)[:n_chars]


def _links(n: int) -> list[dict[str, str]]:
    return [
        {
            "t": f"link {i} " + random.choice(_WORDS),
            "h": f"https://example.com/{random.choice(string.ascii_lowercase * 4)[:6]}/{i}",
        }
        for i in range(n)
    ]


PAGE_TEXT = _prose(8000)
LINKS = _links(80)
FIELDS = [
    {"selector": "#email", "tag": "input", "type": "email", "name": "Email", "required": True, "secret": False},
    {"selector": "#pass", "tag": "input", "type": "password", "name": "Password", "required": True, "secret": True},
    {"selector": "#q", "tag": "input", "type": "search", "name": "Search", "required": False, "secret": False},
    {"selector": "#news", "tag": "input", "type": "checkbox", "name": "Newsletter", "required": False, "secret": False},
    {"selector": "#country", "tag": "select", "type": "select", "name": "Country", "required": False, "secret": False},
    {"selector": "#notes", "tag": "textarea", "type": "textarea", "name": "Notes", "required": False, "secret": False},
]

RESULTS_BUTTON = {
    "selector": "button.results-next",
    "tag": "button",
    "role": "button",
    "name": "Next results",
    "text": "Next results",
    "box": {"x": 512, "y": 640, "w": 140, "h": 36},
    "enabled": True,
}


def _legacy_read() -> str:
    """Old ``_READ_JS`` output: full text + raw link array (no hash/complete)."""
    return json.dumps(
        {
            "url": "https://example.com/catalog",
            "title": "Catalog — Example",
            "text": PAGE_TEXT,
            "links": [{"t": l["t"], "h": l["h"]} for l in LINKS],
        },
        ensure_ascii=False,
    )


def _nbie_read() -> str:
    return json.dumps(
        {
            "ok": True,
            "url": "https://example.com/catalog",
            "title": "Catalog — Example",
            "text": PAGE_TEXT,
            "links": LINKS,
            "fields": FIELDS,
            "contentHash": "3f21ac90",
            "complete": False,
            "readyState": "complete",
        },
        ensure_ascii=False,
    )


# ── session transcripts (tool-result payloads the LLM ingests) ─────────────
#
# Same task, two styles: paginate a catalog twice and submit a search form.


def legacy_session() -> list[str]:
    """Legacy loop: read → click → RE-READ → click → RE-READ → screenshot."""
    info = json.dumps({"url": "https://example.com/catalog", "title": "Catalog — Example"})
    return [
        info,  # navigate
        _legacy_read(),  # read (full page + links)
        info,  # click (returns url/title only — agent must re-read to verify)
        _legacy_read(),  # re-read after click #1
        info,  # click
        _legacy_read(),  # re-read after click #2
        json.dumps(  # verification screenshot
            {"url": "https://example.com/catalog", "title": "Catalog — Example", "screenshot": "~/.nexus/browser/main/shot-1.png"}
        ),
    ]


def nbie_session() -> list[str]:
    """NBIE loop: read once → find → click (verified) → click (verified)."""
    return [
        json.dumps({"url": "https://example.com/catalog", "title": "Catalog — Example"}),  # navigate
        _nbie_read(),  # read once (progressive, contentHash + fields)
        json.dumps({"ok": True, "elements": [RESULTS_BUTTON], "count": 1}),  # find_element
        json.dumps(  # click #1 — verification replaces the re-read
            {
                "url": "https://example.com/catalog?page=2",
                "title": "Catalog — Example",
                "url_changed": True,
                "state_changed": True,
                "dialogs": [],
                "page_signature": {"url": "https://example.com/catalog?page=2", "title": "Catalog — Example", "textHash": "91ba77c4", "dialogs": []},
            },
            ensure_ascii=False,
        ),
        json.dumps(  # click #2 — verified
            {
                "url": "https://example.com/catalog?page=3",
                "title": "Catalog — Example",
                "url_changed": True,
                "state_changed": True,
                "dialogs": [],
                "page_signature": {"url": "https://example.com/catalog?page=3", "title": "Catalog — Example", "textHash": "5cc0e2d1", "dialogs": []},
            },
            ensure_ascii=False,
        ),
        json.dumps({"ok": True, "url": "https://example.com/catalog?page=3", "title": "Catalog — Example", "textHash": "5cc0e2d1", "dialogs": []}),  # page_signature confirm
    ]


# ── scoring ─────────────────────────────────────────────────────────────────


def score(payloads: list[str]) -> int:
    """Token estimate for a transcript of tool-result payloads."""
    msgs = [{"role": "tool", "content": p} for p in payloads]
    return estimate_tokens(msgs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=int, default=1, help="repeat the paginate task N times")
    args = ap.parse_args()

    legacy: list[str] = []
    nbie: list[str] = []
    for _ in range(args.pages):
        legacy += legacy_session()
        nbie += nbie_session()

    a = score(legacy)
    b = score(nbie)
    saved = (1 - b / a) * 100 if a else 0.0

    width = max(len(p) for p in ("legacy (DOM-heavy)", "nbie (text-first)"))
    print(f"NBIE A/B token benchmark — {args.pages} paginate task(s)\n")
    print(f"{'style'.ljust(width)}  {'payloads':>8}  {'est. tokens':>11}")
    print(f"{'-' * width}  {'-' * 8}  {'-' * 11}")
    print(f"{'legacy (DOM-heavy)':<{width}}  {len(legacy):>8}  {a:>11,}")
    print(f"{'nbie (text-first)':<{width}}  {len(nbie):>8}  {b:>11,}")
    print(f"\nestimated saving: {saved:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
