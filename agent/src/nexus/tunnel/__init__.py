"""Public tunnel sharing for Nexus.

Wraps a tunnel provider (ngrok) so the user can expose the local server to the
internet with a shareable link that auto-authenticates via a token.

Public surface:
    - get_manager() -> TunnelManager (process-wide singleton)
    - TunnelManager (start/stop/status, token validation)
"""

from __future__ import annotations

from .manager import TunnelManager, get_manager

__all__ = ["TunnelManager", "get_manager"]
