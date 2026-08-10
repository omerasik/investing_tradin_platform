"""Verified SQLite backup and non-overwriting restore for local paper records."""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from .domain import utc_now


class BackupRecoveryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SQLiteBackupArtifact:
    backup_id: UUID
    source_path: str
    backup_path: str
    manifest_path: str
    sha256: str
    created_at: datetime


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class SQLiteBackupService:
    """Uses SQLite's online backup API and rejects in-place restore targets."""

    def create(self, source_path: str | Path, backup_directory: str | Path) -> SQLiteBackupArtifact:
        source = Path(source_path).resolve()
        destination_dir = Path(backup_directory).resolve()
        if not source.is_file():
            raise BackupRecoveryError("backup_source_must_be_existing_file")
        destination_dir.mkdir(parents=True, exist_ok=True)
        backup_id, created_at = uuid4(), utc_now()
        target = destination_dir / f"paper-backup-{created_at.strftime('%Y%m%dT%H%M%S%f')}-{backup_id}.sqlite"
        source_connection = sqlite3.connect(str(source))
        target_connection = sqlite3.connect(str(target))
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        self._assert_integrity(target)
        artifact = SQLiteBackupArtifact(backup_id, str(source), str(target), str(target.with_suffix(".manifest.json")), _sha256(target), created_at)
        manifest = asdict(artifact)
        manifest["backup_id"] = str(artifact.backup_id)
        manifest["created_at"] = artifact.created_at.isoformat()
        Path(artifact.manifest_path).write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        return artifact

    def verify(self, artifact: SQLiteBackupArtifact) -> None:
        target = Path(artifact.backup_path)
        if not target.is_file() or _sha256(target) != artifact.sha256:
            raise BackupRecoveryError("backup_checksum_mismatch")
        self._assert_integrity(target)

    def restore(self, artifact: SQLiteBackupArtifact, destination_path: str | Path) -> Path:
        self.verify(artifact)
        destination = Path(destination_path).resolve()
        if destination.exists():
            raise BackupRecoveryError("restore_destination_must_not_exist")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_connection = sqlite3.connect(artifact.backup_path)
        destination_connection = sqlite3.connect(str(destination))
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()
        self._assert_integrity(destination)
        return destination

    @staticmethod
    def _assert_integrity(path: Path) -> None:
        connection = sqlite3.connect(str(path))
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if result != ("ok",):
            raise BackupRecoveryError("sqlite_integrity_check_failed")
