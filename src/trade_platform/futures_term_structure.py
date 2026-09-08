"""Deterministic, point-in-time futures settlement term-structure derivation.

Module 3I.3 (roadmap NEXT-03 phase 3). **FUTURES ONLY.** Crypto dated-future
term structure is explicitly out of scope: crypto instruments currently have
``MARK_PRICE`` and ``INDEX_PRICE`` but no first-class exchange settlement-price
authority, so a curve built from mark/index would be a different semantic
artifact than an exchange settlement curve and must never be silently treated
as equivalent to one. Crypto term structure remains a later, separately
reviewed module.

**A term structure is derived evidence, not raw market data.** This module
introduces no new ``ObservationKind``, writes nothing into
``historical_raw_observations``, creates no second market-data source registry,
and never mutates or reinterprets the canonical ``SETTLEMENT_PRICE``
observations Module 3I.1 already sealed. The dependency chain is:

    AuthorizedHistoricalSource -> RawHistoricalObservation -> SETTLEMENT_PRICE
        -> sealed HistoricalDatasetVersion -> Term Structure Derivation

**Point-in-time semantics are exact.** A derivation's inputs are exactly one
sealed dataset version, one method version, one futures series, one ``as_of``
and one ``knowledge_at``. A settlement observation participates only if it
belongs to the sealed dataset, its contract belongs to the requested series,
its own two clocks (``event_at``, ``ingested_at``) and its normalization clock
were knowable at ``knowledge_at``, and the dataset itself was sealed at or
before ``knowledge_at``. Nothing here ever queries "latest known today" while
deriving a historical curve, so a later final settlement can never leak into a
curve whose knowledge time predates it -- and because curves are immutable and
keyed by their own ``knowledge_at``, a later final settlement cannot mutate an
earlier preliminary curve; it can only produce a new one.

**No fallback, no interpolation.** The only observation kind this module ever
reads is ``SETTLEMENT_PRICE`` -- never OHLCV close, last trade, mark, index or
a stale prior value. A contract with no eligible settlement for the requested
snapshot is simply absent from the curve; there is no synthetic point, no
interpolation and no extrapolation anywhere in this module. Every point
resolves to a real contract registered with the Module 3H.1 futures-contract
authority (:mod:`trade_platform.futures_contracts`), ordered by that
authority's own expiration/maturity identity -- never by symbol text.

**Classification and carry are declared, never implicit.** ``CONTANGO`` /
``BACKWARDATION`` / ``FLAT`` labels are emitted only when the method that
produced the curve explicitly enables classification with a minimum point
count and a numeric threshold; the schema makes any other combination
impossible (see the migration). Carry, roll-yield and annualized metrics are
deliberately **not computed** by this module -- the method registry only
records a day-count convention for forward compatibility with a later,
separately reviewed derivation. See the module docstring in the migration for
the full schema rationale.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import cast
from uuid import UUID, uuid4

from .domain import AssetClass
from .futures_contracts import PostgresFuturesContractAuthority
from .futures_market_observations import SettlementFinality
from .persistence import PostgresDatabase

#: The only observation kind base 3I.3 may ever consume. Not configurable per
#: method: widening this would let some other price stand in for an exchange
#: settlement price, which is exactly what this module exists to refuse.
ALLOWED_OBSERVATION_KIND = "SETTLEMENT_PRICE"


class FuturesTermStructureError(ValueError):
    """Base class: every failure path in this module is fail-closed."""


class FuturesTermStructureMethodError(FuturesTermStructureError):
    pass


class FuturesTermStructureDerivationError(FuturesTermStructureError):
    pass


class SettlementFinalityPolicy(StrEnum):
    FINAL_ONLY = "FINAL_ONLY"
    LATEST_KNOWN_ALLOW_PRELIMINARY = "LATEST_KNOWN_ALLOW_PRELIMINARY"


class SessionPolicy(StrEnum):
    SAME_SESSION_STRICT = "SAME_SESSION_STRICT"
    SAME_SESSION_WITH_STALENESS_TOLERANCE = "SAME_SESSION_WITH_STALENESS_TOLERANCE"


class DayCountConvention(StrEnum):
    ACT_365F = "ACT_365F"
    ACT_360 = "ACT_360"


class CurveClassification(StrEnum):
    CONTANGO = "CONTANGO"
    BACKWARDATION = "BACKWARDATION"
    FLAT = "FLAT"


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise FuturesTermStructureError(f"invalid_{name}")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FuturesTermStructureError(f"{name}_must_be_timezone_aware")


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def classify_curve(
    ordered_prices: tuple[Decimal, ...], threshold: Decimal
) -> CurveClassification:
    """Pure, deterministic front/back classification. No ML, no implicit epsilon.

    ``ordered_prices`` must already be ordered front-to-back by contract
    maturity. The comparison is a declared relative threshold against the
    front-month price, never a bare inequality, so a noisy two-point curve
    cannot be classified without an explicit tolerance.
    """
    if len(ordered_prices) < 2:
        raise FuturesTermStructureError("classification_requires_at_least_two_points")
    if threshold <= 0:
        raise FuturesTermStructureError("classification_requires_positive_threshold")
    front, back = ordered_prices[0], ordered_prices[-1]
    if back > front * (1 + threshold):
        return CurveClassification.CONTANGO
    if back < front * (1 - threshold):
        return CurveClassification.BACKWARDATION
    return CurveClassification.FLAT


@dataclass(frozen=True, slots=True)
class FuturesTermStructureMethod:
    """A versioned, content-hashed derivation method. Never edited in place."""

    method_name: str
    method_version: int
    minimum_point_count: int
    settlement_finality_policy: SettlementFinalityPolicy
    session_policy: SessionPolicy
    classification_enabled: bool
    carry_enabled: bool
    effective_from: datetime
    known_at: datetime
    max_staleness_days: int | None = None
    stale_points_flagged: bool = False
    classification_permitted_with_stale_points: bool = False
    classification_minimum_point_count: int | None = None
    classification_flat_threshold: Decimal | None = None
    day_count_convention: DayCountConvention | None = None
    method_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        _require_text(self.method_name, "method_name")
        if self.method_version < 1:
            raise FuturesTermStructureMethodError("invalid_method_version")
        if self.minimum_point_count < 1:
            raise FuturesTermStructureMethodError("invalid_minimum_point_count")
        _require_aware(self.effective_from, "effective_from")
        _require_aware(self.known_at, "known_at")

        if self.session_policy is SessionPolicy.SAME_SESSION_STRICT:
            if (
                self.max_staleness_days is not None
                or self.stale_points_flagged
                or self.classification_permitted_with_stale_points
            ):
                raise FuturesTermStructureMethodError(
                    "staleness_fields_not_applicable_to_strict_session_policy"
                )
        else:
            if self.max_staleness_days is None or self.max_staleness_days <= 0:
                raise FuturesTermStructureMethodError(
                    "staleness_tolerance_requires_positive_max_staleness_days"
                )

        if self.classification_enabled:
            if (
                self.classification_minimum_point_count is None
                or self.classification_minimum_point_count < 2
            ):
                raise FuturesTermStructureMethodError(
                    "classification_requires_minimum_point_count_of_at_least_two"
                )
            if (
                self.classification_flat_threshold is None
                or self.classification_flat_threshold <= 0
            ):
                raise FuturesTermStructureMethodError(
                    "classification_requires_positive_flat_threshold"
                )
        else:
            if (
                self.classification_minimum_point_count is not None
                or self.classification_flat_threshold is not None
            ):
                raise FuturesTermStructureMethodError(
                    "classification_fields_require_classification_enabled"
                )

        if self.carry_enabled:
            if self.day_count_convention is None:
                raise FuturesTermStructureMethodError(
                    "carry_requires_explicit_day_count_convention"
                )
        elif self.day_count_convention is not None:
            raise FuturesTermStructureMethodError(
                "day_count_convention_requires_carry_enabled"
            )

    def _definition_payload(self) -> dict[str, object]:
        return {
            "method_name": self.method_name,
            "method_version": self.method_version,
            "allowed_observation_kind": ALLOWED_OBSERVATION_KIND,
            "minimum_point_count": self.minimum_point_count,
            "settlement_finality_policy": self.settlement_finality_policy.value,
            "session_policy": self.session_policy.value,
            "max_staleness_days": self.max_staleness_days,
            "stale_points_flagged": self.stale_points_flagged,
            "classification_permitted_with_stale_points": (
                self.classification_permitted_with_stale_points
            ),
            "classification_enabled": self.classification_enabled,
            "classification_minimum_point_count": self.classification_minimum_point_count,
            "classification_flat_threshold": (
                None
                if self.classification_flat_threshold is None
                # Quantized to the NUMERIC(10,6) column's declared scale so a
                # value round-tripped through Postgres (which pads to 6 decimal
                # places) hashes identically to the one originally constructed
                # in Python -- otherwise a freshly derived method and one
                # reloaded from storage would disagree on their own identity.
                else str(self.classification_flat_threshold.quantize(Decimal("0.000001")))
            ),
            "carry_enabled": self.carry_enabled,
            "day_count_convention": (
                None if self.day_count_convention is None else self.day_count_convention.value
            ),
            "effective_from": self.effective_from.isoformat(),
        }

    def content_hash(self) -> str:
        return hashlib.sha256(
            _canonical_json(self._definition_payload()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class FuturesTermStructurePoint:
    """One real 3H.1 contract's settlement price on a derived curve."""

    curve_id: UUID
    sequence: int
    instrument_id: str
    contract_expiration_date: date
    settlement_session_date: date
    is_stale: bool
    settlement_price: Decimal
    settlement_finality: SettlementFinality
    provider_revision: int
    normalized_observation_id: UUID
    time_to_expiry_days: int
    point_hash: str


