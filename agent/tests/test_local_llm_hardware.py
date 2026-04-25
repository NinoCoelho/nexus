"""Tests for local_llm.hardware.probe().

Verifies the shape and types of the returned dict without asserting
specific hardware values (the test host is unknown).
"""

from __future__ import annotations

from nexus.local_llm.hardware import probe


def test_probe_returns_dict():
    result = probe()
    assert isinstance(result, dict)


def test_probe_keys():
    result = probe()
    expected_keys = {
        "ram_gb",
        "free_disk_gb",
        "vram_gb",
        "chip",
        "is_apple_silicon",
        "recommended_max_params_b",
    }
    assert expected_keys == set(result.keys())


def test_probe_types():
    result = probe()
    assert isinstance(result["ram_gb"], float)
    assert isinstance(result["free_disk_gb"], float)
    assert isinstance(result["vram_gb"], float)
    assert isinstance(result["chip"], str)
    assert isinstance(result["is_apple_silicon"], bool)
    assert isinstance(result["recommended_max_params_b"], float)


def test_probe_values_sane():
    result = probe()
    assert result["ram_gb"] > 0
    assert result["free_disk_gb"] >= 0
    assert result["vram_gb"] >= 0
    assert len(result["chip"]) > 0
    assert result["recommended_max_params_b"] > 0


def test_probe_is_cached():
    """probe() should return the same object on repeated calls (module-level cache)."""
    r1 = probe()
    r2 = probe()
    assert r1 is r2


def test_vram_matches_ram_on_apple_silicon():
    result = probe()
    if result["is_apple_silicon"]:
        assert result["vram_gb"] == result["ram_gb"]
    else:
        assert result["vram_gb"] == 0.0


def test_recommended_max_params_heuristic():
    result = probe()
    expected = round(result["ram_gb"] / 2.5, 2)
    assert result["recommended_max_params_b"] == expected
