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


# Secret-ish variable-name fragment used across exfil patterns. Matches
# API_KEY / OPENAI_SECRET / GITHUB_TOKEN / DB_PASSWORD / AWS_CREDENTIALS ...
_SECRET_VAR = r"[A-Z0-9_]*(?:API_KEY|KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*"

_DANGEROUS: list[tuple[str, re.Pattern[str]]] = [
    # $(env ...), $(printenv ...) — env-dumping command substitution
    ("credential-exfil-envsub", re.compile(r"\$\(\s*(?:env|printenv)\b", re.IGNORECASE)),
    # ${API_KEY}-style expansion of a secret variable
    ("credential-exfil-varref", re.compile(r"\$\{\s*" + _SECRET_VAR + r"\s*\}")),
    # env/printenv piped into network tools or base64
    (
        "credential-exfil-envpipe",
        re.compile(r"\b(?:env|printenv)\b[^|\n]*\|[^\n]*(?:curl|wget|nc|ncat|socat|base64|http)", re.IGNORECASE),
    ),
    # a secret-looking $VAR (braced or bare) piped into base64 or a network tool
    (
        "credential-exfil-varpipe",
        re.compile(
            r"\$\{?\s*[A-Z0-9_]*(?:API_KEY|KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|ACCESS)[A-Z0-9_]*\s*\}?"
            r"[^|\n]*\|[^\n]*(?:base64|curl|wget|nc|ncat|socat|http)"
        ),
    ),
    # curl/wget pointed at a $-referenced secret (any form: $(CMD), ${VAR}, $VAR)
    (
        "credential-exfil-curl",
        re.compile(r"\b(?:curl|wget)\b[^#\n]*\$\(?\{?\s*" + _SECRET_VAR, re.IGNORECASE),
    ),
    # ssh/aws material: ~/.ssh, x/.ssh, .ssh/config, private key files,
    # .aws/credentials — in prose or code
    (
        "credential-exfil-ssh",
        re.compile(r"\.ssh\b|\.aws[/\\]|\bid_rsa\b|\bid_ed25519\b|\bauthorized_keys\b", re.IGNORECASE),
    ),
    # base64 combined with a secret reference ("base64 $OPENAI_API_KEY" etc.)
    (
        "credential-exfil-base64",
        re.compile(r"base64[^#\n]*\$\(?\{?\s*" + _SECRET_VAR, re.IGNORECASE),
    ),
    # Recursive/forced rm aimed at the filesystem root, home, or system dirs.
    # The target alternation deliberately requires a root-ish operand, so
    # ``rm -rf build/`` and ``rm -rf ./node_modules`` stay legal.
    (
        "destructive-rm",
        re.compile(
            r"\brm\s+[^#\n]*-[A-Za-z]*[rf][A-Za-z]*"  # -r/-f/-rf/-rvf… flags
            r"[^#\n]*"
            r"(?:"
            r"(?:\s|\A)/(?:\s*\*|\s*[;&|)]|\s*$)"  # "/" and "/*"
            r"|~(?:\s|/\*|$|\s*[;&|)])"  # "~", "~/*"
            r"|\$HOME"
            r"|/(?:etc|usr|var|bin|sbin|lib|boot|dev|opt|root|System|Library|private)\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    ("destructive-dd", re.compile(r"\bdd\s+if=", re.IGNORECASE)),
    ("destructive-mkfs", re.compile(r"\bmkfs\b", re.IGNORECASE)),
    ("prompt-injection-ignore", re.compile(r"ignore (?:all|previous|prior|above) instructions", re.IGNORECASE)),
    ("prompt-injection-disregard", re.compile(r"disregard the system", re.IGNORECASE)),
]

_CAUTION: list[tuple[str, re.Pattern[str]]] = [
    ("persistence-cron", re.compile(r"\b(?:cron|crontab)\b", re.IGNORECASE)),
    ("persistence-launchd", re.compile(r"\blaunchd\b", re.IGNORECASE)),
    ("persistence-systemd", re.compile(r"\b(?:systemd|systemctl)\b", re.IGNORECASE)),
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