@dataclass(frozen=True, slots=True)
class FuturesTermStructureCurve:
    """One immutable derived curve instance. Re-derivation is idempotent, not additive."""

    curve_id: UUID
    series_id: str
    dataset_version_id: UUID
    method_id: UUID
    as_of: date
    knowledge_at: datetime
    quote_currency: str
    quote_unit: str
    point_count: int
    contains_preliminary_point: bool
    classification: CurveClassification | None
    content_hash: str
    derived_at: datetime


class PostgresFuturesTermStructureAuthority:
    """Derives and persists immutable term-structure curves from sealed evidence."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._contracts = PostgresFuturesContractAuthority(database)

    # ---- methods ----------------------------------------------------------

    def register_method(self, method: FuturesTermStructureMethod) -> None:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO futures_term_structure_methods VALUES ("  # nosec B608
                    + ",".join(["%s"] * 20) + ")",
                    (
                        method.method_id, method.method_name, method.method_version,
                        ALLOWED_OBSERVATION_KIND, method.minimum_point_count,
                        method.settlement_finality_policy.value, method.session_policy.value,
                        method.max_staleness_days, method.stale_points_flagged,
                        method.classification_permitted_with_stale_points,
                        method.classification_enabled, method.classification_minimum_point_count,
                        method.classification_flat_threshold, method.carry_enabled,
                        None if method.day_count_convention is None
                        else method.day_count_convention.value,
                        json.dumps(method._definition_payload(), sort_keys=True),
                        method.effective_from, method.known_at, method.content_hash(),
                        method.created_at,
                    ),
                )
        except Exception as error:
            raise FuturesTermStructureMethodError(
                "term_structure_method_duplicate_or_invalid"
            ) from error

    def get_method(self, method_id: UUID) -> FuturesTermStructureMethod:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT method_id,method_name,method_version,minimum_point_count,"
                    "settlement_finality_policy,session_policy,max_staleness_days,"
                    "stale_points_flagged,classification_permitted_with_stale_points,"
                    "classification_enabled,classification_minimum_point_count,"
                    "classification_flat_threshold,carry_enabled,day_count_convention,"
                    "effective_from,known_at,content_hash,created_at "
                    "FROM futures_term_structure_methods WHERE method_id=%s",
                    (method_id,),
                )
                row = cursor.fetchone()
        except Exception as error:
            raise FuturesTermStructureMethodError("term_structure_method_read_failed") from error
        if row is None:
            raise FuturesTermStructureMethodError(f"term_structure_method_not_found:{method_id}")
        method = FuturesTermStructureMethod(
            method_name=str(row[1]), method_version=int(str(row[2])),
            minimum_point_count=int(str(row[3])),
            settlement_finality_policy=SettlementFinalityPolicy(str(row[4])),
            session_policy=SessionPolicy(str(row[5])),
            classification_enabled=bool(row[9]), carry_enabled=bool(row[12]),
            effective_from=cast(datetime, row[14]), known_at=cast(datetime, row[15]),
            max_staleness_days=None if row[6] is None else int(str(row[6])),
            stale_points_flagged=bool(row[7]),
            classification_permitted_with_stale_points=bool(row[8]),
            classification_minimum_point_count=None if row[10] is None else int(str(row[10])),
            classification_flat_threshold=None if row[11] is None else Decimal(str(row[11])),
            day_count_convention=None if row[13] is None else DayCountConvention(str(row[13])),
            method_id=cast(UUID, row[0]), created_at=cast(datetime, row[17]),
        )
        if method.content_hash() != str(row[16]):
            raise FuturesTermStructureMethodError("term_structure_method_hash_mismatch")
        return method

    # ---- curves -------------------------------------------------------------

    def derive_curve(
        self,
        *,
        series_id: str,
        dataset_version_id: UUID,
        method_id: UUID,
        as_of: date,
        knowledge_at: datetime,
        derived_at: datetime | None = None,
    ) -> tuple[FuturesTermStructureCurve, tuple[FuturesTermStructurePoint, ...]]:
        """Derive (or idempotently re-fetch) one PIT curve. Inputs fully determine identity."""
        _require_aware(knowledge_at, "knowledge_at")
        resolved_derived_at = knowledge_at if derived_at is None else derived_at
        _require_aware(resolved_derived_at, "derived_at")

        method = self.get_method(method_id)
        if method.known_at > knowledge_at:
            raise FuturesTermStructureDerivationError("method_not_known_at_knowledge_at")

        series = self._contracts.get_series(series_id)
        if series.asset_class is AssetClass.CRYPTO:
            raise FuturesTermStructureDerivationError(
                "crypto_dated_future_not_eligible_for_futures_term_structure"
            )

        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, created_at, content_hash FROM historical_dataset_versions "
                    "WHERE dataset_version_id=%s",
                    (dataset_version_id,),
                )
                dataset_row = cursor.fetchone()
        except Exception as error:
            raise FuturesTermStructureDerivationError("dataset_version_read_failed") from error
        if dataset_row is None:
            raise FuturesTermStructureDerivationError(f"dataset_not_found:{dataset_version_id}")
        if str(dataset_row[0]) != "SEALED":
            raise FuturesTermStructureDerivationError("dataset_not_sealed")
        dataset_created_at = cast(datetime, dataset_row[1])
        dataset_content_hash = str(dataset_row[2])
        if dataset_created_at > knowledge_at:
            raise FuturesTermStructureDerivationError("dataset_not_known_at_knowledge_at")

        if method.session_policy is SessionPolicy.SAME_SESSION_STRICT:
            window_start = as_of
        else:
            window_start = as_of - timedelta(days=cast(int, method.max_staleness_days))

        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT n.normalized_observation_id, r.revision, "
                    "f.settlement_price, f.price_currency, f.settlement_date, f.finality, "
                    "f.quote_unit, c.instrument_id, c.expiration_date "
                    "FROM historical_dataset_members m "
                    "JOIN historical_normalized_observations n "
                    "  ON n.normalized_observation_id=m.normalized_observation_id "
                    "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
                    "JOIN futures_settlement_observations f "
                    "  ON f.normalized_observation_id=n.normalized_observation_id "
                    "JOIN futures_contract_specifications c ON c.instrument_id=n.instrument_id "
                    "WHERE m.dataset_version_id=%s AND c.series_id=%s "
                    "AND r.observation_kind='SETTLEMENT_PRICE' AND n.quality_status='VALIDATED' "
                    "AND n.normalized_at<=%s AND r.event_at<=%s AND r.ingested_at<=%s "
                    "AND f.settlement_date BETWEEN %s AND %s "
                    "ORDER BY c.instrument_id, f.settlement_date DESC, r.revision DESC, "
                    "r.ingested_at DESC",  # nosec B608 - fixed fragments and placeholders only
                    (
                        dataset_version_id, series_id, knowledge_at, knowledge_at, knowledge_at,
                        window_start, as_of,
                    ),
                )
                rows = cursor.fetchall()
        except Exception as error:
            raise FuturesTermStructureDerivationError(
                "term_structure_candidate_read_failed"
            ) from error

        best_by_instrument: dict[str, tuple[object, ...]] = {}
        for row in rows:
            instrument_id = str(row[7])
            best_by_instrument.setdefault(instrument_id, row)

        selected: list[tuple[object, ...]] = []
        for row in best_by_instrument.values():
            expiration_date = cast(date, row[8])
            if expiration_date < as_of:
                # Already expired before this curve's own snapshot date -- not
                # a live term-structure leg, and never silently included.
                continue
            finality = SettlementFinality(str(row[5]))
            if (
                method.settlement_finality_policy is SettlementFinalityPolicy.FINAL_ONLY
                and finality is not SettlementFinality.FINAL
            ):
                continue
            selected.append(row)

        if not selected:
            raise FuturesTermStructureDerivationError("no_eligible_settlement_points")

        currencies = {str(row[3]) for row in selected}
        quote_units = {str(row[6]) for row in selected}
        if len(currencies) > 1:
            raise FuturesTermStructureDerivationError("mixed_quote_currency_in_curve")
        if len(quote_units) > 1:
            raise FuturesTermStructureDerivationError("mixed_quote_unit_in_curve")

        if len(selected) < method.minimum_point_count:
            raise FuturesTermStructureDerivationError(
                f"insufficient_point_count:{len(selected)}<{method.minimum_point_count}"
            )

        ordered_rows = sorted(selected, key=lambda row: (cast(date, row[8]), str(row[7])))

        curve_id = uuid4()
        points: list[FuturesTermStructurePoint] = []
        for sequence, row in enumerate(ordered_rows, start=1):
            normalized_observation_id = cast(UUID, row[0])
            revision = int(str(row[1]))
            settlement_price = Decimal(str(row[2]))
            settlement_session_date = cast(date, row[4])
            finality = SettlementFinality(str(row[5]))
            instrument_id = str(row[7])
            expiration_date = cast(date, row[8])
            is_stale = settlement_session_date != as_of
            time_to_expiry_days = (expiration_date - as_of).days
            point_hash = hashlib.sha256(
                "|".join(
                    (
                        str(sequence), instrument_id, str(normalized_observation_id),
                        str(settlement_price), finality.value, str(revision),
                        expiration_date.isoformat(), settlement_session_date.isoformat(),
                    )
                ).encode("utf-8")
            ).hexdigest()
            points.append(
                FuturesTermStructurePoint(
                    curve_id=curve_id, sequence=sequence, instrument_id=instrument_id,
                    contract_expiration_date=expiration_date,
                    settlement_session_date=settlement_session_date, is_stale=is_stale,
                    settlement_price=settlement_price, settlement_finality=finality,
                    provider_revision=revision,
                    normalized_observation_id=normalized_observation_id,
                    time_to_expiry_days=time_to_expiry_days, point_hash=point_hash,
                )
            )

        contains_preliminary = any(
            point.settlement_finality is SettlementFinality.PRELIMINARY for point in points
        )

        classification: CurveClassification | None = None
        if (
            method.classification_enabled
            and method.classification_minimum_point_count is not None
            and len(points) >= method.classification_minimum_point_count
            and (
                not any(point.is_stale for point in points)
                or method.classification_permitted_with_stale_points
            )
        ):
            classification = classify_curve(
                tuple(point.settlement_price for point in points),
                cast(Decimal, method.classification_flat_threshold),
            )

        quote_currency = next(iter(currencies))
        quote_unit = next(iter(quote_units))
        curve_payload: dict[str, object] = {
            "series_id": series_id,
            "dataset_version_id": str(dataset_version_id),
            "dataset_content_hash": dataset_content_hash,
            "method_id": str(method_id),
            "method_content_hash": method.content_hash(),
            "as_of": as_of.isoformat(),
            "knowledge_at": knowledge_at.isoformat(),
            "quote_currency": quote_currency,
            "quote_unit": quote_unit,
            "points": [
                {
                    "sequence": point.sequence,
                    "instrument_id": point.instrument_id,
                    "normalized_observation_id": str(point.normalized_observation_id),
                    "settlement_price": str(point.settlement_price),
                    "settlement_finality": point.settlement_finality.value,
                    "provider_revision": point.provider_revision,
                    "contract_expiration_date": point.contract_expiration_date.isoformat(),
                    "settlement_session_date": point.settlement_session_date.isoformat(),
                }
                for point in points
            ],
        }
        content_hash = hashlib.sha256(_canonical_json(curve_payload).encode("utf-8")).hexdigest()

        curve = FuturesTermStructureCurve(
            curve_id=curve_id, series_id=series_id, dataset_version_id=dataset_version_id,
            method_id=method_id, as_of=as_of, knowledge_at=knowledge_at,
            quote_currency=quote_currency, quote_unit=quote_unit, point_count=len(points),
            contains_preliminary_point=contains_preliminary, classification=classification,
            content_hash=content_hash, derived_at=resolved_derived_at,
        )

        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    # Fixed placeholder count; no identifier or value is interpolated.
                    "INSERT INTO futures_term_structure_curves VALUES ("  # nosec B608
                    + ",".join(["%s"] * 13) + ") "
                    "ON CONFLICT (series_id,dataset_version_id,method_id,as_of,knowledge_at) "
                    "DO NOTHING RETURNING curve_id",
                    (
                        curve.curve_id, curve.series_id, curve.dataset_version_id,
                        curve.method_id, curve.as_of, curve.knowledge_at, curve.quote_currency,
                        curve.quote_unit, curve.point_count, curve.contains_preliminary_point,
                        None if curve.classification is None else curve.classification.value,
                        curve.content_hash, curve.derived_at,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT curve_id, content_hash FROM futures_term_structure_curves "
                        "WHERE series_id=%s AND dataset_version_id=%s AND method_id=%s "
                        "AND as_of=%s AND knowledge_at=%s",
                        (series_id, dataset_version_id, method_id, as_of, knowledge_at),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[1]) != curve.content_hash:
                        raise FuturesTermStructureDerivationError(
                            "term_structure_curve_conflict"
                        )
                    existing_curve_id = cast(UUID, existing[0])
                    return self._load_curve(existing_curve_id)
                for point in points:
                    cursor.execute(
                        "INSERT INTO futures_term_structure_points VALUES ("  # nosec B608
                        + ",".join(["%s"] * 12) + ")",
                        (
                            point.curve_id, point.sequence, point.instrument_id,
                            point.contract_expiration_date, point.settlement_session_date,
                            point.is_stale, point.settlement_price,
                            point.settlement_finality.value, point.provider_revision,
                            point.normalized_observation_id, point.time_to_expiry_days,
                            point.point_hash,
                        ),
                    )
        except FuturesTermStructureDerivationError:
            raise
        except Exception as error:
            raise FuturesTermStructureDerivationError(
                "term_structure_curve_derivation_failed"
            ) from error
        return curve, tuple(points)

    def _load_curve(
        self, curve_id: UUID
    ) -> tuple[FuturesTermStructureCurve, tuple[FuturesTermStructurePoint, ...]]:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT curve_id,series_id,dataset_version_id,method_id,as_of,knowledge_at,"
                    "quote_currency,quote_unit,point_count,contains_preliminary_point,"
                    "classification,content_hash,derived_at "
                    "FROM futures_term_structure_curves WHERE curve_id=%s",
                    (curve_id,),
                )
                curve_row = cursor.fetchone()
                cursor.execute(
                    "SELECT curve_id,sequence,instrument_id,contract_expiration_date,"
                    "settlement_session_date,is_stale,settlement_price,settlement_finality,"
                    "provider_revision,normalized_observation_id,time_to_expiry_days,point_hash "
                    "FROM futures_term_structure_points WHERE curve_id=%s ORDER BY sequence",
                    (curve_id,),
                )
                point_rows = cursor.fetchall()
        except Exception as error:
            raise FuturesTermStructureDerivationError("term_structure_curve_read_failed") from error
        if curve_row is None:
            raise FuturesTermStructureDerivationError(f"term_structure_curve_not_found:{curve_id}")
        curve = FuturesTermStructureCurve(
            curve_id=cast(UUID, curve_row[0]), series_id=str(curve_row[1]),
            dataset_version_id=cast(UUID, curve_row[2]), method_id=cast(UUID, curve_row[3]),
            as_of=cast(date, curve_row[4]), knowledge_at=cast(datetime, curve_row[5]),
            quote_currency=str(curve_row[6]), quote_unit=str(curve_row[7]),
            point_count=int(str(curve_row[8])), contains_preliminary_point=bool(curve_row[9]),
            classification=None if curve_row[10] is None else CurveClassification(str(curve_row[10])),
            content_hash=str(curve_row[11]), derived_at=cast(datetime, curve_row[12]),
        )
        points = tuple(
            FuturesTermStructurePoint(
                curve_id=cast(UUID, row[0]), sequence=int(str(row[1])),
                instrument_id=str(row[2]), contract_expiration_date=cast(date, row[3]),
                settlement_session_date=cast(date, row[4]), is_stale=bool(row[5]),
                settlement_price=Decimal(str(row[6])),
                settlement_finality=SettlementFinality(str(row[7])),
                provider_revision=int(str(row[8])), normalized_observation_id=cast(UUID, row[9]),
                time_to_expiry_days=int(str(row[10])), point_hash=str(row[11]),
            )
            for row in point_rows
        )
        return curve, points

    def get_curve(
        self, curve_id: UUID
    ) -> tuple[FuturesTermStructureCurve, tuple[FuturesTermStructurePoint, ...]]:
        return self._load_curve(curve_id)
