"""Nexus — self-evolving agentic platform."""

from importlib import metadata

try:
    __version__ = metadata.version("nexus")
except metadata.PackageNotFoundError:
    __version__ = "dev"
