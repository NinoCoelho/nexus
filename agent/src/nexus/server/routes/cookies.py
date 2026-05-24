"""Cookie import API for the Chrome cookie export extension.

Stores cookies in Netscape HTTP Cookie File format at
``~/.nexus/cookies/<domain>.cookies.txt`` — the same format and
directory already read by Loom's ``FilesystemCookieStore``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()

COOKIE_DIR = Path.home() / ".nexus" / "cookies"


class CookieEntry(BaseModel):
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    httpOnly: bool = False
    expirationDate: float = 0


class DomainCookies(BaseModel):
    domain: str
    cookies: list[CookieEntry]


class ImportRequest(BaseModel):
    domains: list[DomainCookies]


def _domain_filename(domain: str) -> str:
    d = domain.lstrip(".")
    return f"{d}.cookies.txt"


def _write_netscape(domain: str, cookies: list[CookieEntry]) -> int:
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    fname = _domain_filename(domain)
    path = COOKIE_DIR / fname
    lines: list[str] = ["# Netscape HTTP Cookie File", ""]
    for c in cookies:
        domain_val = c.domain
        flag = "TRUE" if domain_val.startswith(".") else "FALSE"
        secure = "TRUE" if c.secure else "FALSE"
        expiry = str(int(c.expirationDate)) if c.expirationDate else "0"
        http_only = "#HttpOnly_" if c.httpOnly else ""
        lines.append(
            f"{http_only}{domain_val}\t{flag}\t{c.path}\t{secure}\t{expiry}\t{c.name}\t{c.value}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(cookies)


@router.post("/cookies/import")
async def import_cookies(body: ImportRequest) -> dict[str, Any]:
    total = 0
    imported_domains: list[str] = []
    for dc in body.domains:
        if not dc.cookies:
            continue
        count = _write_netscape(dc.domain, dc.cookies)
        total += count
        imported_domains.append(dc.domain)
    return {"imported": total, "domains": imported_domains}


@router.get("/cookies")
async def list_cookies() -> list[dict[str, Any]]:
    if not COOKIE_DIR.exists():
        return []
    results: list[dict[str, Any]] = []
    for f in sorted(COOKIE_DIR.glob("*.cookies.txt")):
        domain = f.stem.rsplit(".cookies", 1)[0]
        line_count = sum(1 for line in f.read_text().splitlines() if line and not line.startswith("#"))
        results.append({"domain": domain, "count": line_count, "file": str(f)})
    return results


@router.delete("/cookies/{domain:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cookies(domain: str) -> None:
    fname = _domain_filename(domain)
    path = COOKIE_DIR / fname
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No cookies for {domain}")
    path.unlink()
