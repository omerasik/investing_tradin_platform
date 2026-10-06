"""Single-instance guard for long-running recorders (R0/R1A operations).

An exclusive, operating-system-held lock on ``<directory>/.<name>.lock``
(``msvcrt.locking`` on Windows, ``fcntl.flock`` elsewhere). The operating
system releases it when the holding process exits for any reason -- crash,
kill, shutdown -- so there is never a stale lock to clear by hand, and a
second recorder on the same archive root is refused instead of interleaving
writes. An informational ``.<name>.owner.json`` (pid, start time, command)
is written beside it for operators; it is never what decides ownership.

This changes no evidence semantics: it writes nothing under any partition and
the archive scanners only read below the layout directory.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any


class InstanceLockHeldError(RuntimeError):
    """Raised when another live process already holds the lock."""


@dataclass(frozen=True, slots=True)
class InstanceLockStatusV1:
    held: bool
    owner: Mapping[str, Any] | None


def _paths(directory: Path, name: str) -> tuple[Path, Path]:
    if not name or not name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("lock_name_invalid")
    return directory / f".{name}.lock", directory / f".{name}.owner.json"


def _try_lock(handle: IO[bytes]) -> bool:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_owner(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


@contextmanager
def exclusive_instance_lock_v1(
    directory: Path, name: str, *, description: str = ""
) -> Iterator[Mapping[str, Any]]:
    """Hold the lock for the ``with`` body or raise :class:`InstanceLockHeldError`."""
    lock_path, owner_path = _paths(directory, name)
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")  # noqa: SIM115 - held open for the lock's lifetime
    try:
        if not _try_lock(handle):
            owner = _read_owner(owner_path)
            detail = "" if owner is None else f" (pid {owner.get('pid')}, since {owner.get('started_at')})"
            raise InstanceLockHeldError(f"{name} already running for {directory}{detail}")
        owner = {
            "pid": os.getpid(),
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "description": description,
        }
        owner_path.write_text(json.dumps(owner, sort_keys=True), encoding="utf-8")
        try:
            yield owner
        finally:
            if (_read_owner(owner_path) or {}).get("pid") == owner["pid"]:
                owner_path.unlink(missing_ok=True)
            _unlock(handle)
    finally:
        handle.close()


def instance_lock_status_v1(directory: Path, name: str) -> InstanceLockStatusV1:
    """Whether a live process holds the lock now (a probe: acquire and release at once)."""
    lock_path, owner_path = _paths(directory, name)
    if not lock_path.exists():
        return InstanceLockStatusV1(held=False, owner=None)
    with open(lock_path, "a+b") as handle:
        if _try_lock(handle):
            _unlock(handle)
            return InstanceLockStatusV1(held=False, owner=None)
    return InstanceLockStatusV1(held=True, owner=_read_owner(owner_path))


__all__ = [
    "InstanceLockHeldError",
    "InstanceLockStatusV1",
    "exclusive_instance_lock_v1",
    "instance_lock_status_v1",
]
