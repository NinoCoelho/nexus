#!/usr/bin/env python3
"""
markitdown — Nexus wrapper around Microsoft's markitdown library.

Converts files (PDF / DOCX / PPTX / XLSX / HTML / images / audio / …) to clean
Markdown. Can dump to stdout, write to a file, or land directly inside
~/.nexus/vault/.

Usage:
  markitdown.py convert <input> [-o OUT | --vault REL] [--stdout]
  markitdown.py batch   <dir>   [--out-dir OUT | --vault REL]
  markitdown.py formats
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB

# File extensions markitdown can handle. Kept in sync with the upstream README;
# unknown extensions fall through to markitdown anyway, this list is only used
# for `formats` and for batch-mode filtering.
SUPPORTED_EXTS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls",
    ".html", ".htm", ".csv", ".json", ".xml", ".epub", ".zip",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".wav", ".mp3", ".m4a",
}


def log(msg: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr, flush=True)


def ensure_markitdown(*, allow_install: bool, quiet: bool):
    """Import MarkItDown, pip-installing markitdown[all] on first failure."""
    try:
        from markitdown import MarkItDown  # type: ignore[import]

        return MarkItDown
    except ImportError:
        if not allow_install:
            raise

    log("markitdown not installed — running `pip install markitdown[all]` …", quiet=quiet)
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "markitdown[all]"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(
            "failed to install markitdown — try `uv pip install 'markitdown[all]'` "
            "manually or pass --no-install"
        )

    from markitdown import MarkItDown  # type: ignore[import]

    return MarkItDown


def is_url(s: str) -> bool:
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def vault_root() -> Path:
    env = os.environ.get("NEXUS_VAULT_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".nexus" / "vault"


def write_vault(rel: str, body: str, *, source: str) -> Path:
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise SystemExit(f"--vault path must be relative and inside the vault: {rel!r}")
    if rel_path.suffix.lower() != ".md":
        rel_path = rel_path.with_suffix(".md")

    target = vault_root() / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = (
        "---\n"
        f"source: {source}\n"
        f"imported_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        "tags: [imported]\n"
        "---\n\n"
    )
    target.write_text(frontmatter + body, encoding="utf-8")
    return target


def convert_one(MarkItDown, src: str, *, max_bytes: int) -> str:
    if not is_url(src):
        p = Path(src).expanduser()
        if not p.exists():
            raise SystemExit(f"no such file: {src}")
        size = p.stat().st_size
        if size > max_bytes:
            raise SystemExit(
                f"refusing to convert {src!r}: {size} bytes > --max-bytes {max_bytes}. "
                "Pass a larger --max-bytes if intentional."
            )
        target = str(p)
    else:
        target = src

    md = MarkItDown()
    try:
        result = md.convert(target)
    except Exception as exc:
        raise SystemExit(f"markitdown failed on {src}: {exc}") from exc
    return getattr(result, "text_content", "") or ""


def cmd_convert(args: argparse.Namespace) -> int:
    if args.output and args.vault:
        raise SystemExit("--output and --vault are mutually exclusive")

    MarkItDown = ensure_markitdown(allow_install=not args.no_install, quiet=args.quiet)
    body = convert_one(MarkItDown, args.input, max_bytes=args.max_bytes)

    written: Path | None = None
    if args.vault:
        written = write_vault(args.vault, body, source=args.input)
        log(f"wrote {written}", quiet=args.quiet)
    elif args.output:
        out = Path(args.output).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        written = out
        log(f"wrote {out}", quiet=args.quiet)

    if written is None or args.stdout:
        sys.stdout.write(body)
        if not body.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    if args.out_dir and args.vault:
        raise SystemExit("--out-dir and --vault are mutually exclusive")
    if not (args.out_dir or args.vault):
        raise SystemExit("batch needs either --out-dir or --vault")

    root = Path(args.input).expanduser()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {args.input}")

    MarkItDown = ensure_markitdown(allow_install=not args.no_install, quiet=args.quiet)

    converted = 0
    skipped = 0
    failed = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTS:
            skipped += 1
            continue
        rel = path.relative_to(root).with_suffix(".md")
        try:
            body = convert_one(MarkItDown, str(path), max_bytes=args.max_bytes)
        except SystemExit as exc:
            log(f"  ! {path}: {exc}", quiet=args.quiet)
            failed += 1
            continue

        if args.vault:
            write_vault(str(Path(args.vault) / rel), body, source=str(path))
        else:
            out = Path(args.out_dir).expanduser() / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(body, encoding="utf-8")
        converted += 1
        log(f"  + {rel}", quiet=args.quiet)

    log(
        f"done: {converted} converted, {skipped} skipped (unsupported), {failed} failed",
        quiet=args.quiet,
    )
    return 0 if failed == 0 else 1


def cmd_formats(_: argparse.Namespace) -> int:
    for ext in sorted(SUPPORTED_EXTS):
        print(ext)
    print("(plus YouTube and other http(s) URLs)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="markitdown", description=__doc__)
    p.add_argument("--quiet", action="store_true", help="suppress progress on stderr")
    p.add_argument(
        "--no-install",
        action="store_true",
        help="don't pip-install markitdown on first run; fail loudly instead",
    )
    p.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"refuse inputs larger than this (default {DEFAULT_MAX_BYTES})",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="convert one file or URL")
    c.add_argument("input", help="path or http(s) URL")
    c.add_argument("-o", "--output", help="write Markdown to this file")
    c.add_argument("--vault", help="write to ~/.nexus/vault/<this rel path>")
    c.add_argument("--stdout", action="store_true", help="also dump to stdout")
    c.set_defaults(func=cmd_convert)

    b = sub.add_parser("batch", help="convert every supported file in a directory")
    b.add_argument("input", help="source directory")
    b.add_argument("--out-dir", help="mirror tree under this directory")
    b.add_argument("--vault", help="mirror tree under ~/.nexus/vault/<rel>")
    b.set_defaults(func=cmd_batch)

    f = sub.add_parser("formats", help="list supported extensions")
    f.set_defaults(func=cmd_formats)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
