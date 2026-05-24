"""Skill discovery — find candidate skills in external repos and classify them.

Discovery turns a curated list of source repos (``external_sources.json``) into
ranked, abstract candidate descriptions a non-technical user can pick from.

Pipeline per source:
1. List all ``**/SKILL.md`` paths in the repo (GitHub trees API for ``kind=github``).
2. Fetch each ``SKILL.md`` body (markdown only — never executable assets).
3. Run a one-shot LLM classifier on the body to extract a structured summary.
4. Cache the classified candidate to ``<cache_dir>/<source_slug>/<skill_name>.json``.

``discover(user_ask, language)`` then loads cached candidates from every source
and ranks them with a cheap keyword-overlap score against the user's request.
The wizard's UI never sees raw markdown — only the classified attributes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from ..agent.llm.types import ChatMessage, LLMProvider
from loom.types import Role

log = logging.getLogger(__name__)

CLASSIFIER_VERSION = 1
_GITHUB_TREE_URL = "https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
_GITHUB_RAW_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
_MAX_BODY_BYTES = 100_000
_FETCH_TIMEOUT_S = 15.0
_CLASSIFIER_MAX_INPUT = 8000
_COST_TIERS = ("free", "low", "medium", "high")


# ── data shapes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Source:
    slug: str
    kind: str
    owner: str
    repo: str
    branch: str
    description: str
    verified: bool


@dataclass(frozen=True)
class KeyReq:
    name: str
    vendor: str = ""
    get_key_url: str = ""
    free_tier_available: bool = False


@dataclass(frozen=True)
class Classification:
    title: str
    summary: str
    capabilities: tuple[str, ...] = ()
    complexity: int = 3
    cost_tier: Literal["free", "low", "medium", "high"] = "free"
    requires_keys: tuple[KeyReq, ...] = ()
    risks: tuple[str, ...] = ()
    confidence: float = 0.5
    language: str = "en"


@dataclass(frozen=True)
class Candidate:
    id: str
    source_slug: str
    source_url: str
    source_verified: bool
    skill_path: str
    body: str
    body_hash: str
    classification: Classification


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float


# ── source loading ─────────────────────────────────────────────────────────


def load_sources(*, builtin_path: Path, user_path: Path | None = None) -> list[Source]:
    """Load curated + user-added sources. User entries are tagged ``verified=False``."""
    sources: list[Source] = []
    seen: set[str] = set()
    inputs = [(builtin_path, True)]
    if user_path is not None:
        inputs.append((user_path, False))
    for path, default_verified in inputs:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            log.warning("could not parse external_sources at %s", path)
            continue
        for entry in data.get("sources", []):
            slug = entry.get("slug")
            if not slug or slug in seen:
                continue
            sources.append(
                Source(
                    slug=str(slug),
                    kind=str(entry.get("kind", "github")),
                    owner=str(entry.get("owner", "")),
                    repo=str(entry.get("repo", "")),
                    branch=str(entry.get("branch", "main")),
                    description=str(entry.get("description", "")),
                    verified=bool(entry.get("verified", default_verified)),
                )
            )
            seen.add(slug)
    return sources


# ── classifier ─────────────────────────────────────────────────────────────


_CLASSIFIER_PROMPT = """You will be given the contents of a SKILL.md file from a public skill \
repository. Your job is to extract a structured summary that a non-technical user can read.

Reply ONLY with valid JSON matching this schema, in the language code specified:

{{
  "title": "<short capability title, 2-6 words>",
  "summary": "<one plain-language sentence describing what this skill does for the user>",
  "capabilities": ["<3-5 short capability bullets>"],
  "complexity": <integer 1-5; 1=trivial, 5=needs lots of setup>,
  "cost_tier": "<free|low|medium|high>",
  "requires_keys": [
    {{"name": "UPPER_SNAKE_NAME", "vendor": "Vendor Name", "get_key_url": "https://...", "free_tier_available": true}}
  ],
  "risks": ["<short safety/privacy concerns or empty list>"],
  "confidence": <float 0..1 for how clearly the SKILL.md describes the skill>
}}

Output language code: {language}

