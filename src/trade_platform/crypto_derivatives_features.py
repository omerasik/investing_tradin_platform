"""Deterministic Module 3J.1c crypto mark/index/funding feature calculators.

**Feature Authority extension, not a second authority.** This module registers
exactly the three remaining ``FeatureFamily.DERIVATIVES`` feature definitions
from the 3J.1 proposal
(``docs/MODULE_3J1_PROPOSAL_MULTI_ASSET_DERIVATIVES_FEATURE_PACK.md`` sections
3.5-3.7) -- ``crypto_mark_index_basis``, ``crypto_realized_funding_annualized``
and ``crypto_funding_forecast_error`` -- and computes their values, but every
durable value it produces is written through the existing, unmodified
:class:`trade_platform.feature_authority.PostgresFeatureAuthority`
(``register`` / ``materialize_subject``). This module owns no table, no
subject registry, no dataset registry and no funding-convention authority of
its own -- it is a stateless calculator over already-authoritative 3H.2/3I.2
evidence.

**Canonical evidence chain.** Every observation this module reads resolves
strictly through ``historical_dataset_members`` ->
``historical_normalized_observations`` -> ``historical_raw_observations`` ->
``crypto_reference_price_observations`` / ``crypto_funding_observations``.
Funding-schedule facts resolve through the existing 3H.2
``crypto_funding_conventions`` authority, read by exact ``convention_id``
never re-resolved "as of today".

**Dataset identity.** ``FeatureMaterializationV2.dataset_version`` is always
``str(dataset_version_id)`` -- the caller-declared sealed
``historical_dataset_versions.dataset_version_id`` every input observation
must be a member of. No cross-dataset pair is ever formed.

**crypto_mark_index_basis.** ``(mark_price - index_price) / index_price`` for
one instrument, exact-timestamp match only (``mark.event_at ==
index.event_at``, no tolerance/staleness window), both observations members
of the exact same sealed dataset, correct kinds (never mark-vs-mark or
index-vs-index), and both individually PIT-visible. Requires the instrument's
3H.2 ``ReferencePriceRequirement`` to permit both ``MARK`` and ``INDEX``
(``MARK_AND_INDEX``) -- a defensive recheck of pair coherence beyond what
3I.2 already validated at ingestion.

**crypto_realized_funding_annualized.** Annualizes exactly one
``FUNDING_RATE_REALIZED`` observation using the 3H.2 funding convention it was
already bound to at ingestion (``convention_id`` / ``convention_version``
stored on the observation) -- never a convention re-resolved "as of today".
``annualization_basis = ACT_365_FIXED`` is an immutable feature-definition
parameter, not a venue claim. Fails closed (raises) only when the resolved
convention's own ``interval_hours`` is non-positive or does not convert to a
whole number of seconds; every other mismatch (wrong instrument, wrong
convention version, incoherent settlement asset, not-yet-knowable convention,
non-perpetual instrument) silently produces no materialization.

**crypto_funding_forecast_error.** ``realized_rate - indicative_rate`` for the
same instrument and the same ``target_funding_at``, where ``indicative_rate``
is the latest eligible ``FUNDING_RATE_INDICATIVE`` publication strictly before
the target instant, both sides members of the same sealed dataset and bound
to the identical ``(convention_id, convention_version)``. This is a
post-event forecast-error feature: ``knowledge_at`` (and therefore visibility
at any ``decision_at``) can never precede the realized observation's own
knowledge time.

**Revision resolution.** Every observation kind is independently ranked
``revision DESC, ingested_at DESC`` at its own exact instant -- the same
convention 3I.1/3I.2/3J.1a/3J.1b already use. If multiple distinct
``provider_identifier`` values remain eligible for the same
instrument/kind/instant after revision resolution, the pairing is ambiguous
and this module fails closed (raises) rather than choosing arbitrarily.

**Quality semantics.** Every materialization this module writes is
``VALIDATED``. Fail-closed conditions that are genuinely ambiguous (mixed
provider identity) or reflect malformed convention data (non-positive or
non-integral-second funding interval) raise
:class:`CryptoDerivativesFeatureError`; every other ineligibility (missing
observation, timestamp mismatch, cross-dataset pair, wrong kind, convention
mismatch, ineligible instrument kind) silently produces no materialization at
all -- never a fabricated ``DEGRADED``/``REJECTED`` row.

**No AI/ML.** Every formula is a closed-form deterministic arithmetic
expression over already-authorized evidence. No provider/network call is
made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from .crypto_instruments import (
    CryptoInstrumentError,
    CryptoInstrumentKind,
    CryptoInstrumentSpecification,
    PostgresCryptoInstrumentAuthority,
    ReferencePriceRequirement,
)
from .feature_authority import (
    FeatureDefinitionVersion,
    FeatureFamily,
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
    PostgresFeatureAuthority,
)
from .persistence import PostgresDatabase

#: Identifies exactly this 3J.1c implementation. Never reused across a
#: behavioural change to a formula below -- a changed formula requires a new
#: calculation_version (and a new semantic_version).
CALCULATION_VERSION_MARK_INDEX_BASIS = "derivatives-crypto-mark-index-basis-3j1c-v1"
CALCULATION_VERSION_REALIZED_FUNDING_ANNUALIZED = (
    "derivatives-crypto-realized-funding-annualized-3j1c-v1"
)
CALCULATION_VERSION_FUNDING_FORECAST_ERROR = "derivatives-crypto-funding-forecast-error-3j1c-v1"

CRYPTO_MARK_INDEX_BASIS = "crypto_mark_index_basis"
CRYPTO_REALIZED_FUNDING_ANNUALIZED = "crypto_realized_funding_annualized"
CRYPTO_FUNDING_FORECAST_ERROR = "crypto_funding_forecast_error"

#: An explicit, immutable feature-definition parameter -- a calculation
#: convention, never a claim that any venue itself annualizes on ACT/365.
#: Changing this requires a new feature definition/version, never an in-place
#: edit of this one.
_ANNUALIZATION_BASIS = "ACT_365_FIXED"
_ANNUALIZATION_YEAR_SECONDS = 365 * 24 * 60 * 60

#: ``feature_materializations.value`` is ``NUMERIC(38,12)``. The exact native
#: result is computed first, then quantized to this exact column scale before
#: it is hashed and written -- otherwise the value PostgreSQL actually stores
#: would silently diverge from the one this module hashed. Same convention as
#: ``open_interest_features._VALUE_SCALE``.
_VALUE_SCALE = Decimal("1E-12")

_MARK_INDEX_REQUIRED_FIELDS = ("price", "price_asset", "event_at", "revision", "ingested_at")
_REALIZED_FUNDING_REQUIRED_FIELDS = (
    "funding_rate", "target_funding_at", "convention_id", "convention_version",
    "revision", "ingested_at",
)
_FORECAST_ERROR_REQUIRED_FIELDS = (
    "funding_rate", "target_funding_at", "published_at", "convention_id",
    "convention_version", "revision", "ingested_at",
)


class CryptoDerivativesFeatureError(ValueError):
    """Base class: every failure path in this module is fail-closed."""


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CryptoDerivativesFeatureError(f"{name}_must_be_timezone_aware")


def _validate_and_convert_interval(interval_hours: Decimal) -> Decimal:
    """Deterministic ``interval_hours`` -> ``interval_seconds`` conversion.

    A pure function (no database access) so both failure modes are directly
    unit-testable: ``crypto_funding_conventions.interval_hours`` already
    carries a database ``CHECK(interval_hours > 0)``, so a non-positive value
    can never actually reach :meth:`PostgresCryptoDerivativesFeatureCalculator.
    _resolve_exact_convention` through Postgres -- this function is what lets
    that branch be proven anyway. Never rounds an unknown cadence.
    """
    if interval_hours <= 0:
        raise CryptoDerivativesFeatureError("non_positive_funding_interval")
    interval_seconds = interval_hours * Decimal(3600)
    if interval_seconds != interval_seconds.to_integral_value():
        raise CryptoDerivativesFeatureError("non_integral_interval_seconds")
    return interval_seconds


def crypto_mark_index_basis_definition(created_at: datetime) -> FeatureDefinitionVersion:
    """The single v1 3J.1c ``crypto_mark_index_basis`` feature definition. Register once."""
    return FeatureDefinitionVersion(
        CRYPTO_MARK_INDEX_BASIS, FeatureFamily.DERIVATIVES, "1.0.0", "quant",
        "Crypto mark/index basis for one instrument: (mark_price - index_price) / "
        "index_price. The MARK_PRICE and INDEX_PRICE observations must share the "
        "exact same event_at (no tolerance/staleness window), the exact same price "
        "asset, and the exact same sealed historical dataset version, and be "
        "individually PIT-visible. Only valid where the instrument's 3H.2 "
        "ReferencePriceRequirement permits both MARK and INDEX.",
        ("MARK_PRICE", "INDEX_PRICE"), _MARK_INDEX_REQUIRED_FIELDS, "as_observed",
        "event=shared mark/index event_at; effective_at=max(mark,index effective_at); "
        "knowledge_at=max(mark,index normalized_at, dataset.created_at); "
        "computed_at>=knowledge_at", 0, {}, "fail_closed_no_materialization", "reject",
        "reject_future_knowledge", None, None, "dimensionless",
        CALCULATION_VERSION_MARK_INDEX_BASIS, created_at,
    )


def crypto_realized_funding_annualized_definition(created_at: datetime) -> FeatureDefinitionVersion:
    """The single v1 3J.1c ``crypto_realized_funding_annualized`` feature definition."""
    return FeatureDefinitionVersion(
        CRYPTO_REALIZED_FUNDING_ANNUALIZED, FeatureFamily.DERIVATIVES, "1.0.0", "quant",
        "Annualizes one FUNDING_RATE_REALIZED observation using the 3H.2 funding "
        "convention already bound to it at ingestion: "
        "annualized_rate = realized_funding_rate * annualization_year_seconds / "
        "interval_seconds, with annualization_basis=ACT_365_FIXED declared as an "
        "immutable calculation convention, not a venue fact. Fails closed if the "
        "resolved interval is not a positive whole number of seconds.",
        ("FUNDING_RATE_REALIZED",), _REALIZED_FUNDING_REQUIRED_FIELDS, "as_observed",
        "event=realized funding/target settlement instant; "
        "effective_at=realized.effective_at; "
        "knowledge_at=max(realized normalized_at, dataset.created_at); "
        "computed_at>=knowledge_at", 0,
        {"annualization_basis": _ANNUALIZATION_BASIS,
         "annualization_year_seconds": _ANNUALIZATION_YEAR_SECONDS},
        "fail_closed_no_materialization", "reject", "reject_future_knowledge", None, None,
        "1/year", CALCULATION_VERSION_REALIZED_FUNDING_ANNUALIZED, created_at,
    )


def crypto_funding_forecast_error_definition(created_at: datetime) -> FeatureDefinitionVersion:
    """The single v1 3J.1c ``crypto_funding_forecast_error`` feature definition."""
    return FeatureDefinitionVersion(
        CRYPTO_FUNDING_FORECAST_ERROR, FeatureFamily.DERIVATIVES, "1.0.0", "quant",
        "Post-event funding forecast-error for one target funding instant: "
        "realized_rate - indicative_rate, where indicative_rate is the latest "
        "eligible FUNDING_RATE_INDICATIVE publication strictly before the target "
        "instant, bound to the identical funding-convention identity and version as "
        "the realized observation, both members of the exact same sealed dataset. "
        "Never visible before the realized observation itself is PIT-visible -- this "
        "is an estimate-error feature, never a pre-event prediction.",
        ("FUNDING_RATE_REALIZED", "FUNDING_RATE_INDICATIVE"), _FORECAST_ERROR_REQUIRED_FIELDS,
        "as_observed",
        "event=target_funding_at; effective_at=max(realized,indicative effective_at); "
        "knowledge_at=max(realized,indicative normalized_at, dataset.created_at); "
        "computed_at>=knowledge_at", 1, {}, "fail_closed_no_materialization", "reject",
        "reject_future_knowledge", None, None, "dimensionless",
        CALCULATION_VERSION_FUNDING_FORECAST_ERROR, created_at,
    )


@dataclass(frozen=True, slots=True)
class _DatasetInfo:
    dataset_version_id: UUID
    content_hash: str
    source_id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _ReferencePriceObservation:
    normalized_observation_id: UUID
    raw_observation_id: UUID
    provider_identifier: str
    event_at: datetime
    effective_at: datetime
    ingested_at: datetime
    revision: int
    normalized_at: datetime
    price: Decimal
    price_asset: str


@dataclass(frozen=True, slots=True)
class _FundingObservation:
    normalized_observation_id: UUID
    raw_observation_id: UUID
    provider_identifier: str
    event_at: datetime
    effective_at: datetime
    ingested_at: datetime
    revision: int
    normalized_at: datetime
    funding_rate: Decimal
    convention_id: UUID
    convention_version: int
    target_funding_at: datetime
    published_at: datetime
    settlement_asset: str


@dataclass(frozen=True, slots=True)
class _ConventionInfo:
    convention_id: UUID
    interval_hours: Decimal
    interval_seconds: Decimal
    funding_settlement_asset: str


def _mark_index_basis_manifest_tokens(
    *, dataset: _DatasetInfo, instrument_id: str,
    mark: _ReferencePriceObservation, index: _ReferencePriceObservation,
) -> tuple[str, ...]:
    """Deterministic canonical provenance tokens. Same evidence -> identical manifest."""
    return (
        f"historical_dataset_version_id:{dataset.dataset_version_id}",
        f"historical_dataset_content_hash:{dataset.content_hash}",
        f"source_id:{dataset.source_id}",
        f"instrument_id:{instrument_id}",
        f"mark_normalized_observation_id:{mark.normalized_observation_id}",
        f"mark_raw_observation_id:{mark.raw_observation_id}",
        f"mark_event_at:{mark.event_at.isoformat()}",
        f"mark_revision:{mark.revision}",
        f"mark_ingested_at:{mark.ingested_at.isoformat()}",
        f"index_normalized_observation_id:{index.normalized_observation_id}",
        f"index_raw_observation_id:{index.raw_observation_id}",
        f"index_event_at:{index.event_at.isoformat()}",
        f"index_revision:{index.revision}",
        f"index_ingested_at:{index.ingested_at.isoformat()}",
        f"price_asset:{mark.price_asset}",
    )


def _realized_funding_annualized_manifest_tokens(
    *, dataset: _DatasetInfo, instrument_id: str,
    realized: _FundingObservation, convention: _ConventionInfo,
) -> tuple[str, ...]:
    return (
        f"historical_dataset_version_id:{dataset.dataset_version_id}",
        f"historical_dataset_content_hash:{dataset.content_hash}",
        f"source_id:{dataset.source_id}",
        f"instrument_id:{instrument_id}",
        f"realized_normalized_observation_id:{realized.normalized_observation_id}",
        f"realized_raw_observation_id:{realized.raw_observation_id}",
        f"realized_target_funding_at:{realized.target_funding_at.isoformat()}",
        f"realized_revision:{realized.revision}",
        f"realized_ingested_at:{realized.ingested_at.isoformat()}",
        f"convention_id:{convention.convention_id}",
        f"convention_version:{realized.convention_version}",
        f"interval_hours:{convention.interval_hours}",
        f"funding_settlement_asset:{convention.funding_settlement_asset}",
        f"annualization_basis:{_ANNUALIZATION_BASIS}",
    )


def _funding_forecast_error_manifest_tokens(
    *, dataset: _DatasetInfo, instrument_id: str,
    realized: _FundingObservation, indicative: _FundingObservation,
) -> tuple[str, ...]:
    return (
        f"historical_dataset_version_id:{dataset.dataset_version_id}",
        f"historical_dataset_content_hash:{dataset.content_hash}",
        f"source_id:{dataset.source_id}",
        f"instrument_id:{instrument_id}",
        f"realized_normalized_observation_id:{realized.normalized_observation_id}",
        f"realized_raw_observation_id:{realized.raw_observation_id}",
        f"realized_revision:{realized.revision}",
        f"realized_ingested_at:{realized.ingested_at.isoformat()}",
        f"realized_event_at:{realized.event_at.isoformat()}",
        f"realized_target_funding_at:{realized.target_funding_at.isoformat()}",
        f"indicative_normalized_observation_id:{indicative.normalized_observation_id}",
        f"indicative_raw_observation_id:{indicative.raw_observation_id}",
        f"indicative_revision:{indicative.revision}",
        f"indicative_ingested_at:{indicative.ingested_at.isoformat()}",
        f"indicative_published_at:{indicative.published_at.isoformat()}",
        f"indicative_target_funding_at:{indicative.target_funding_at.isoformat()}",
        f"convention_id:{realized.convention_id}",
        f"convention_version:{realized.convention_version}",
        f"funding_settlement_asset:{realized.settlement_asset}",
    )


class PostgresCryptoDerivativesFeatureCalculator:
    """Computes and materializes the three 3J.1c crypto derivatives features."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._feature_authority = PostgresFeatureAuthority(database)
        self._crypto = PostgresCryptoInstrumentAuthority(database)

    def _load_dataset(self, dataset_version_id: UUID, decision_at: datetime) -> _DatasetInfo:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, created_at, content_hash, source_id FROM "
                "historical_dataset_versions WHERE dataset_version_id=%s",
                (dataset_version_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise CryptoDerivativesFeatureError("dataset_not_found")
        if str(row[0]) != "SEALED":
            raise CryptoDerivativesFeatureError("dataset_not_sealed")
        created_at = cast(datetime, row[1])
        if created_at > decision_at:
            raise CryptoDerivativesFeatureError("dataset_not_knowable_at_decision_at")
        return _DatasetInfo(dataset_version_id, str(row[2]), cast(UUID, row[3]), created_at)

    def _specification_or_none(
        self, instrument_id: str, decision_at: datetime
    ) -> CryptoInstrumentSpecification | None:
        try:
            return self._crypto.get_specification(instrument_id, known_at=decision_at)
        except CryptoInstrumentError:
            return None

    # ---- crypto_mark_index_basis -------------------------------------------

    def _reference_price_rows(
        self, *, dataset_version_id: UUID, instrument_id: str, kind: str,
        event_at: datetime, decision_at: datetime,
    ) -> tuple[_ReferencePriceObservation, ...]:
        statement = (
            "SELECT normalized_observation_id, raw_observation_id, provider_identifier, "
            "event_at, effective_at, ingested_at, revision, normalized_at, price, price_asset "
            "FROM (SELECT n.normalized_observation_id, r.raw_observation_id, "
            "r.provider_identifier, r.event_at, r.effective_at, r.ingested_at, r.revision, "
            "n.normalized_at, p.price, p.price_asset, ROW_NUMBER() OVER ("
            "PARTITION BY r.provider_identifier, r.event_at "
            "ORDER BY r.revision DESC, r.ingested_at DESC) AS rnk "
            "FROM historical_dataset_members m "
            "JOIN historical_normalized_observations n ON n.normalized_observation_id=m.normalized_observation_id "
            "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
            "JOIN crypto_reference_price_observations p ON p.normalized_observation_id=n.normalized_observation_id "
            "WHERE m.dataset_version_id=%s AND n.instrument_id=%s AND r.observation_kind=%s "
            "AND n.quality_status='VALIDATED' AND n.normalized_at<=%s AND r.ingested_at<=%s "
            "AND r.event_at=%s) ranked WHERE rnk=1"
        )
        params = (dataset_version_id, instrument_id, kind, decision_at, decision_at, event_at)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(statement, params)
            rows = cursor.fetchall()
        return tuple(
            _ReferencePriceObservation(
                UUID(str(row[0])), UUID(str(row[1])), str(row[2]), row[3], row[4], row[5],
                int(str(row[6])), row[7], Decimal(str(row[8])), str(row[9]),
            )
            for row in rows
        )

    def _select_reference_price(
        self, *, dataset_version_id: UUID, instrument_id: str, kind: str, event_at: datetime,
        decision_at: datetime, ambiguous_message: str,
    ) -> _ReferencePriceObservation | None:
        rows = self._reference_price_rows(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id, kind=kind,
            event_at=event_at, decision_at=decision_at,
        )
        if not rows:
            return None
        if len({row.provider_identifier for row in rows}) > 1:
            raise CryptoDerivativesFeatureError(ambiguous_message)
        return rows[0]

    def materialize_crypto_mark_index_basis(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version_id: UUID,
        event_at: datetime,
        decision_at: datetime,
        computed_at: datetime | None = None,
    ) -> FeatureMaterializationV2 | None:
        _aware(event_at, "event_at")
        _aware(decision_at, "decision_at")
        if computed_at is not None:
            _aware(computed_at, "computed_at")

        dataset = self._load_dataset(dataset_version_id, decision_at)
        mark = self._select_reference_price(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            kind="MARK_PRICE", event_at=event_at, decision_at=decision_at,
            ambiguous_message="ambiguous_mark_observation_identity",
        )
        if mark is None:
            return None
        index = self._select_reference_price(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            kind="INDEX_PRICE", event_at=event_at, decision_at=decision_at,
            ambiguous_message="ambiguous_index_observation_identity",
        )
        if index is None:
            return None
        if mark.price_asset != index.price_asset:
            return None
        specification = self._specification_or_none(instrument_id, decision_at)
        if (
            specification is None
            or specification.reference_price_requirement is not ReferencePriceRequirement.MARK_AND_INDEX
        ):
            return None

        knowledge_at = max(mark.normalized_at, index.normalized_at, dataset.created_at)
        if knowledge_at > decision_at:
            raise CryptoDerivativesFeatureError("knowledge_at_exceeds_decision_at")
        effective_at = max(mark.effective_at, index.effective_at)
        resolved_computed_at = knowledge_at if computed_at is None else computed_at
        value = ((mark.price - index.price) / index.price).quantize(_VALUE_SCALE)

        materialization = FeatureMaterializationV2.create(
            feature_id=feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=instrument_id, dataset_version=str(dataset_version_id),
            event_at=event_at, effective_at=effective_at, knowledge_at=knowledge_at,
            computed_at=resolved_computed_at,
            source_observation_manifest=_mark_index_basis_manifest_tokens(
                dataset=dataset, instrument_id=instrument_id, mark=mark, index=index,
            ),
            value=value, quality_status=FeatureQualityStatus.VALIDATED,
        )
        self._feature_authority.materialize_subject(materialization)
        return materialization

    # ---- funding (shared plumbing) ------------------------------------------

    def _eligible_funding_rows(
        self,
        *,
        dataset_version_id: UUID,
        instrument_id: str,
        kind: str,
        decision_at: datetime,
        extra_predicate: str,
        predicate_params: tuple[object, ...],
    ) -> tuple[_FundingObservation, ...]:
        statement = (
            "SELECT normalized_observation_id, raw_observation_id, provider_identifier, "
            "event_at, effective_at, ingested_at, revision, normalized_at, funding_rate, "
            "convention_id, convention_version, target_funding_at, published_at, "
            "settlement_asset FROM (SELECT n.normalized_observation_id, r.raw_observation_id, "
            "r.provider_identifier, r.event_at, r.effective_at, r.ingested_at, r.revision, "
            "n.normalized_at, f.funding_rate, f.convention_id, f.convention_version, "
            "f.target_funding_at, f.published_at, f.settlement_asset, ROW_NUMBER() OVER ("
            "PARTITION BY r.provider_identifier, r.event_at "
            "ORDER BY r.revision DESC, r.ingested_at DESC) AS rnk "
            "FROM historical_dataset_members m "
            "JOIN historical_normalized_observations n ON n.normalized_observation_id=m.normalized_observation_id "
            "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
            "JOIN crypto_funding_observations f ON f.normalized_observation_id=n.normalized_observation_id "
            "WHERE m.dataset_version_id=%s AND n.instrument_id=%s AND r.observation_kind=%s "
            "AND n.quality_status='VALIDATED' AND n.normalized_at<=%s AND r.ingested_at<=%s "
            f"AND {extra_predicate}"  # nosec B608 - fixed policy fragments only
            ") ranked WHERE rnk=1 ORDER BY event_at DESC"
        )
        params = (
            dataset_version_id, instrument_id, kind, decision_at, decision_at, *predicate_params,
        )
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(statement, params)
            rows = cursor.fetchall()
        return tuple(
            _FundingObservation(
                UUID(str(row[0])), UUID(str(row[1])), str(row[2]), row[3], row[4], row[5],
                int(str(row[6])), row[7], Decimal(str(row[8])), UUID(str(row[9])),
                int(str(row[10])), row[11], row[12], str(row[13]),
            )
            for row in rows
        )

    def _select_funding(
        self, *, dataset_version_id: UUID, instrument_id: str, kind: str,
        extra_predicate: str, predicate_params: tuple[object, ...], decision_at: datetime,
        ambiguous_message: str,
    ) -> _FundingObservation | None:
        rows = self._eligible_funding_rows(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id, kind=kind,
            decision_at=decision_at, extra_predicate=extra_predicate,
            predicate_params=predicate_params,
        )
        if not rows:
            return None
        if len({row.provider_identifier for row in rows}) > 1:
            raise CryptoDerivativesFeatureError(ambiguous_message)
        return rows[0]

    def _select_latest_indicative(
        self, *, dataset_version_id: UUID, instrument_id: str, target_funding_at: datetime,
        decision_at: datetime,
    ) -> _FundingObservation | None:
        rows = self._eligible_funding_rows(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            kind="FUNDING_RATE_INDICATIVE", decision_at=decision_at,
            extra_predicate="f.target_funding_at=%s AND f.published_at<%s",
            predicate_params=(target_funding_at, target_funding_at),
        )
        if not rows:
            return None
        latest_event_at = rows[0].event_at
        top_group = tuple(row for row in rows if row.event_at == latest_event_at)
        if len({row.provider_identifier for row in top_group}) > 1:
            raise CryptoDerivativesFeatureError("ambiguous_indicative_funding_observation_identity")
        return top_group[0]

    def _resolve_exact_convention(
        self, *, convention_id: UUID, expected_instrument_id: str,
        expected_convention_version: int, expected_settlement_asset: str,
        target_funding_at: datetime, decision_at: datetime,
    ) -> _ConventionInfo | None:
        """Resolve exactly the convention already bound to the observation.

        Never "as of today" -- read by primary key, then judged against the
        identity, coherence and both PIT clocks (``effective_from`` and
        ``known_at``) the same way :meth:`PostgresCryptoInstrumentAuthority.
        funding_convention_point_in_time` would, but without re-resolving
        against whatever convention is latest today.
        """
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM crypto_funding_conventions WHERE convention_id=%s",
                (convention_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        instrument_id = str(row[1])
        convention_version = int(str(row[2]))
        interval_hours = Decimal(str(row[3]))
        funding_settlement_asset = str(row[7])
        effective_from = cast(datetime, row[8])
        known_at = cast(datetime, row[9])
        if instrument_id != expected_instrument_id:
            return None
        if convention_version != expected_convention_version:
            return None
        if funding_settlement_asset != expected_settlement_asset:
            return None
        if effective_from > target_funding_at:
            return None
        if known_at > decision_at:
            return None
        interval_seconds = _validate_and_convert_interval(interval_hours)
        return _ConventionInfo(convention_id, interval_hours, interval_seconds, funding_settlement_asset)

    # ---- crypto_realized_funding_annualized ---------------------------------

    def materialize_crypto_realized_funding_annualized(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version_id: UUID,
        event_at: datetime,
        decision_at: datetime,
        computed_at: datetime | None = None,
    ) -> FeatureMaterializationV2 | None:
        _aware(event_at, "event_at")
        _aware(decision_at, "decision_at")
        if computed_at is not None:
            _aware(computed_at, "computed_at")

        dataset = self._load_dataset(dataset_version_id, decision_at)
        realized = self._select_funding(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            kind="FUNDING_RATE_REALIZED", extra_predicate="f.target_funding_at=%s",
            predicate_params=(event_at,), decision_at=decision_at,
            ambiguous_message="ambiguous_realized_funding_observation_identity",
        )
        if realized is None:
            return None
        specification = self._specification_or_none(instrument_id, decision_at)
        if specification is None or specification.kind is not CryptoInstrumentKind.PERPETUAL:
            return None
        convention = self._resolve_exact_convention(
            convention_id=realized.convention_id, expected_instrument_id=instrument_id,
            expected_convention_version=realized.convention_version,
            expected_settlement_asset=realized.settlement_asset,
            target_funding_at=realized.target_funding_at, decision_at=decision_at,
        )
        if convention is None:
            return None

        knowledge_at = max(realized.normalized_at, dataset.created_at)
        if knowledge_at > decision_at:
            raise CryptoDerivativesFeatureError("knowledge_at_exceeds_decision_at")
        effective_at = realized.effective_at
        resolved_computed_at = knowledge_at if computed_at is None else computed_at
        value = (
            realized.funding_rate * Decimal(_ANNUALIZATION_YEAR_SECONDS) / convention.interval_seconds
        ).quantize(_VALUE_SCALE)

        materialization = FeatureMaterializationV2.create(
            feature_id=feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=instrument_id, dataset_version=str(dataset_version_id),
            event_at=event_at, effective_at=effective_at, knowledge_at=knowledge_at,
            computed_at=resolved_computed_at,
            source_observation_manifest=_realized_funding_annualized_manifest_tokens(
                dataset=dataset, instrument_id=instrument_id, realized=realized,
                convention=convention,
            ),
            value=value, quality_status=FeatureQualityStatus.VALIDATED,
        )
        self._feature_authority.materialize_subject(materialization)
        return materialization

    # ---- crypto_funding_forecast_error ---------------------------------------

    def materialize_crypto_funding_forecast_error(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version_id: UUID,
        target_funding_at: datetime,
        decision_at: datetime,
        computed_at: datetime | None = None,
    ) -> FeatureMaterializationV2 | None:
        _aware(target_funding_at, "target_funding_at")
        _aware(decision_at, "decision_at")
        if computed_at is not None:
            _aware(computed_at, "computed_at")

        dataset = self._load_dataset(dataset_version_id, decision_at)
        realized = self._select_funding(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            kind="FUNDING_RATE_REALIZED", extra_predicate="f.target_funding_at=%s",
            predicate_params=(target_funding_at,), decision_at=decision_at,
            ambiguous_message="ambiguous_realized_funding_observation_identity",
        )
        if realized is None:
            return None
        specification = self._specification_or_none(instrument_id, decision_at)
        if specification is None or specification.kind is not CryptoInstrumentKind.PERPETUAL:
            return None
        indicative = self._select_latest_indicative(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            target_funding_at=target_funding_at, decision_at=decision_at,
        )
        if indicative is None:
            return None
        if (
            realized.convention_id != indicative.convention_id
            or realized.convention_version != indicative.convention_version
        ):
            return None

        knowledge_at = max(realized.normalized_at, indicative.normalized_at, dataset.created_at)
        if knowledge_at > decision_at:
            raise CryptoDerivativesFeatureError("knowledge_at_exceeds_decision_at")
        effective_at = max(realized.effective_at, indicative.effective_at)
        resolved_computed_at = knowledge_at if computed_at is None else computed_at
        value = (realized.funding_rate - indicative.funding_rate).quantize(_VALUE_SCALE)

        materialization = FeatureMaterializationV2.create(
            feature_id=feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=instrument_id, dataset_version=str(dataset_version_id),
            event_at=target_funding_at, effective_at=effective_at, knowledge_at=knowledge_at,
            computed_at=resolved_computed_at,
            source_observation_manifest=_funding_forecast_error_manifest_tokens(
                dataset=dataset, instrument_id=instrument_id, realized=realized,
                indicative=indicative,
            ),
            value=value, quality_status=FeatureQualityStatus.VALIDATED,
        )
        self._feature_authority.materialize_subject(materialization)
        return materialization
