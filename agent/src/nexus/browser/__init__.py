"""Managed Chrome (computer-use) browser subsystem.

One Chrome process per context — ``main`` (coordinator + unprojected
sessions) and ``project:<id>`` — driven over CDP by the ``browser`` tool
and the ``/browser/*`` HTTP surface.
"""

from .manager import (
    ACTIONS,
    BrowserError,
    BrowserInstance,
    BrowserManager,
    find_chrome_binary,
    get_manager,
    set_manager,
)
from .tool import BROWSER_TOOL, BrowserHandler

__all__ = [
    "ACTIONS",
    "BROWSER_TOOL",
    "BrowserError",
    "BrowserHandler",
    "BrowserInstance",
    "BrowserManager",
    "find_chrome_binary",
    "get_manager",
    "set_manager",
]
