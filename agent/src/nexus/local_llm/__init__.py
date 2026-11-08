"""Local LLM management: hardware probe, HuggingFace search, download, and runtime control."""

from __future__ import annotations

from .hardware import probe
from .hf_search import HfSearchError, search_gguf_repos, list_repo_ggufs, repo_card
from .downloader import DownloadTask, start_download, get_task, list_tasks
from .manager import (
    add_to_config,
    cleanup_stale_config,
    discover_binary,
    is_running,
    list_running,
    remove_from_config,
    slug_for,
    slugify,
    start,
    stop,
    stop_all,
)
from .shards import group_files, parse_shard, shard_base

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
    "add_to_config",
    "cleanup_stale_config",
    "discover_binary",
    "is_running",
    "list_running",
    "remove_from_config",
    "slug_for",
    "slugify",
    "group_files",
    "parse_shard",
    "shard_base",
    "start",
    "stop",
    "stop_all",
]
