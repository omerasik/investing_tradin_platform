"""Authorized point-in-time macro releases with revision-safe reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid4

from .persistence import PostgresDatabase


class PitMacroError(ValueError):
    pass


class MacroCategory(StrEnum):
    POLICY_RATE = "POLICY_RATE"
    CPI = "CPI"
    EMPLOYMENT = "EMPLOYMENT"
    GDP = "GDP"
    YIELD_CURVE = "YIELD_CURVE"
    LIQUIDITY_CREDIT = "LIQUIDITY_CREDIT"


@dataclass(frozen=True, slots=True)
class AuthorizedMacroSource:
    provider: str
    terms_version: str
    authorization_reference: str
    authorized_at: datetime
    created_at: datetime
    source_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        if not all(
            value.strip()
            for value in (self.provider, self.terms_version, self.authorization_reference)
        ):
            raise PitMacroError("macro_source_authorization_required")
        _aware(self.authorized_at)
        _aware(self.created_at)
        if self.created_at < self.authorized_at:
            raise PitMacroError("macro_source_created_before_authorization")


@dataclass(frozen=True, slots=True)
class PitMacroObservation:
    source_id: UUID
    category: MacroCategory
    series_code: str
    observation_period: date
    initial_release_at: datetime
    release_at: datetime
    actual: Decimal | None
    expected: Decimal | None
    prior: Decimal | None
    revision: int
    ingested_at: datetime
    provenance_uri: str
    raw_payload_sha256: str
    observation_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        if (
            not self.series_code.strip()
            or not self.provenance_uri.strip()
            or len(self.raw_payload_sha256) != 64
        ):
            raise PitMacroError("invalid_macro_identity")
        for timestamp in (self.initial_release_at, self.release_at, self.ingested_at):
            _aware(timestamp)
        if (
            self.revision < 0
            or self.release_at < self.initial_release_at
            or self.ingested_at < self.release_at
        ):
            raise PitMacroError("invalid_macro_revision_timestamps")
        if self.revision == 0 and self.release_at != self.initial_release_at:
            raise PitMacroError("initial_release_timestamp_mismatch")


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PitMacroError("macro_timestamp_must_be_timezone_aware")


class PostgresPitMacroStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register_source(self, source: AuthorizedMacroSource) -> None:
        source.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO pit_macro_sources VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        source.source_id,
                        source.provider,
                        source.terms_version,
                        source.authorization_reference,
                        source.authorized_at,
                        source.created_at,
                    ),
                )
        except Exception as error:
            raise PitMacroError("macro_source_registration_failed") from error

    def ingest(self, observations: tuple[PitMacroObservation, ...]) -> None:
        if not observations:
            raise PitMacroError("empty_macro_batch")
        for item in observations:
            item.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                for item in observations:
                    cursor.execute(
                        "INSERT INTO pit_macro_observations VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            item.observation_id,
                            item.source_id,
                            item.category.value,
                            item.series_code,
                            item.observation_period,
                            item.initial_release_at,
                            item.release_at,
                            item.actual,
                            item.expected,
                            item.prior,
                            item.revision,
                            item.ingested_at,
                            item.provenance_uri,
                            item.raw_payload_sha256,
                        ),
                    )
        except Exception as error:
            raise PitMacroError("macro_ingestion_failed") from error

    def available_as_of(self, series_code: str, as_of: datetime) -> tuple[PitMacroObservation, ...]:
        _aware(as_of)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM (SELECT o.*,ROW_NUMBER() OVER (PARTITION BY source_id,series_code,observation_period ORDER BY revision DESC,release_at DESC,ingested_at DESC) rank FROM pit_macro_observations o WHERE series_code=%s AND release_at<=%s AND ingested_at<=%s) x WHERE rank=1 ORDER BY observation_period",
                (series_code, as_of, as_of),
            )
            rows = cursor.fetchall()
        return tuple(
            PitMacroObservation(
                UUID(str(r[1])),
                MacroCategory(str(r[2])),
                str(r[3]),
                cast(date, r[4]),
                cast(datetime, r[5]),
                cast(datetime, r[6]),
                None if r[7] is None else Decimal(str(r[7])),
                None if r[8] is None else Decimal(str(r[8])),
                None if r[9] is None else Decimal(str(r[9])),
                int(str(r[10])),
                cast(datetime, r[11]),
                str(r[12]),
                str(r[13]),
                UUID(str(r[0])),
            )
            for r in rows
        )
