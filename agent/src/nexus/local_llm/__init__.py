"""Local LLM management: hardware probe, HuggingFace search, download, and runtime control."""

from __future__ import annotations

from .hardware import probe
from .hf_search import HfSearchError, search_gguf_repos, list_repo_ggufs, repo_card
from .downloader import DownloadTask, start_download, get_task, list_tasks
from .manager import current, discover_binary, start, stop, restart

__all__ = [
    "probe",
    "HfSearchError",
    "search_gguf_repos",
    "list_repo_ggufs",
    "repo_card",
    "DownloadTask",
    "start_download",
    "get_task",
    "list_tasks",
    "current",
    "discover_binary",
    "start",
    "stop",
    "restart",
]
