---
name: calendar_trigger
description: Fires scheduled calendar events when their start time arrives.
schedule: every 1 minute
enabled: true
---

You are the Nexus calendar trigger driver. The driver scans every calendar
file in the vault each minute and fires events whose start time has just
arrived. Dispatch happens inline via the vault dispatch pipeline; the
scheduler's run_fn is unused (driver returns no events).