SKILL.md:
---
{body}
---
"""


async def classify_with_llm(
    provider: LLMProvider,
    *,
    body: str,
    language: str,
    model: str | None = None,
) -> Classification:
    """Call the LLM provider once to extract structured metadata from a SKILL.md body.

    ``model`` overrides the provider's construction default; required when
    callers (e.g. the wizard) hold a generic provider that wasn't seeded
    with one. Without it, providers built by the registry raise
    ``LLMError("No model specified")`` on every call.
    """
    excerpt = body[:_CLASSIFIER_MAX_INPUT]
    prompt = _CLASSIFIER_PROMPT.format(body=excerpt, language=language)
    msg = ChatMessage(role=Role.USER, content=prompt)
    resp = await provider.chat([msg], max_tokens=800, model=model)
    raw = (resp.content or "").strip()
    return parse_classification(raw, language=language)


def parse_classification(raw: str, *, language: str) -> Classification:
    """Coerce LLM output into a Classification, degrading gracefully on bad JSON."""
    text = raw
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        obj = json.loads(text)
    except Exception:
        log.warning("classifier did not return JSON: %.200s", raw)
        return Classification(
            title="Untitled skill", summary=text[:160], language=language, confidence=0.0
        )
    return Classification(
        title=str(obj.get("title", "Untitled"))[:80],
        summary=str(obj.get("summary", ""))[:300],
        capabilities=tuple(str(c) for c in (obj.get("capabilities") or []))[:8],
        complexity=max(1, min(5, _coerce_int(obj.get("complexity"), 3))),
        cost_tier=_coerce_cost_tier(obj.get("cost_tier")),
        requires_keys=tuple(_coerce_key_req(k) for k in (obj.get("requires_keys") or [])),
        risks=tuple(str(r) for r in (obj.get("risks") or []))[:8],
        confidence=max(0.0, min(1.0, _coerce_float(obj.get("confidence"), 0.5))),
        language=language,
    )


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _coerce_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _coerce_cost_tier(v: Any) -> Literal["free", "low", "medium", "high"]:
    s = str(v or "free").lower()
    if s in _COST_TIERS:
        return s  # type: ignore[return-value]
    return "free"


def _coerce_key_req(v: Any) -> KeyReq:
    if not isinstance(v, dict):
        return KeyReq(name=str(v))
    return KeyReq(
        name=str(v.get("name", ""))[:64],
        vendor=str(v.get("vendor", ""))[:64],
        get_key_url=str(v.get("get_key_url", ""))[:300],
        free_tier_available=bool(v.get("free_tier_available", False)),
    )


# ── github fetchers (default impls) ────────────────────────────────────────


async def list_skill_md_paths_github(
    client: httpx.AsyncClient, source: Source
) -> list[str]:
    url = _GITHUB_TREE_URL.format(
        owner=source.owner, repo=source.repo, branch=source.branch
    )
    resp = await client.get(
        url, headers={"Accept": "application/vnd.github+json"}, timeout=_FETCH_TIMEOUT_S
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        t["path"]
        for t in data.get("tree", [])
        if t.get("type") == "blob" and t.get("path", "").endswith("/SKILL.md")
    ]


async def fetch_raw_skill_md_github(
    client: httpx.AsyncClient, source: Source, path: str
) -> str:
    url = _GITHUB_RAW_URL.format(
        owner=source.owner, repo=source.repo, branch=source.branch, path=path
    )
    resp = await client.get(url, timeout=_FETCH_TIMEOUT_S)
    resp.raise_for_status()
    return resp.text


# ── cache layout ───────────────────────────────────────────────────────────


def _slugify_path(path: str) -> str:
    """``some/dir/SKILL.md`` → ``some-dir`` (used as candidate id and cache key)."""
    base = path.removesuffix("/SKILL.md").removesuffix("SKILL.md")
    base = base.rstrip("/")
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-").lower()
    return s or "skill"


def _candidate_to_cache(c: Candidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "source_slug": c.source_slug,
        "source_url": c.source_url,
        "source_verified": c.source_verified,
        "skill_path": c.skill_path,
        "body": c.body,
        "body_hash": c.body_hash,
        "classification": _classification_to_dict(c.classification),
        "classifier_version": CLASSIFIER_VERSION,
    }


def _candidate_from_cache(d: dict[str, Any]) -> Candidate:
    return Candidate(
        id=d["id"],
        source_slug=d["source_slug"],
        source_url=d["source_url"],
        source_verified=bool(d.get("source_verified", False)),
        skill_path=d["skill_path"],
        body=d["body"],
        body_hash=d["body_hash"],
        classification=_classification_from_dict(d["classification"]),
    )


def _classification_to_dict(c: Classification) -> dict[str, Any]:
    return {
        "title": c.title,
        "summary": c.summary,
        "capabilities": list(c.capabilities),
        "complexity": c.complexity,
        "cost_tier": c.cost_tier,
        "requires_keys": [asdict(k) for k in c.requires_keys],
        "risks": list(c.risks),
        "confidence": c.confidence,
        "language": c.language,
    }


def _classification_from_dict(d: dict[str, Any]) -> Classification:
    return Classification(
        title=d.get("title", ""),
        summary=d.get("summary", ""),
        capabilities=tuple(d.get("capabilities") or []),
        complexity=int(d.get("complexity", 3)),
        cost_tier=_coerce_cost_tier(d.get("cost_tier")),
        requires_keys=tuple(KeyReq(**k) for k in d.get("requires_keys") or []),
        risks=tuple(d.get("risks") or []),
        confidence=float(d.get("confidence", 0.5)),
        language=d.get("language", "en"),
    )


# ── ranking ────────────────────────────────────────────────────────────────


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(s: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(s.lower()) if len(t) > 2}


def score_candidate(c: Candidate, user_ask: str) -> float:
    """Cheap keyword-overlap score on title/summary/capabilities. 0..1."""
    ask = _tokenize(user_ask)
    if not ask:
        return c.classification.confidence * 0.5
    haystack = " ".join(
        [
            c.classification.title,
            c.classification.summary,
            " ".join(c.classification.capabilities),
        ]
    )
    haystack_tokens = _tokenize(haystack)
    if not haystack_tokens:
        return 0.0
    overlap = len(ask & haystack_tokens)
    if overlap == 0:
        return 0.0
    base = overlap / max(1, len(ask))
    return min(1.0, base * (0.6 + 0.4 * c.classification.confidence))


# ── orchestrator ───────────────────────────────────────────────────────────


PathLister = Callable[[Source], Awaitable[list[str]]]
BodyFetcher = Callable[[Source, str], Awaitable[str]]
Classifier = Callable[[str, str], Awaitable[Classification]]


class SkillDiscovery:
    """Orchestrates listing → fetching → classifying → caching → ranking.

    Tests inject ``path_lister``, ``body_fetcher``, and/or ``classifier`` to avoid
    real HTTP and LLM calls. Production callers pass an ``LLMProvider`` and rely
    on the GitHub default fetchers.
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        sources: list[Source],
        provider: LLMProvider | None = None,
        provider_model: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        path_lister: PathLister | None = None,
        body_fetcher: BodyFetcher | None = None,
        classifier: Classifier | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._sources = list(sources)
        self._provider = provider
        self._provider_model = provider_model
        self._http = http_client
        self._path_lister = path_lister or self._default_path_lister
        self._body_fetcher = body_fetcher or self._default_body_fetcher
        self._classifier = classifier or self._default_classifier

    @property
    def sources(self) -> list[Source]:
        return list(self._sources)

    async def _default_path_lister(self, source: Source) -> list[str]:
        if self._http is None:
            self._http = httpx.AsyncClient()
        if source.kind != "github":
            raise ValueError(f"unsupported source kind: {source.kind!r}")
        return await list_skill_md_paths_github(self._http, source)

    async def _default_body_fetcher(self, source: Source, path: str) -> str:
        if self._http is None:
            self._http = httpx.AsyncClient()
        if source.kind != "github":
            raise ValueError(f"unsupported source kind: {source.kind!r}")
        return await fetch_raw_skill_md_github(self._http, source, path)

    async def _default_classifier(self, body: str, language: str) -> Classification:
        if self._provider is None:
            return Classification(
                title="(no classifier configured)", summary="", language=language
            )
        return await classify_with_llm(
            self._provider,
            body=body,
            language=language,
            model=self._provider_model,
        )

    async def refresh_source(
        self, source: Source, *, language: str = "en", force: bool = False
    ) -> list[Candidate]:
        """Refresh one source's candidates. Cached entries with matching language
        and classifier version are reused unless ``force=True``."""
        try:
            paths = await self._path_lister(source)
        except Exception:
            log.exception("path lister failed for source %s", source.slug)
            return []

        out: list[Candidate] = []
        for path in paths:
            try:
                cand = await self._refresh_one(
                    source, path, language=language, force=force
                )
            except Exception:
                log.exception("refresh failed for %s/%s", source.slug, path)
                continue
            if cand is not None:
                out.append(cand)
        return out

    async def _refresh_one(
        self, source: Source, path: str, *, language: str, force: bool
    ) -> Candidate | None:
        skill_slug = _slugify_path(path)
        cache_path = self._cache_dir / source.slug / f"{skill_slug}.json"
        if cache_path.is_file() and not force:
            cached = self._load_cached(cache_path, language=language)
            if cached is not None:
                return cached

        body = await self._body_fetcher(source, path)
        if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
            log.info("skipping oversized SKILL.md %s/%s", source.slug, path)
            return None
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

        classification = await self._classifier(body, language)
        cand = Candidate(
            id=f"{source.slug}--{skill_slug}",
            source_slug=source.slug,
            source_url=(
                f"https://github.com/{source.owner}/{source.repo}/"
                f"blob/{source.branch}/{path}"
            ),
            source_verified=source.verified,
            skill_path=path,
            body=body,
            body_hash=body_hash,
            classification=classification,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(_candidate_to_cache(cand), indent=2))
        return cand

    def _load_cached(self, cache_path: Path, *, language: str) -> Candidate | None:
        try:
            data = json.loads(cache_path.read_text())
        except Exception:
            return None
        if data.get("classifier_version") != CLASSIFIER_VERSION:
            return None
        cls = data.get("classification") or {}
        if cls.get("language") != language:
            return None
        try:
            return _candidate_from_cache(data)
        except Exception:
            return None

    async def discover(
        self, user_ask: str, *, language: str = "en", limit: int = 8
    ) -> list[ScoredCandidate]:
        """Refresh all sources (cached), score candidates against ``user_ask``,
        return the top-N with score > 0."""
        results: list[Candidate] = []
        for source in self._sources:
            cands = await self.refresh_source(source, language=language)
            results.extend(cands)
        scored = [ScoredCandidate(candidate=c, score=score_candidate(c, user_ask)) for c in results]
        scored.sort(key=lambda x: x.score, reverse=True)
        return [s for s in scored if s.score > 0.0][:limit]

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()


# ── module-level singleton wiring ──────────────────────────────────────────


_BUILTIN_SOURCES_FILE = Path(__file__).parent / "external_sources.json"


def builtin_sources_path() -> Path:
    return _BUILTIN_SOURCES_FILE


def user_sources_path(skills_dir: Path) -> Path:
    return skills_dir / "external_sources.user.json"


def discovery_cache_dir(skills_dir: Path) -> Path:
    return skills_dir / "_discovery_cache"


def load_candidate_by_id(cache_dir: Path, candidate_id: str) -> Candidate | None:
    """Resolve a candidate id (``<source-slug>--<skill-slug>``) back to its
    cached :class:`Candidate`, or ``None`` if the cache file is missing or
    unreadable.

    Used by the build endpoint so the UI only has to round-trip the id, not
    the full body.
    """
    source_slug, sep, skill_slug = candidate_id.partition("--")
    if not sep or not source_slug or not skill_slug:
        return None
    cache_path = cache_dir / source_slug / f"{skill_slug}.json"
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text())
    except Exception:
        return None
    if data.get("classifier_version") != CLASSIFIER_VERSION:
        return None
    try:
        return _candidate_from_cache(data)
    except Exception:
        return None
