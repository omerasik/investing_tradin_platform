"""Production secret-management boundary: a real authority beyond raw environment variables.

Development, local and paper runtimes may keep resolving secrets from process
environment variables (:class:`EnvironmentSecretProvider`) exactly as before Module 3D.
Protected production composition instead requires :class:`FileSecretProvider`, which
reads each secret from its own file under a deployment-provisioned secrets directory --
the on-disk contract every mainstream secret manager's sync tooling already speaks
(Kubernetes ``Secret``/CSI-driver volumes, Vault Agent, AWS/GCP secret-store CSI
drivers, Docker/Swarm/Nomad secrets under ``/run/secrets``). This module never talks to
a specific vendor API; it defines the boundary a real secret manager is expected to
populate onto disk via its own injector, and it enforces that production never falls
back to reading security-critical secret material from a raw environment variable.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

__all__ = [
    "EnvironmentSecretProvider",
    "FileSecretProvider",
    "SecretProvider",
    "SecretUnavailableError",
]


class SecretUnavailableError(RuntimeError):
    """A required secret could not be resolved from the configured authority.

    Never includes the attempted secret value; only the secret name and a fixed
    machine-readable reason, matching the fail-closed, non-leaking pattern used by
    :class:`trade_platform.config.SecretReferenceError`.
    """


class SecretProvider(Protocol):
    """Deployment-owned secret resolution boundary; never logs or caches plaintext beyond the call."""

    @property
    def is_production_capable(self) -> bool: ...

    def get_secret(self, name: str) -> str: ...


def _validate_name(name: str) -> None:
    if not name or name != name.strip() or not all(character.isalnum() or character in "_-" for character in name):
        raise SecretUnavailableError("invalid_secret_name")


@dataclass(frozen=True, slots=True)
class EnvironmentSecretProvider:
    """Development/paper-only: resolves ``<prefix><NAME>`` from the process environment.

    Explicitly **not** production-capable: raw process environment variables are
    visible to anything that can read ``/proc/<pid>/environ`` or a crash dump, are
    trivially inherited by child processes and leaked into logs, and carry no
    rotation, per-secret access audit, or least-privilege boundary. Kept only so
    local/dev/paper flows keep working exactly as they did before Module 3D --
    :func:`trade_platform.runtime_app.compose_protected_postgres_app` refuses to
    accept this provider for ``production``.
    """

    prefix: str = "TRADE_PLATFORM_SECRET_"

    @property
    def is_production_capable(self) -> bool:
        return False

    def get_secret(self, name: str) -> str:
        _validate_name(name)
        value = os.environ.get(f"{self.prefix}{name}")
        if not value:
            raise SecretUnavailableError(f"secret_unavailable:{name}")
        return value


@dataclass(frozen=True, slots=True)
class FileSecretProvider:
    """Production-capable: one file per secret under a deployment-provisioned directory.

    Fails closed if the directory is missing, a secret file is missing or empty, or
    (on POSIX) the file's permissions are group/world readable or writable. This
    provider's job is to enforce that boundary -- never to fetch a vendor API itself;
    an orchestrator's secret-manager sync tooling (Vault Agent, a CSI driver, a
    Kubernetes ``Secret`` volume, etc.) is responsible for populating and rotating the
    files underneath it.
    """

    directory: Path
    _resolved_directory: Path = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        resolved = self.directory.resolve()
        if not resolved.is_dir():
            raise SecretUnavailableError(f"secret_directory_missing:{self.directory}")
        object.__setattr__(self, "_resolved_directory", resolved)

    @property
    def is_production_capable(self) -> bool:
        return True

    def get_secret(self, name: str) -> str:
        _validate_name(name)
        path = self._secret_path(name)
        value = self._read_and_check(path, name)
        if not value:
            raise SecretUnavailableError(f"secret_file_empty:{name}")
        return value

    def rotation_timestamp(self, name: str) -> float:
        """File modification time, exposed so callers can detect and react to rotation."""
        _validate_name(name)
        path = self._secret_path(name)
        try:
            return path.stat().st_mtime
        except OSError as error:
            raise SecretUnavailableError(f"secret_file_missing:{name}") from error

    def _secret_path(self, name: str) -> Path:
        return self._resolved_directory / name

    @staticmethod
    def _read_and_check(path: Path, name: str) -> str:
        try:
            file_stat = path.stat()
        except OSError as error:
            raise SecretUnavailableError(f"secret_file_missing:{name}") from error
        if os.name == "posix" and file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SecretUnavailableError(f"secret_file_permissions_too_open:{name}")
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise SecretUnavailableError(f"secret_file_unreadable:{name}") from error
