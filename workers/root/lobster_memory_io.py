"""
Shared safe-write helper for /root/lobster-memory/*.json files.

Why this exists: 30+ worker scripts concurrently rewrite the same persona JSON
files. Path.write_text() truncates first then writes, so any crash mid-write
leaves a half-written or empty file (the clawtrix 2026-05-24 truncation incident).
Adding cross-worker locking is the other half: without it, two workers can each
read the file, modify their own region, and the slower writer overwrites the
faster writer's section.

Usage:
    from lobster_memory_io import safe_write_json, safe_read_modify_write
    safe_write_json(path, data)                          # overwrite (with lock)
    safe_read_modify_write(path, fn)                     # read → fn(d) → write
"""
from __future__ import annotations
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Any

LOCK_DIR = "/var/lock/lobster-memory"
os.makedirs(LOCK_DIR, exist_ok=True)


def _lock_path(target: Path) -> str:
    # one lock file per persona JSON, kept out of the data dir
    return os.path.join(LOCK_DIR, target.name + ".lock")


def safe_write_json(path, data, *, indent: int = 2) -> None:
    """Atomically write JSON to `path` under an exclusive file lock.

    1. Acquire LOCK_EX on a sidecar lock file (so the lock survives os.replace).
    2. Write JSON to a tmp file in the same dir (same filesystem → rename is atomic).
    3. fsync the tmp file.
    4. os.replace(tmp, path) — POSIX atomic rename.
    5. Release lock.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_p = _lock_path(p)
    payload = json.dumps(data, ensure_ascii=False, indent=indent)
    with open(lock_p, "a+") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=p.name + ".", suffix=".tmp", dir=str(p.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, p)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
                raise
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def safe_read_modify_write(
    path, mutate_fn: Callable[[dict], Any], *, indent: int = 2
) -> dict:
    """Read JSON, call mutate_fn(data), write atomically — all under one lock.

    Use this for any worker that does the read-modify-write pattern. It guarantees
    no other locked writer can interleave between the read and the write.
    """
    p = Path(path)
    lock_p = _lock_path(p)
    with open(lock_p, "a+") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            mutate_fn(data)
            payload = json.dumps(data, ensure_ascii=False, indent=indent)
            fd, tmp_name = tempfile.mkstemp(
                prefix=p.name + ".", suffix=".tmp", dir=str(p.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, p)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
                raise
            return data
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
