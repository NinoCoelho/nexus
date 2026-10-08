---
name: context_cleanup
description: Bounds the size and age of the compaction artifact directories in the vault
schedule: "17 4 * * *"
enabled: true
---

Compaction is reversible by design: shrunk tool results are written to
`.tool-cache/`, and every summarized, dropped or hard-trimmed message is
journaled to `.session-memory/.parts/`. Both live inside the vault, which
Syncthing replicates, and nothing used to clean either of them up.

This driver removes tool-cache entries older than 30 days (and enforces a
512 MB ceiling, oldest-first), plus recovery archives older than 180 days.
The recovery archives get a much longer horizon because they are the only
verbatim record of messages that left the context window.

Runs daily at 04:17 to stay clear of the 6-hourly workflow cleanup.
