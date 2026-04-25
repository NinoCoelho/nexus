"""Hardware capability probe for local LLM sizing.

Runs once per process (cached after first call). On Apple Silicon, parses
``system_profiler SPHardwareDataType -json`` to get the chip name; falls back
to ``platform.processor()`` on failure.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

_CACHE: dict[str, Any] | None = None


def probe() -> dict[str, Any]:
    """Return a dict describing the host hardware capabilities.

    Returns:
        dict with keys:
            ram_gb: float — total RAM in gigabytes.
            free_disk_gb: float — free disk in gigabytes at home dir.
            vram_gb: float — VRAM in GB (unified memory on Apple Silicon; else 0).
            chip: str — chip/processor name.
            is_apple_silicon: bool — True when running on Apple Silicon.
            recommended_max_params_b: float — rough Q4-weight param budget.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    mem = psutil.virtual_memory()
    ram_gb = mem.total / 1e9

    home = Path.home()
    disk = psutil.disk_usage(str(home))
    free_disk_gb = disk.free / 1e9

    is_apple_silicon = _detect_apple_silicon()
    chip = _chip_name(is_apple_silicon)
    vram_gb = ram_gb if is_apple_silicon else 0.0

    # Heuristic: Q4 weights ≈ 0.5 bytes/param; KV cache + OS overhead adds ~60%
    # so effective usable RAM for model is ram_gb / 2.5.
    recommended_max_params_b = ram_gb / 2.5

    _CACHE = {
        "ram_gb": round(ram_gb, 2),
        "free_disk_gb": round(free_disk_gb, 2),
        "vram_gb": round(vram_gb, 2),
        "chip": chip,
        "is_apple_silicon": is_apple_silicon,
        "recommended_max_params_b": round(recommended_max_params_b, 2),
    }
    return _CACHE


def _detect_apple_silicon() -> bool:
    if sys.platform != "darwin":
        return False
    machine = platform.machine()
    return machine == "arm64"


def _chip_name(is_apple_silicon: bool) -> str:
    if not is_apple_silicon:
        proc = platform.processor()
        return proc if proc else "unknown"

    try:
        result = subprocess.run(
            ["system_profiler", "SPHardwareDataType", "-json"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        data = json.loads(result.stdout)
        items = data.get("SPHardwareDataType", [])
        if items:
            chip = items[0].get("chip_type") or items[0].get("cpu_type") or ""
            if chip:
                return chip
    except Exception:
        pass

    proc = platform.processor()
    return proc if proc else "unknown"
