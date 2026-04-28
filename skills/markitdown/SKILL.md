---
name: markitdown
description: Convert files to clean LLM-friendly Markdown — PDF, DOCX, PPTX, XLSX, HTML, CSV, JSON, XML, EPUB, images (with OCR if available), audio (with transcription if available), and ZIP archives. Use whenever the user provides a non-markdown document and asks to read, summarize, ingest, or save it to the vault.
type: procedure
role: ingest
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
---

# markitdown

Convert almost any document to Markdown so the agent (and the vault FTS index)
can read it. Wraps Microsoft's `markitdown` library with a Nexus-native CLI
that auto-installs the dependency on first use and can write the result
straight into the vault.

## When to use

- The user attaches or references a `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.html`,
  `.epub`, `.csv`, `.json`, `.xml`, `.zip`, `.jpg/.png`, `.mp3/.wav` and asks
  to read, summarize, extract, or ingest it.
- "Save this PDF to the vault", "ingest this file", "what does this docx say".
- Before another skill (deep-research, summarize-file, vault-curator) needs
  text it can actually parse.

## When **not** to use

- Plain `.md` / `.txt` files — just read them directly.
- Web pages — use `web-scrape` (it already returns Markdown).
- Source code — read the file.

## Procedure

### 1. Convert and dump to stdout

```bash
python3 ~/.nexus/skills/markitdown/scripts/markitdown.py convert <path-or-url>
```

Output is Markdown on stdout. Pipe to `wc -l` first if the file is huge — the
agent loop has limited context.

### 2. Convert and save to a file

```bash
python3 ~/.nexus/skills/markitdown/scripts/markitdown.py convert <input> -o <output.md>
```

### 3. Convert and write straight into the vault

```bash
python3 ~/.nexus/skills/markitdown/scripts/markitdown.py convert <input> --vault inbox/<name>.md
```

This writes to `~/.nexus/vault/<rel-path>` and emits frontmatter with `source:`
pointing at the original file plus a `tags: [imported]` marker so the
vault-curator can find it later. Use a subfolder (`inbox/`, `research/`, …)
that already exists in the user's vault layout when possible.

### 4. Batch a directory

```bash
python3 ~/.nexus/skills/markitdown/scripts/markitdown.py batch <dir> --out-dir <dest>
```

Walks `<dir>` recursively, converts every supported file, mirrors the tree
under `<dest>`. Add `--vault inbox/` to mirror into the vault instead.

### 5. List supported formats

```bash
python3 ~/.nexus/skills/markitdown/scripts/markitdown.py formats
```

## Flags

- `-o, --output PATH` — write to a regular file.
- `--vault REL` — write under `~/.nexus/vault/REL`. Mutually exclusive with `-o`.
- `--stdout` — force stdout even when `--vault`/`-o` is set (also writes the file).
- `--max-bytes N` — refuse files larger than N (default 50 MiB) to protect the loop.
- `--quiet` — suppress progress notes on stderr.
- `--no-install` — fail instead of pip-installing `markitdown` on first run.

## First-run install

The wrapper auto-installs `markitdown[all]` into the **current Python env**
(via `pip install --quiet`) the first time it runs and the import fails.
On the packaged `.app` build this targets the bundled Python; in dev it lands
in the active `uv` venv. Pass `--no-install` to opt out and surface the
ImportError to the caller. For a one-shot manual install:

```bash
uv pip install 'markitdown[all]'   # dev checkout
pip install 'markitdown[all]'      # packaged .app
```

## Supported formats

PDF · DOCX · PPTX · XLSX · XLS · HTML · CSV · JSON · XML · EPUB · ZIP ·
images (JPEG / PNG / GIF / WebP — with OCR when Tesseract is installed) ·
audio (WAV / MP3 — with Whisper transcription when configured) · YouTube URLs.

For images with no OCR backend you still get EXIF metadata as Markdown.
For audio with no Whisper backend you get duration + format metadata only.

## Gotchas

- **Large PDFs** — convert to a file (`-o`) and `head` it before reading the
  full result; otherwise you'll blow the context window.
- **Scanned PDFs** need an OCR backend (`tesseract` on PATH, or the optional
  Azure Document Intelligence keys exposed via `MD_AZURE_*` env vars).
- **DOCX with comments / tracked changes** — markitdown drops those by
  default. If the user cares about review history, fall back to a different
  tool.
- **Encrypted documents** — the wrapper reports a clean error on stderr
  rather than crashing.
- The wrapper sets `--max-bytes` to 50 MiB to keep accidental large-file
  conversions from hanging the loop. Override explicitly when needed.
