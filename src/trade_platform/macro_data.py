"""Point-in-time macroeconomic release storage for leakage-safe research."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path


class MacroDataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MacroRelease:
    series_id: str
    observation_start: datetime
    observation_end: datetime
    released_at: datetime
    ingested_at: datetime
    actual: Decimal
    provider: str
    source_identifier: str
    revision: int = 0
    expected: Decimal | None = None
    prior: Decimal | None = None
    consensus_dispersion: Decimal | None = None
    data_version: str = ""

    def validate(self) -> None:
        timestamps = (self.observation_start, self.observation_end, self.released_at, self.ingested_at)
        if not self.series_id.strip() or not self.provider.strip() or not self.source_identifier.strip() or self.revision < 0:
            raise MacroDataError("invalid_macro_identity")
        if any(item.tzinfo is None for item in timestamps):
            raise MacroDataError("macro_timestamps_must_be_timezone_aware")
        if self.observation_end < self.observation_start:
            raise MacroDataError("invalid_observation_period")
        if self.released_at < self.observation_end or self.ingested_at < self.released_at:
            raise MacroDataError("invalid_macro_timestamp_semantics")
        if self.consensus_dispersion is not None and self.consensus_dispersion < 0:
            raise MacroDataError("invalid_consensus_dispersion")


class SQLiteMacroReleaseStore:
    """Append-only release history; queries select only values known at decision time."""

    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS macro_releases (
                series_id TEXT NOT NULL, observation_start TEXT NOT NULL, observation_end TEXT NOT NULL,
                released_at TEXT NOT NULL, ingested_at TEXT NOT NULL, actual TEXT NOT NULL,
                provider TEXT NOT NULL, source_identifier TEXT NOT NULL, revision INTEGER NOT NULL,
                expected TEXT, prior_value TEXT, consensus_dispersion TEXT, data_version TEXT NOT NULL,
                PRIMARY KEY (provider, source_identifier, revision)
            )
            """
        )
        self._connection.commit()

    def ingest(self, releases: list[MacroRelease]) -> None:
        if not releases:
            raise MacroDataError("empty_macro_batch")
        for release in releases:
            release.validate()
            try:
                self._connection.execute(
                    "INSERT INTO macro_releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        release.series_id, release.observation_start.isoformat(), release.observation_end.isoformat(),
                        release.released_at.isoformat(), release.ingested_at.isoformat(), str(release.actual), release.provider,
                        release.source_identifier, release.revision, self._decimal_or_none(release.expected),
                        self._decimal_or_none(release.prior), self._decimal_or_none(release.consensus_dispersion), release.data_version,
                    ),
                )
            except sqlite3.IntegrityError as error:
                self._connection.rollback()
                raise MacroDataError("duplicate_macro_release_revision") from error
        self._connection.commit()

    def available_as_of(self, series_id: str, decision_at: datetime) -> list[MacroRelease]:
        if not series_id.strip() or decision_at.tzinfo is None:
            raise MacroDataError("invalid_macro_as_of_query")
        rows = self._connection.execute(
            """
            SELECT series_id, observation_start, observation_end, released_at, ingested_at, actual,
                   provider, source_identifier, revision, expected, prior_value, consensus_dispersion, data_version
            FROM macro_releases
            WHERE series_id = ? AND released_at <= ? AND ingested_at <= ?
            ORDER BY observation_end, released_at DESC, revision DESC, ingested_at DESC
            """,
            (series_id, decision_at.isoformat(), decision_at.isoformat()),
        ).fetchall()
        latest_by_observation: dict[tuple[str, str], MacroRelease] = {}
        for row in rows:
            release = self._from_row(row)
            latest_by_observation.setdefault((row[1], row[2]), release)
        return list(latest_by_observation.values())

    def history(self, series_id: str, observation_end: datetime) -> list[MacroRelease]:
        if observation_end.tzinfo is None:
            raise MacroDataError("macro_timestamps_must_be_timezone_aware")
        rows = self._connection.execute(
            "SELECT series_id, observation_start, observation_end, released_at, ingested_at, actual, provider, source_identifier, revision, expected, prior_value, consensus_dispersion, data_version FROM macro_releases WHERE series_id = ? AND observation_end = ? ORDER BY released_at, revision",
            (series_id, observation_end.isoformat()),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _decimal_or_none(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _from_row(row: tuple[object, ...]) -> MacroRelease:
        return MacroRelease(
            str(row[0]), datetime.fromisoformat(str(row[1])), datetime.fromisoformat(str(row[2])),
            datetime.fromisoformat(str(row[3])), datetime.fromisoformat(str(row[4])), Decimal(str(row[5])),
            str(row[6]), str(row[7]), int(row[8]),
            None if row[9] is None else Decimal(str(row[9])), None if row[10] is None else Decimal(str(row[10])),
            None if row[11] is None else Decimal(str(row[11])), str(row[12]),
        )
