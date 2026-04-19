"""Regex-based static security scanner for agent-authored skills."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Finding:
    pattern: str
    match: str
    path: str


@dataclass
class Verdict:
    level: Literal["safe", "caution", "dangerous"]
    findings: list[Finding] = field(default_factory=list)


_DANGEROUS: list[tuple[str, re.Pattern[str]]] = [
    ("credential-exfil-curl", re.compile(r"curl.*\$\((API_KEY|SECRET|TOKEN)\)", re.IGNORECASE)),
    ("credential-exfil-ssh", re.compile(r"~/\.ssh", re.IGNORECASE)),
    ("credential-exfil-aws", re.compile(r"~/\.aws", re.IGNORECASE)),
    ("credential-exfil-base64", re.compile(r"base64.*env", re.IGNORECASE)),
    ("destructive-rm", re.compile(r"rm\s+-rf\s+/", re.IGNORECASE)),
    ("destructive-dd", re.compile(r"\bdd\s+if=", re.IGNORECASE)),
    ("destructive-mkfs", re.compile(r"\bmkfs\b", re.IGNORECASE)),
    ("prompt-injection-ignore", re.compile(r"ignore previous instructions", re.IGNORECASE)),
    ("prompt-injection-disregard", re.compile(r"disregard the system", re.IGNORECASE)),
]

_CAUTION: list[tuple[str, re.Pattern[str]]] = [
    ("persistence-cron", re.compile(r"\bcron\b", re.IGNORECASE)),
    ("persistence-launchd", re.compile(r"\blaunchd\b", re.IGNORECASE)),
    ("persistence-systemd", re.compile(r"\bsystemd\b", re.IGNORECASE)),
    ("persistence-bashrc", re.compile(r"\.bashrc", re.IGNORECASE)),
]


def scan(skill_md: str, scripts: list[str] | None = None) -> Verdict:
    """Scan SKILL.md body and optional script contents for dangerous patterns."""
    sources: list[tuple[str, str]] = [("SKILL.md", skill_md)]
    for i, s in enumerate(scripts or []):
        sources.append((f"script[{i}]", s))

    findings: list[Finding] = []
    level: Literal["safe", "caution", "dangerous"] = "safe"

    for path, content in sources:
        for name, pat in _DANGEROUS:
            m = pat.search(content)
            if m:
                findings.append(Finding(pattern=name, match=m.group(0), path=path))
                level = "dangerous"
        for name, pat in _CAUTION:
            m = pat.search(content)
            if m:
                findings.append(Finding(pattern=name, match=m.group(0), path=path))
                if level != "dangerous":
                    level = "caution"

    return Verdict(level=level, findings=findings)
