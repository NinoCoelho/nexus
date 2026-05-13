"""Per-skill Python virtual-environment manager.

Each skill that ships a ``requirements.txt`` gets an isolated venv under
``~/.nexus/venvs/<skill-name>/``.  Venvs are created eagerly (at seed /
install time) and re-synced when the requirements file changes (content-hash
check).  They are intentionally kept outside the skill directory so the skill
folder stays portable — copy it to another host, and the venv is recreated
from the manifest on next use.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_VENVS_DIR = Path.home() / ".nexus" / "venvs"


def _venv_root(skill_name: str) -> Path:
    return _VENVS_DIR / skill_name


def _python_bin(skill_name: str) -> Path:
    return _venv_root(skill_name) / "bin" / "python3"


def _requirements_file(skill_dir: Path) -> Path:
    return skill_dir / "requirements.txt"


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _hash_marker_path(skill_name: str) -> Path:
    return _venv_root(skill_name) / ".requirements-hash"


def _read_hash_marker(skill_name: str) -> str | None:
    p = _hash_marker_path(skill_name)
    if p.is_file():
        return p.read_text().strip()
    return None


def _write_hash_marker(skill_name: str, h: str) -> None:
    _hash_marker_path(skill_name).write_text(h)


def has_venv(skill_name: str) -> bool:
    return _python_bin(skill_name).is_file()


def venv_python(skill_name: str) -> Path | None:
    bin_ = _python_bin(skill_name)
    return bin_ if bin_.is_file() else None


def ensure_venv(
    skill_name: str,
    skill_dir: Path,
    python_version: str | None = None,
) -> Path:
    """Create or re-sync the venv for *skill_name*.

    Returns the path to the venv's ``python3`` binary.
    """
    req_file = _requirements_file(skill_dir)
    if not req_file.is_file():
        raise FileNotFoundError(
            f"no requirements.txt in {skill_dir}"
        )

    venv_dir = _venv_root(skill_name)
    current_hash = _hash_file(req_file)
    stored_hash = _read_hash_marker(skill_name)

    if has_venv(skill_name) and stored_hash == current_hash:
        return _python_bin(skill_name)

    if venv_dir.exists():
        shutil.rmtree(venv_dir)

    venv_dir.parent.mkdir(parents=True, exist_ok=True)

    uv_args = ["uv", "venv", str(venv_dir)]
    if python_version:
        uv_args += ["--python", python_version]

    log.info("creating venv for skill %s (python=%s)", skill_name, python_version)
    r = subprocess.run(uv_args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(
            f"uv venv failed for {skill_name}: {r.stderr.strip()}"
        )

    log.info("installing requirements for skill %s", skill_name)
    r = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(_python_bin(skill_name)),
            "-r",
            str(req_file),
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise RuntimeError(
            f"uv pip install failed for {skill_name}: {r.stderr.strip()}"
        )

    _write_hash_marker(skill_name, current_hash)
    log.info("venv ready for skill %s", skill_name)
    return _python_bin(skill_name)


def sync_venv(
    skill_name: str,
    skill_dir: Path,
    python_version: str | None = None,
) -> Path:
    """Re-sync a skill venv if requirements changed, creating it if needed."""
    return ensure_venv(skill_name, skill_dir, python_version)


def remove_venv(skill_name: str) -> None:
    venv_dir = _venv_root(skill_name)
    if venv_dir.exists():
        shutil.rmtree(venv_dir)
        log.info("removed venv for skill %s", skill_name)


def venv_info(skill_name: str, skill_dir: Path) -> dict[str, object]:
    """Return a dict describing the venv state for agent consumption."""
    req_file = _requirements_file(skill_dir)
    has_req = req_file.is_file()
    exists = has_venv(skill_name)
    py = str(venv_python(skill_name)) if exists else None
    req_content = req_file.read_text().strip() if has_req else None
    return {
        "has_requirements": has_req,
        "venv_exists": exists,
        "python_path": py,
        "requirements": req_content,
    }
