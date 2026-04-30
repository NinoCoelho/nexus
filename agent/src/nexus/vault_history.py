"""Vault history — opt-in git-backed snapshots of ~/.nexus/vault/.

When enabled (config: ``vault.history.enabled``), every successful vault
mutation (write/delete/move) appends a commit to a private git work-tree
at ``~/.nexus/.vault-history``. The vault directory itself is the
work-tree, so no ``.git`` folder appears inside the vault.

Undo is per-path: each call steps the file/folder back one *real* commit
in its log (commits whose message starts with ``undo:`` are skipped).
A small ``undo-cursors.json`` next to the git-dir tracks how far back
we have stepped per path; any new write to a path resets its cursor.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_VAULT_ROOT = Path("~/.nexus/vault").expanduser()
_HISTORY_DIR = Path("~/.nexus/.vault-history").expanduser()
_CURSORS_PATH = Path("~/.nexus/.vault-history-cursors.json").expanduser()

_UNDO_PREFIX = "undo: "
_BOOTSTRAP_MSG = "history: enable"


class HistoryError(RuntimeError):
    """Raised for user-visible history failures (e.g. git missing, repo absent)."""


@dataclass
class Commit:
    sha: str
    timestamp: int  # unix seconds
    message: str
    action: str  # write | delete | move | undo | enable | other


@dataclass
class UndoResult:
    undone: bool
    reason: str | None = None  # set when undone is False
    commit: str | None = None  # the new "undo:" commit sha
    restored_from: str | None = None  # the older sha we checked out from
    paths: list[str] | None = None  # rel paths whose working tree changed


# ---------------------------------------------------------------------------
# Config helpers (read-only — toggling lives in enable/disable below)
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    try:
        from . import config_file
        return bool(config_file.load().vault.history.enabled)
    except Exception:
        return False


def _set_enabled(enabled: bool) -> None:
    from . import config_file
    cfg = config_file.load()
    cfg.vault.history.enabled = enabled
    config_file.save(cfg)


# ---------------------------------------------------------------------------
# Git plumbing
# ---------------------------------------------------------------------------

def _git_available() -> bool:
    return shutil.which("git") is not None


def _run_git(*args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    # Detach from any ambient GIT_* vars (e.g. user running nexus inside a git
    # repo could leak GIT_DIR / GIT_WORK_TREE) — we always want our own.
    for k in list(full_env.keys()):
        if k.startswith("GIT_"):
            full_env.pop(k, None)
    if env:
        full_env.update(env)
    full_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    cmd = [
        "git",
        f"--git-dir={_HISTORY_DIR}",
        f"--work-tree={_VAULT_ROOT}",
        *args,
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
        env=full_env,
    )


def _repo_exists() -> bool:
    return (_HISTORY_DIR / "HEAD").exists()


def _ensure_vault_root() -> None:
    _VAULT_ROOT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Enable / disable / status
# ---------------------------------------------------------------------------

def enable() -> dict[str, Any]:
    """Turn history on. Initializes the repo on first call. Idempotent."""
    if not _git_available():
        raise HistoryError("git is not on PATH; install git to enable vault history")
    _ensure_vault_root()
    if not _repo_exists():
        _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        # Initialize the repo with the vault as work-tree. Using `--git-dir` /
        # `--work-tree` *as command-level options* (before the subcommand) is
        # the supported way to set both at once for `git init`.
        _run_git("init", "--quiet")
        # local user.name/user.email so commits don't fall back to a global
        # config (which may not exist on a fresh host).
        _run_git("config", "user.name", "nexus")
        _run_git("config", "user.email", "nexus@localhost")
        _run_git("config", "commit.gpgsign", "false")
        # Initial bootstrap commit covers whatever is already in the vault.
        _run_git("add", "-A")
        # --allow-empty so even an empty vault gets a root commit; staged files
        # also get committed in the same call.
        _run_git(
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            _BOOTSTRAP_MSG,
        )
    _set_enabled(True)
    return status()


def disable() -> dict[str, Any]:
    _set_enabled(False)
    return status()


def status() -> dict[str, Any]:
    enabled = is_enabled()
    repo_exists = _repo_exists()
    commit_count = 0
    last_commit: dict[str, Any] | None = None
    if repo_exists and _git_available():
        try:
            res = _run_git("rev-list", "--count", "HEAD", check=False)
            if res.returncode == 0:
                commit_count = int(res.stdout.strip() or 0)
        except Exception:
            pass
        try:
            commits = log(limit=1)
            if commits:
                c = commits[0]
                last_commit = {
                    "sha": c.sha,
                    "timestamp": c.timestamp,
                    "message": c.message,
                    "action": c.action,
                }
        except Exception:
            pass
    return {
        "enabled": enabled,
        "repo_exists": repo_exists,
        "git_available": _git_available(),
        "commit_count": commit_count,
        "last_commit": last_commit,
    }


# ---------------------------------------------------------------------------
# Recording (called from vault.py after every successful mutation)
# ---------------------------------------------------------------------------

def record(paths: list[str], message: str) -> str | None:
    """Stage ``paths`` (or the whole tree if ``paths`` is empty) and commit.

    Called *after* the vault mutation succeeds. Returns the new commit SHA
    or ``None`` if disabled / nothing to commit. Never raises — history is a
    best-effort sidecar; failures are logged.
    """
    if not is_enabled() or not _repo_exists() or not _git_available():
        return None
    try:
        if paths:
            _run_git("add", "--", *paths)
        else:
            _run_git("add", "-A")
        diff = _run_git("diff", "--cached", "--quiet", check=False)
        if diff.returncode == 0:
            return None  # nothing staged
        _run_git("commit", "--quiet", "--allow-empty-message", "-m", message)
        head = _run_git("rev-parse", "HEAD")
        sha = head.stdout.strip()
        # any explicit write to a path resets that path's undo cursor
        if paths:
            _drop_cursors(paths)
        else:
            _clear_all_cursors()
        return sha
    except subprocess.CalledProcessError as exc:
        _log.warning("vault_history.record failed: %s", exc.stderr or exc, exc_info=False)
        return None
    except Exception:
        _log.warning("vault_history.record unexpected failure", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

def _classify(message: str) -> str:
    head = message.split(":", 1)[0].strip()
    if head in {"write", "delete", "move", "undo", "history"}:
        return head if head != "history" else "enable"
    return "other"


def log(path: str | None = None, limit: int = 100) -> list[Commit]:
    if not _repo_exists():
        return []
    args = ["log", f"-{limit}", "--format=%H%x09%ct%x09%s"]
    if path is not None:
        # safely resolve the path so a caller can't smuggle "../" arguments
        _safe_rel(path)
        args += ["--", path]
    res = _run_git(*args, check=False)
    if res.returncode != 0:
        return []
    out: list[Commit] = []
    for line in res.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        sha, ts, msg = parts
        try:
            ts_int = int(ts)
        except ValueError:
            continue
        out.append(Commit(sha=sha, timestamp=ts_int, message=msg, action=_classify(msg)))
    return out


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def _safe_rel(rel: str) -> str:
    """Reject paths that escape the vault root."""
    if not rel or rel.startswith("/"):
        raise ValueError(f"path must be vault-relative: {rel!r}")
    p = Path(rel)
    if any(part == ".." for part in p.parts):
        raise ValueError(f"path must not traverse upward: {rel!r}")
    return rel.rstrip("/")


def _real_commits_for(path: str) -> list[str]:
    """Return SHAs touching ``path``, newest first, excluding undo commits."""
    if not _repo_exists():
        return []
    res = _run_git("log", "--format=%H%x09%s", "--", path, check=False)
    if res.returncode != 0:
        return []
    shas: list[str] = []
    for line in res.stdout.splitlines():
        sha, _, msg = line.partition("\t")
        if not sha:
            continue
        if msg.startswith(_UNDO_PREFIX):
            continue
        shas.append(sha)
    return shas


def _read_cursors() -> dict[str, str]:
    if not _CURSORS_PATH.exists():
        return {}
    try:
        return json.loads(_CURSORS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cursors(d: dict[str, str]) -> None:
    _CURSORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CURSORS_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _drop_cursors(paths: list[str]) -> None:
    cursors = _read_cursors()
    changed = False
    for p in paths:
        # also drop any cursor for an ancestor folder, since that folder's
        # state has now changed under it.
        for key in list(cursors.keys()):
            if key == p or p.startswith(key.rstrip("/") + "/") or key.startswith(p.rstrip("/") + "/"):
                cursors.pop(key, None)
                changed = True
    if changed:
        _write_cursors(cursors)


def _clear_all_cursors() -> None:
    if _CURSORS_PATH.exists():
        try:
            _CURSORS_PATH.unlink()
        except OSError:
            pass


def _set_cursor(path: str, sha: str) -> None:
    cursors = _read_cursors()
    cursors[path] = sha
    _write_cursors(cursors)


def undo(path: str) -> UndoResult:
    """Step ``path`` back one real commit. See module docstring."""
    if not is_enabled():
        return UndoResult(undone=False, reason="disabled")
    if not _repo_exists():
        return UndoResult(undone=False, reason="no_repo")
    if not _git_available():
        return UndoResult(undone=False, reason="git_missing")
    rel = _safe_rel(path)
    real = _real_commits_for(rel)
    if not real:
        return UndoResult(undone=False, reason="no_history")

    cursors = _read_cursors()
    cursor_sha = cursors.get(rel)
    try:
        cur_idx = real.index(cursor_sha) if cursor_sha else 0
    except ValueError:
        cur_idx = 0
    target_idx = cur_idx + 1
    if target_idx >= len(real):
        return UndoResult(undone=False, reason="no_history")
    target_sha = real[target_idx]

    # Snapshot the working tree's state for the path before the checkout so
    # we can compute which files actually changed for the indexer.
    before = _list_tracked_under(rel)
    try:
        _run_git("checkout", target_sha, "--", rel)
    except subprocess.CalledProcessError as exc:
        # pathspec didn't exist at target_sha — that means we want to remove
        # the path (it didn't exist that far back). Use rm.
        if "did not match any file" in (exc.stderr or "") or "pathspec" in (exc.stderr or ""):
            try:
                _run_git("rm", "-rf", "--quiet", "--", rel)
            except subprocess.CalledProcessError:
                return UndoResult(undone=False, reason="checkout_failed")
        else:
            return UndoResult(undone=False, reason="checkout_failed")
    after = _list_tracked_under(rel)

    # commit the undo as a new commit so further undos keep stepping back.
    try:
        _run_git("add", "-A", "--", rel)
        diff = _run_git("diff", "--cached", "--quiet", check=False)
        new_commit: str | None = None
        if diff.returncode != 0:
            _run_git("commit", "--quiet", "-m", f"{_UNDO_PREFIX}{rel}")
            head = _run_git("rev-parse", "HEAD")
            new_commit = head.stdout.strip()
    except subprocess.CalledProcessError:
        return UndoResult(undone=False, reason="commit_failed")

    _set_cursor(rel, target_sha)

    # Compute touched paths (set difference + intersection with before/after).
    touched = sorted(before.symmetric_difference(after) | _changed_files(rel, target_sha))
    _reindex_after_undo(touched)
    return UndoResult(
        undone=True,
        commit=new_commit,
        restored_from=target_sha,
        paths=touched,
    )


def _reindex_after_undo(rel_paths: list[str]) -> None:
    """After a checkout-based undo, re-run vault indexing for changed paths.

    Imported lazily to avoid a circular import (vault.py calls into us, too).
    """
    if not rel_paths:
        return
    try:
        from . import vault
    except Exception:
        return
    removed: list[str] = []
    for rel in rel_paths:
        full = _VAULT_ROOT / rel
        if full.is_file():
            try:
                content = full.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                # binary file: skip text indexers but still invalidate the
                # graph cache via the standard remove path.
                continue
            try:
                vault._post_write_hooks(rel, content)
            except Exception:
                _log.warning("vault_history: post_write_hooks failed for %s", rel, exc_info=True)
        else:
            removed.append(rel)
    if removed:
        try:
            vault._post_remove_hooks(removed)
        except Exception:
            _log.warning("vault_history: post_remove_hooks failed", exc_info=True)


def _list_tracked_under(rel: str) -> set[str]:
    """Files currently in the working tree under rel (file or folder)."""
    full = _VAULT_ROOT / rel
    if full.is_file():
        return {rel}
    if full.is_dir():
        out: set[str] = set()
        for sub in full.rglob("*"):
            if sub.is_file():
                out.add(str(sub.relative_to(_VAULT_ROOT)))
        return out
    return set()


def _changed_files(rel: str, target_sha: str) -> set[str]:
    """Files whose blob differs between target_sha and HEAD under rel."""
    res = _run_git("diff", "--name-only", target_sha, "HEAD", "--", rel, check=False)
    if res.returncode != 0:
        return set()
    return {ln for ln in res.stdout.splitlines() if ln.strip()}


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------

def purge(before_iso: str | None = None) -> dict[str, Any]:
    """Run garbage collection. ``before_iso`` is reserved for future shallow
    truncation; v1 just runs ``git gc --prune=now`` to reclaim space."""
    if not _repo_exists():
        return {"ok": False, "reason": "no_repo"}
    if not _git_available():
        return {"ok": False, "reason": "git_missing"}
    try:
        _run_git("gc", "--prune=now", "--quiet")
        return {"ok": True}
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "reason": "gc_failed", "stderr": exc.stderr}
