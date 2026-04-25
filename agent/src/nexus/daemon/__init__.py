"""Daemon management for Nexus.

Re-exports all public symbols so ``from nexus.daemon import X`` keeps
working after the module was split into a package.
"""

from .installer import ServiceInstaller
from .manager import DaemonManager

# Global instances (mirrors the old module-level singletons)
daemon_manager = DaemonManager()
service_installer = ServiceInstaller()

__all__ = [
    "DaemonManager",
    "ServiceInstaller",
    "daemon_manager",
    "service_installer",
]
