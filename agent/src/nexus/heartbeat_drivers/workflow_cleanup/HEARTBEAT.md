---
name: workflow_cleanup
description: Periodically cleans up old completed/failed/cancelled workflow runs
schedule: "0 */6 * * *"
enabled: true
---

Removes workflow runs older than 30 days that have reached a terminal status (completed, failed, cancelled) and their associated step run data.
