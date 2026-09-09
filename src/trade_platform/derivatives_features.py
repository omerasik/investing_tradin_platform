"""Deterministic Module 3J.1a derivatives feature calculator.

**Feature Authority extension, not a second authority.** This module
registers three ``FeatureFamily.DERIVATIVES`` feature definitions (the
futures-curve pack from ``docs/MODULE_3J1_PROPOSAL_MULTI_ASSET_DERIVATIVES_
FEATURE_PACK.md``) and computes their values, but every durable value it
produces is written through the existing, unmodified
:class:`trade_platform.feature_authority.PostgresFeatureAuthority`
(``register`` / ``materialize_subject``). This module owns no table, no
subject registry and no dataset registry of its own -- it is a stateless
calculator/orchestrator over already-authoritative evidence.

**Curve authority rule.** A futures term-structure curve
(:mod:`trade_platform.futures_term_structure`) is the only input this module
ever reads for a curve-shaped feature. It is loaded strictly by
``curve_id`` through ``PostgresFuturesTermStructureAuthority.get_curve()`` --
a read of the already-frozen ``futures_term_structure_curves`` /
``futures_term_structure_points`` rows only. This module never queries
``futures_settlement_observations`` directly and never reselects, interpolates
or extrapolates a curve point; 3I.3 already did point selection (finality
policy, staleness policy, minimum point count) once, and duplicating that
selection here would create exactly the second, divergent authority 3I.3 and
3J.0 were written to prevent.

**Subject identity.** All three features in this module are about a whole
futures series, never one contract, so every materialization uses
``FeatureSubjectType.FUTURES_SERIES`` with ``subject_id = curve.series_id`` --
never an individual contract, a continuous synthetic instrument, an equity or
a crypto instrument.

**Dataset identity.** ``FeatureMaterializationV2.dataset_version`` is always
``str(historical_dataset_versions.dataset_version_id)`` -- the canonical
sealed-dataset UUID the consumed curve itself was derived from, per the 3J.1
proposal section 7. A curve's own ``curve_id``/``content_hash`` are carried
separately, only as manifest provenance; they are never substituted for the
dataset identity.

**Caller-declared identity is independently proven, never trusted.** Every
public ``materialize_*`` method takes the caller's asserted
``(series_id, dataset_version_id, method_id)`` alongside the ``curve_id``
and verifies, before computing or writing anything, that the loaded curve
actually belongs to that exact series, was derived from that exact sealed
dataset, and used that exact method -- and that the dataset is genuinely
sealed and was knowable at the curve's own ``knowledge_at``. Any mismatch
fails closed with no value computed and no materialization written.

**PIT semantics.** A feature never invents knowledge earlier than its input
curve: ``event_at = effective_at = midnight UTC on curve.as_of``,
``knowledge_at = curve.knowledge_at`` (never earlier), and
``computed_at >= knowledge_at`` (defaults to ``knowledge_at`` when the caller
does not supply a later value). A later, separately-derived curve at a later
``knowledge_at`` produces a new, independent materialization; it can never
mutate or backdate an earlier one -- the underlying Feature Authority is
already append-only and PIT-gated.

**Quality semantics.** ``VALIDATED`` only when every curve point this
feature actually reads is ``FINAL`` and not stale; ``DEGRADED`` when the
upstream 3I.3 method explicitly permitted a preliminary or stale point and
one was actually consumed. ``REJECTED`` is never used by this module --
every failure mode (insufficient points, missing day-count convention,
non-positive price, unsealed/unknown dataset, subject/curve identity
mismatch) raises :class:`DerivativesFeatureError` and writes nothing,
per the proposal's "fail closed, no fabricated REJECTED row" rule.

**No AI/ML.** Every formula here is a closed-form deterministic calculation
over already-authorized evidence. No provider/network call is made by this
module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import cast
from uuid import UUID

from .feature_authority import (
    FeatureDefinitionVersion,
    FeatureFamily,
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
    PostgresFeatureAuthority,
)
from .futures_market_observations import SettlementFinality
from .futures_term_structure import (
    DayCountConvention,
    FuturesTermStructureCurve,
    FuturesTermStructureError,
    FuturesTermStructureMethod,
    FuturesTermStructurePoint,
    PostgresFuturesTermStructureAuthority,
)
from .persistence import PostgresDatabase

#: Not a raw ``ObservationKind`` -- points at the derived 3I.3 curve/point
#: authority these features exclusively read, per proposal section 5.
FUTURES_TERM_STRUCTURE_DATASET_TYPE = "FUTURES_TERM_STRUCTURE"

#: Identifies exactly this 3J.1a implementation. Never reused across a
#: behavioural change to any of the three formulas below -- a changed
#: formula requires a new calculation_version (and a new semantic_version).
CALCULATION_VERSION = "derivatives-futures-curve-3j1a-v1"

FUTURES_FRONT_BACK_NORMALIZED_SPREAD = "futures_front_back_normalized_spread"
FUTURES_ANNUALIZED_CALENDAR_SPREAD_RATE = "futures_annualized_calendar_spread_rate"
FUTURES_CURVE_CURVATURE = "futures_curve_curvature"

_CURVE_REQUIRED_FIELDS = (
    "curve_id", "curve_content_hash", "point_sequence", "settlement_price",
    "contract_expiration_date", "method_id", "method_content_hash",
)
_CARRY_REQUIRED_FIELDS = _CURVE_REQUIRED_FIELDS + ("carry_day_count_convention",)

#: ``feature_materializations.value`` is ``NUMERIC(38,12)``. A calendar-spread
#: ratio or annualized rate need not terminate in decimal (e.g. 2.50/100.50),
#: so the computed value is quantized to this exact column scale before it is
#: hashed and written -- otherwise the value Postgres actually stores would
#: silently diverge from the one this module hashed, and a value re-read
#: after a round trip would never equal the one just computed. Same rationale
#: as ``FuturesTermStructureMethod._definition_payload``'s own quantization of
#: ``classification_flat_threshold`` to its ``NUMERIC(10,6)`` column.
_VALUE_SCALE = Decimal("1E-12")


class DerivativesFeatureError(ValueError):
    """Base class: every failure path in this module is fail-closed."""


def derivatives_feature_definitions(
    created_at: datetime,
) -> tuple[FeatureDefinitionVersion, FeatureDefinitionVersion, FeatureDefinitionVersion]:
    """The three v1 3J.1a futures-curve feature definitions. Register once."""
    spread = FeatureDefinitionVersion(
        FUTURES_FRONT_BACK_NORMALIZED_SPREAD, FeatureFamily.DERIVATIVES, "1.0.0", "quant",
        "Normalized price spread between the two nearest-expiration points of an "
        "already-derived 3I.3 futures term-structure curve: (P_back - P_front) / P_front.",
        (FUTURES_TERM_STRUCTURE_DATASET_TYPE,), _CURVE_REQUIRED_FIELDS, "as_curve_derived",
        "event=effective=curve.as_of (midnight UTC); knowledge=curve.knowledge_at; "
        "computed_at>=knowledge_at", 0, {}, "fail_closed_no_materialization", "reject",
        "reject_future_knowledge", None, None, "dimensionless", CALCULATION_VERSION, created_at,
    )
    rate = FeatureDefinitionVersion(
        FUTURES_ANNUALIZED_CALENDAR_SPREAD_RATE, FeatureFamily.DERIVATIVES, "1.0.0", "quant",
        "Annualized calendar-spread rate between the two nearest-expiration points of a 3I.3 "
        "curve: normalized_spread / year_fraction(T_front, T_back, declared_day_count_convention), "
        "using the curve's own term-structure method's declared carry day-count convention -- "
        "never an implicit ACT/365 assumption.",
        (FUTURES_TERM_STRUCTURE_DATASET_TYPE,), _CARRY_REQUIRED_FIELDS, "as_curve_derived",
        "event=effective=curve.as_of (midnight UTC); knowledge=curve.knowledge_at; "
        "computed_at>=knowledge_at", 0, {}, "fail_closed_no_materialization", "reject",
        "reject_future_knowledge", None, None, "1/year", CALCULATION_VERSION, created_at,
    )
    curvature = FeatureDefinitionVersion(
        FUTURES_CURVE_CURVATURE, FeatureFamily.DERIVATIVES, "1.0.0", "quant",
        "Unequal-spacing three-point second-derivative curvature estimate over the three "
        "nearest-expiration points (front, mid, back) of a 3I.3 curve, normalized by the front "
        "price. Units are year^-2, not dimensionless: dividing by price removes the price "
        "dimension but not the time dimension.",
        (FUTURES_TERM_STRUCTURE_DATASET_TYPE,), _CARRY_REQUIRED_FIELDS, "as_curve_derived",
        "event=effective=curve.as_of (midnight UTC); knowledge=curve.knowledge_at; "
        "computed_at>=knowledge_at", 0, {}, "fail_closed_no_materialization", "reject",
        "reject_future_knowledge", None, None, "year^-2", CALCULATION_VERSION, created_at,
    )
    return spread, rate, curvature


def year_fraction(start: date, end: date, convention: DayCountConvention) -> Decimal:
    """Named, explicit day-count year fraction. Never an implicit ACT/365 default."""
    if end <= start:
        raise DerivativesFeatureError("non_positive_year_fraction")
    days = Decimal((end - start).days)
    if convention is DayCountConvention.ACT_365F:
        return days / Decimal(365)
    if convention is DayCountConvention.ACT_360:
        return days / Decimal(360)
    # Unreachable while DayCountConvention has exactly these two members --
    # kept as defense-in-depth against a future widening of that enum without
    # a matching year_fraction branch (mirrors futures_term_structure.py's
    # own "unreachable, defense-in-depth" comments).
    raise DerivativesFeatureError(f"unsupported_day_count_convention:{convention}")


def normalized_calendar_spread(front_price: Decimal, back_price: Decimal) -> Decimal:
    """``(P_back - P_front) / P_front``. Requires both real, positive prices."""
    if front_price <= 0 or back_price <= 0:
        raise DerivativesFeatureError("non_positive_settlement_price")
    return (back_price - front_price) / front_price


def annualized_calendar_spread_rate(
    *,
    front_price: Decimal,
    back_price: Decimal,
    front_expiration: date,
    back_expiration: date,
    convention: DayCountConvention,
) -> Decimal:
    spread = normalized_calendar_spread(front_price, back_price)
    yf = year_fraction(front_expiration, back_expiration, convention)
    return spread / yf


def curve_curvature(
    *,
    front_price: Decimal,
    mid_price: Decimal,
    back_price: Decimal,
    front_expiration: date,
    mid_expiration: date,
    back_expiration: date,
    convention: DayCountConvention,
) -> Decimal:
    if front_price <= 0 or mid_price <= 0 or back_price <= 0:
        raise DerivativesFeatureError("non_positive_settlement_price")
    h1 = year_fraction(front_expiration, mid_expiration, convention)
    h2 = year_fraction(mid_expiration, back_expiration, convention)
    second_derivative = (Decimal(2) / (h1 + h2)) * (
        (back_price - mid_price) / h2 - (mid_price - front_price) / h1
    )
    return second_derivative / front_price


@dataclass(frozen=True, slots=True)
class _CurveEvidence:
    """Everything one materialization call needs, already identity-checked."""

    curve: FuturesTermStructureCurve
    points: tuple[FuturesTermStructurePoint, ...]
    method: FuturesTermStructureMethod
    dataset_content_hash: str
    source_id: UUID


def _manifest_tokens(
    evidence: _CurveEvidence, points_used: tuple[FuturesTermStructurePoint, ...]
) -> tuple[str, ...]:
    """Deterministic canonical provenance tokens. Same evidence -> identical manifest.

    Every token is a resolvable canonical id, never a human-readable label
    alone, per the 3J.1 proposal section 6 manifest contract. ``points_used``
    is already in the curve's own deterministic sequence order, so token
    order never depends on iteration/dict order.
    """
    tokens = [
        f"historical_dataset_version_id:{evidence.curve.dataset_version_id}",
        f"historical_dataset_content_hash:{evidence.dataset_content_hash}",
        f"source_id:{evidence.source_id}",
        f"curve_id:{evidence.curve.curve_id}",
        f"curve_content_hash:{evidence.curve.content_hash}",
        f"method_id:{evidence.method.method_id}",
        f"method_version:{evidence.method.method_version}",
        f"method_content_hash:{evidence.method.content_hash()}",
    ]
    for point in points_used:
        tokens.append(f"curve_point:{point.curve_id}:{point.sequence}")
        tokens.append(f"settlement_observation:{point.normalized_observation_id}")
    return tuple(tokens)


def _quality_status(points_used: tuple[FuturesTermStructurePoint, ...]) -> FeatureQualityStatus:
    if all(
        point.settlement_finality is SettlementFinality.FINAL and not point.is_stale
        for point in points_used
    ):
        return FeatureQualityStatus.VALIDATED
    return FeatureQualityStatus.DEGRADED


class PostgresDerivativesFeatureCalculator:
    """Computes and materializes the 3J.1a futures-curve feature pack."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._term_structure = PostgresFuturesTermStructureAuthority(database)
        self._feature_authority = PostgresFeatureAuthority(database)

    def _load_evidence(
        self, *, curve_id: UUID, series_id: str, dataset_version_id: UUID, method_id: UUID,
    ) -> _CurveEvidence:
        try:
            curve, points = self._term_structure.get_curve(curve_id)
        except FuturesTermStructureError as error:
            raise DerivativesFeatureError("curve_not_found") from error
        if curve.series_id != series_id:
            raise DerivativesFeatureError("curve_series_mismatch")
        if curve.dataset_version_id != dataset_version_id:
            raise DerivativesFeatureError("curve_dataset_mismatch")
        if curve.method_id != method_id:
            raise DerivativesFeatureError("curve_method_mismatch")
        try:
            method = self._term_structure.get_method(method_id)
        except FuturesTermStructureError as error:
            raise DerivativesFeatureError("method_not_found") from error

        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, created_at, content_hash, source_id FROM "
                "historical_dataset_versions WHERE dataset_version_id=%s",
                (dataset_version_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise DerivativesFeatureError("dataset_not_found")
        if str(row[0]) != "SEALED":
            raise DerivativesFeatureError("dataset_not_sealed")
        dataset_created_at = cast(datetime, row[1])
        if dataset_created_at > curve.knowledge_at:
            raise DerivativesFeatureError("dataset_not_knowable_at_curve_knowledge_at")

        return _CurveEvidence(
            curve=curve, points=points, method=method,
            dataset_content_hash=str(row[2]), source_id=cast(UUID, row[3]),
        )

    def _require_carry_convention(self, evidence: _CurveEvidence) -> DayCountConvention:
        if not evidence.method.carry_enabled or evidence.method.day_count_convention is None:
            raise DerivativesFeatureError("missing_carry_day_count_convention")
        return evidence.method.day_count_convention

    def _write(
        self,
        *,
        feature_id: UUID,
        evidence: _CurveEvidence,
        points_used: tuple[FuturesTermStructurePoint, ...],
        value: Decimal,
        computed_at: datetime | None,
    ) -> FeatureMaterializationV2:
        event_at = effective_at = datetime.combine(evidence.curve.as_of, time.min, tzinfo=UTC)
        knowledge_at = evidence.curve.knowledge_at
        resolved_computed_at = knowledge_at if computed_at is None else computed_at
        quantized_value = value.quantize(_VALUE_SCALE)
        materialization = FeatureMaterializationV2.create(
            feature_id=feature_id, subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=evidence.curve.series_id, dataset_version=str(evidence.curve.dataset_version_id),
            event_at=event_at, effective_at=effective_at, knowledge_at=knowledge_at,
            computed_at=resolved_computed_at, source_observation_manifest=_manifest_tokens(evidence, points_used),
            value=quantized_value, quality_status=_quality_status(points_used),
        )
        self._feature_authority.materialize_subject(materialization)
        return materialization

    def materialize_normalized_spread(
        self,
        *,
        feature_id: UUID,
        curve_id: UUID,
        series_id: str,
        dataset_version_id: UUID,
        method_id: UUID,
        computed_at: datetime | None = None,
    ) -> FeatureMaterializationV2:
        evidence = self._load_evidence(
            curve_id=curve_id, series_id=series_id, dataset_version_id=dataset_version_id,
            method_id=method_id,
        )
        if len(evidence.points) < 2:
            raise DerivativesFeatureError("insufficient_curve_points_for_spread")
        front, back = evidence.points[0], evidence.points[1]
        value = normalized_calendar_spread(front.settlement_price, back.settlement_price)
        return self._write(
            feature_id=feature_id, evidence=evidence, points_used=(front, back),
            value=value, computed_at=computed_at,
        )

    def materialize_annualized_calendar_spread_rate(
        self,
        *,
        feature_id: UUID,
        curve_id: UUID,
        series_id: str,
        dataset_version_id: UUID,
        method_id: UUID,
        computed_at: datetime | None = None,
    ) -> FeatureMaterializationV2:
        evidence = self._load_evidence(
            curve_id=curve_id, series_id=series_id, dataset_version_id=dataset_version_id,
            method_id=method_id,
        )
        if len(evidence.points) < 2:
            raise DerivativesFeatureError("insufficient_curve_points_for_calendar_spread_rate")
        convention = self._require_carry_convention(evidence)
        front, back = evidence.points[0], evidence.points[1]
        value = annualized_calendar_spread_rate(
            front_price=front.settlement_price, back_price=back.settlement_price,
            front_expiration=front.contract_expiration_date,
            back_expiration=back.contract_expiration_date, convention=convention,
        )
        return self._write(
            feature_id=feature_id, evidence=evidence, points_used=(front, back),
            value=value, computed_at=computed_at,
        )

    def materialize_curve_curvature(
        self,
        *,
        feature_id: UUID,
        curve_id: UUID,
        series_id: str,
        dataset_version_id: UUID,
        method_id: UUID,
        computed_at: datetime | None = None,
    ) -> FeatureMaterializationV2:
        evidence = self._load_evidence(
            curve_id=curve_id, series_id=series_id, dataset_version_id=dataset_version_id,
            method_id=method_id,
        )
        if len(evidence.points) < 3:
            raise DerivativesFeatureError("insufficient_curve_points_for_curvature")
        convention = self._require_carry_convention(evidence)
        front, mid, back = evidence.points[0], evidence.points[1], evidence.points[2]
        value = curve_curvature(
            front_price=front.settlement_price, mid_price=mid.settlement_price,
            back_price=back.settlement_price, front_expiration=front.contract_expiration_date,
            mid_expiration=mid.contract_expiration_date, back_expiration=back.contract_expiration_date,
            convention=convention,
        )
        return self._write(
            feature_id=feature_id, evidence=evidence, points_used=(front, mid, back),
            value=value, computed_at=computed_at,
        )
