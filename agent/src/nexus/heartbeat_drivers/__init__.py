"""Heartbeat drivers shipped with Nexus.

Each subdirectory contains a HEARTBEAT.md + driver.py pair scanned by
:class:`loom.heartbeat.HeartbeatRegistry` at server startup.
"""

from pathlib import Path

DRIVERS_DIR = Path(__file__).parent
