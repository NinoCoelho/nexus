---
name: stock-media
description: Use this whenever you need stock photos or videos for articles, carousels, videos, or any creative project. Searches Pixabay and Pexels with round-robin API key rotation.
type: procedure
role: media
platform: nexus
platform_version: "0.1"
nexus_status: stable
nexus_authored_by: builtin
requires_keys:
  - PIXABAY_API_KEY
  - PEXELS_API_KEY
---

## When to use
- Any time you need stock photos or videos downloaded to disk.
- When an article, carousel, reel, or editorial needs media assets.
- Prefer over manual web searches for stock imagery -- this is faster, legal, and handles key rotation automatically.

## Steps

### 1. Search for media

```bash
python3 ~/.nexus/vault/scripts/stock-media.py search "mountain sunset" --output json

# Table output for quick scanning
python3 ~/.nexus/vault/scripts/stock-media.py search "office desk" --per-page 10

# Video search
python3 ~/.nexus/vault/scripts/stock-media.py search "ocean waves" --type video --source pexels

# With filters
python3 ~/.nexus/vault/scripts/stock-media.py search "team meeting" --orientation landscape --per-page 10

# Single source only
python3 ~/.nexus/vault/scripts/stock-media.py search "coffee" --source pixabay --output json
```

**Flags:**
- `--source pixabay|pexels|all` (default: all)
- `--type photo|video` (default: photo)
- `--per-page N` (default 20, max 200 for Pixabay, 80 for Pexels)
- `--orientation landscape|portrait|square`
- `--category CAT` (Pixabay only)
- `--color COLOR` (Pexels only -- hex or name)
- `--output json|table` (default: table)

### 2. Download specific files

```bash
# Download by URL
python3 ~/.nexus/vault/scripts/stock-media.py download "https://images.pexels.com/photos/1234/photo.jpeg" --filename cover.jpg --dir ./assets

# Batch: search + download top N results
python3 ~/.nexus/vault/scripts/stock-media.py download-batch "coffee shop" --count 5 --size large --dir ./assets
```

**Batch download flags:**
- `--count N` (how many files to download)
- `--size original|large|medium|small` (default: large)
- `--dir PATH` (output directory, created if missing)

### 3. Use results

Search returns JSON with download URLs, dimensions, likes, and page URLs. Pick the best results and download. The output from `--output json` can be piped to `jq` for filtering.

## Gotchas
- **Pexels requires User-Agent** -- already handled in the script. If you ever rewrite the HTTP layer, make sure to include `User-Agent` header or Pexels returns 403.
- **Round-robin state is persisted** to `~/.nexus/vault/scripts/.stock-media-state.json` so key rotation survives across invocations.
- **Pixabay rate limit**: ~5000 requests/hour per key. Pexels: ~200 requests/hour per key. With round-robin this gives good headroom.
- **Pexels videos**: the script picks HD quality by default; all quality options are in `all_qualities`.
- **No external dependencies** -- uses only Python stdlib (`urllib`, `json`, `argparse`).
- **API keys**: The script reads keys from environment variables (`PIXABAY_API_KEY`, `PEXELS_API_KEY`). Ensure these are set in `~/.nexus/secrets.toml` or your shell environment.
