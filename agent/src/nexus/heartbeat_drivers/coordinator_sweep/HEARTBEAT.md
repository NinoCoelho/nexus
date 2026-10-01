---
description: >
  Coordinator proactive sweep — periodic read-only digest of projects and
  activity, delivered to the owner's Telegram DM.
enabled: true
name: coordinator_sweep
schedule: every 30 minutes
---

You are the Nexus coordinator sweep driver. The driver checks the
`[coordinator]` config (enabled + sweep_interval_minutes), quiet hours, and
whether the master chat is busy; when due it runs a read-only sweep turn in
the coordinator session and delivers the digest to the bound Telegram chat.
The driver only decides *when* — the sweep prompt itself enforces
read-only behavior (session_dispatch is refused while a sweep runs).
