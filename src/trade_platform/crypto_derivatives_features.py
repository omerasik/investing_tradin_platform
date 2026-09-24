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

**Dataset-scale basis (Phase 3D.7R.2).** :meth:`PostgresCryptoDerivativesFeatureCalculator.
iter_crypto_mark_index_basis` resolves a whole sealed dataset's MARK/INDEX
evidence in one ranked, streamed query and applies the identical per-event
rule set (:meth:`~PostgresCryptoDerivativesFeatureCalculator._basis_from_pair`)
the per-event method uses -- same values, manifests and V2 content hashes --
and ``materialize_crypto_mark_index_basis_batch`` writes it in bounded chunks
through ``PostgresFeatureAuthority.materialize_subject_stream``. The per-event
method stays the reference path.

**No AI/ML.** Every formula is a closed-form deterministic arithmetic
expression over already-authorized evidence. No provider/network call is
made.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from itertools import groupby, pairwise
from typing import Any, cast
from uuid import UUID, uuid4

from .crypto_instruments import (
    CryptoInstrumentError,
    CryptoInstrumentKind,
    CryptoInstrumentSpecification,
    PostgresCryptoInstrumentAuthority,
    ReferencePriceRequirement,
)
from .evidence_tier_authority_v1 import EvidenceTierVerdictV1
from .feature_authority import (
    FeatureDefinitionVersion,
    FeatureFamily,
    FeatureMaterializationV2,
    FeatureMaterializationV3,
    FeatureQualityStatus,
    FeatureSubjectType,
    PostgresFeatureAuthority,
)
from .knowledge_time_doctrine_v1 import (
    ObservationKnowledgeV1,
    derive_observation_knowledge_v1,
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

#: Rows fetched per round trip from a dataset-scoped evidence stream. Bounds
#: client memory; the ranked result itself is held by the server-side cursor.
_STREAM_FETCH_SIZE = 10000

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


def _strictly_increasing(event_ats: Sequence[datetime]) -> tuple[datetime, ...]:
    """Validate a batch's requested events: timezone-aware and strictly increasing."""
    requested = tuple(event_ats)
    for event_at in requested:
        _aware(event_at, "event_at")
    if any(later <= earlier for earlier, later in pairwise(requested)):
        raise CryptoDerivativesFeatureError("event_ats_must_be_strictly_increasing")
    return requested


def _select_single_identity(
    rows: Sequence[_ReferencePriceObservation], ambiguous_message: str
) -> _ReferencePriceObservation | None:
    """No eligible row -> ``None``; more than one provider identity -> fail closed."""
    if not rows:
        return None
    if len({row.provider_identifier for row in rows}) > 1:
        raise CryptoDerivativesFeatureError(ambiguous_message)
    return rows[0]


def _stream_rows(
    database: PostgresDatabase, statement: str, params: tuple[object, ...]
) -> Generator[tuple[Any, ...], None, None]:
    """Stream one query's result through a ``WITH HOLD`` server-side cursor.

    The query is evaluated once, in one snapshot, when its declaring
    transaction commits; every fetch and the final ``CLOSE`` then run in their
    own short transactions. The caller can therefore commit feature writes on
    the same connection between fetches while client memory stays bounded to
    one fetch.
    """
    with database.transaction() as connection:
        cursor = connection.cursor(name=f"feature_evidence_{uuid4().hex}", withhold=True)
        cursor.execute(statement, params)
    try:
        while True:
            with database.transaction():
                rows = cursor.fetchmany(_STREAM_FETCH_SIZE)
            if not rows:
                return
            yield from rows
    finally:
        with database.transaction():
            cursor.close()


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


#: Phase R2A.2. Same formula, eligibility and quantization as v1; a new
#: calculation version because the *timestamp semantics* changed, and a
#: semantic version bump because a consumer's decision time now means
#: something different. The v1 definition and its rows are untouched.
CALCULATION_VERSION_MARK_INDEX_BASIS_V3 = "derivatives-crypto-mark-index-basis-r2a2-three-clock-v1"
CRYPTO_MARK_INDEX_BASIS_V3_SEMANTIC_VERSION = "2.0.0"


def crypto_mark_index_basis_three_clock_definition(
    created_at: datetime,
) -> FeatureDefinitionVersion:
    """The ``crypto_mark_index_basis`` 2.0.0 definition, materialized as V3 rows only."""
    return FeatureDefinitionVersion(
        CRYPTO_MARK_INDEX_BASIS, FeatureFamily.DERIVATIVES,
        CRYPTO_MARK_INDEX_BASIS_V3_SEMANTIC_VERSION, "quant",
        "Crypto mark/index basis for one instrument: (mark_price - index_price) / "
        "index_price, with the v1 eligibility rules unchanged (exact shared event_at, "
        "same price asset, same sealed dataset, MARK_AND_INDEX instrument). Differs "
        "from v1 only in its clocks: market knowledge time comes from the R2A "
        "knowledge-time doctrine and the dataset's evidence-tier verdict, never from "
        "normalization or seal time.",
        ("MARK_PRICE", "INDEX_PRICE"), _MARK_INDEX_REQUIRED_FIELDS, "as_observed",
        "event=shared mark/index event_at; effective_at=max(mark,index effective_at); "
        "market_knowledge_at=max over mark,index of the doctrine knowledge time of each "
        "input measured from its effective_at (undefined for T0/T1); "
        "platform_recorded_at=max(mark,index normalized_at, dataset.created_at), audit only; "
        "claim_ceiling=min over inputs; computed_at operational only", 0, {},
        "fail_closed_no_materialization", "reject", "reject_future_knowledge", None, None,
        "dimensionless", CALCULATION_VERSION_MARK_INDEX_BASIS_V3, created_at,
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


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _mark_index_basis_manifest_tokens_v3(
    *, dataset: _DatasetInfo, instrument_id: str, mark: _ReferencePriceObservation,
    index: _ReferencePriceObservation, evidence_tier: EvidenceTierVerdictV1,
) -> tuple[str, ...]:
    """The V3 manifest: evidence identity only, every instant in UTC.

    Unlike the v1 manifest (kept byte-for-byte for existing V2 hashes) it
    carries no ``ingested_at`` -- a platform clock -- and formats instants in
    UTC, so the V3 identity cannot depend on ingestion time or on the
    database session's time zone.
    """
    return (
        f"historical_dataset_version_id:{dataset.dataset_version_id}",
        f"historical_dataset_content_hash:{dataset.content_hash}",
        f"source_id:{dataset.source_id}",
        f"evidence_tier_verdict_id:{evidence_tier.evidence_id}",
        f"instrument_id:{instrument_id}",
        f"mark_normalized_observation_id:{mark.normalized_observation_id}",
        f"mark_raw_observation_id:{mark.raw_observation_id}",
        f"mark_event_at:{_utc(mark.event_at)}",
        f"mark_revision:{mark.revision}",
        f"index_normalized_observation_id:{index.normalized_observation_id}",
        f"index_raw_observation_id:{index.raw_observation_id}",
        f"index_event_at:{_utc(index.event_at)}",
        f"index_revision:{index.revision}",
        f"price_asset:{mark.price_asset}",
    )


def _reference_price_from_row(row: Sequence[Any]) -> _ReferencePriceObservation:
    return _ReferencePriceObservation(
        UUID(str(row[0])), UUID(str(row[1])), str(row[2]), row[3], row[4], row[5],
        int(str(row[6])), row[7], Decimal(str(row[8])), str(row[9]),
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

    def _basis_from_pair(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset: _DatasetInfo,
        event_at: datetime,
        decision_at: datetime,
        computed_at: datetime | None,
        mark: _ReferencePriceObservation,
        index: _ReferencePriceObservation,
        specification: Callable[[], CryptoInstrumentSpecification | None],
    ) -> FeatureMaterializationV2 | None:
        """The single basis rule set once the mark/index pair is resolved.

        Shared verbatim by the per-event and the dataset-streamed paths, so both
        apply the identical coherence checks, PIT rule, formula, quantization and
        manifest -- only the evidence source differs.
        """
        if mark.price_asset != index.price_asset:
            return None
        resolved_specification = specification()
        if (
            resolved_specification is None
            or resolved_specification.reference_price_requirement
            is not ReferencePriceRequirement.MARK_AND_INDEX
        ):
            return None

        knowledge_at = max(mark.normalized_at, index.normalized_at, dataset.created_at)
        if knowledge_at > decision_at:
            raise CryptoDerivativesFeatureError("knowledge_at_exceeds_decision_at")
        effective_at = max(mark.effective_at, index.effective_at)
        resolved_computed_at = knowledge_at if computed_at is None else computed_at
        value = ((mark.price - index.price) / index.price).quantize(_VALUE_SCALE)

        return FeatureMaterializationV2.create(
            feature_id=feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=instrument_id, dataset_version=str(dataset.dataset_version_id),
            event_at=event_at, effective_at=effective_at, knowledge_at=knowledge_at,
            computed_at=resolved_computed_at,
            source_observation_manifest=_mark_index_basis_manifest_tokens(
                dataset=dataset, instrument_id=instrument_id, mark=mark, index=index,
            ),
            value=value, quality_status=FeatureQualityStatus.VALIDATED,
        )

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
        return tuple(_reference_price_from_row(row) for row in rows)

    def _select_reference_price(
        self, *, dataset_version_id: UUID, instrument_id: str, kind: str, event_at: datetime,
        decision_at: datetime, ambiguous_message: str,
    ) -> _ReferencePriceObservation | None:
        rows = self._reference_price_rows(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id, kind=kind,
            event_at=event_at, decision_at=decision_at,
        )
        return _select_single_identity(rows, ambiguous_message)

    def iter_crypto_mark_index_basis(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version_id: UUID,
        event_ats: Sequence[datetime],
        decision_at: datetime,
        computed_at: datetime | None = None,
    ) -> Iterator[FeatureMaterializationV2]:
        """Read-only, dataset-streamed form of :meth:`materialize_crypto_mark_index_basis`.

        Yields, in ``event_ats`` order, exactly the materialization the per-event
        method would build for each event (skipping the events it would skip,
        raising where and with what it would raise) -- but resolves the sealed
        dataset's MARK/INDEX evidence in ONE ranked, ``event_at``-ordered query
        streamed through a server-side cursor, instead of two dataset-wide
        lookups per event. ``event_ats`` must be strictly increasing (every
        canonical caller passes a fixed grid) so the evidence stream and the
        requested events merge in a single pass with bounded memory. Nothing is
        written; see :meth:`materialize_crypto_mark_index_basis_batch`.

        Ranking is identical to the per-event query -- one partition per
        ``(observation_kind, provider_identifier, event_at)`` ordered
        ``revision DESC, ingested_at DESC`` over the same PIT-visible,
        ``VALIDATED``, exact-instrument dataset members -- so the per-event
        ambiguity rule is judged over the identical candidate set. The
        instrument specification is resolved once at ``decision_at`` (lazily,
        at the first event that needs it) instead of once per event.
        """
        _aware(decision_at, "decision_at")
        if computed_at is not None:
            _aware(computed_at, "computed_at")
        requested = _strictly_increasing(event_ats)
        if not requested:
            return
        dataset = self._load_dataset(dataset_version_id, decision_at)
        specification = self._cached_specification(instrument_id, decision_at)
        with closing(
            self._iter_mark_index_pairs(
                dataset_version_id=dataset_version_id, instrument_id=instrument_id,
                requested=requested, decision_at=decision_at,
            )
        ) as pairs:
            for event_at, mark, index in pairs:
                materialization = self._basis_from_pair(
                    feature_id=feature_id, instrument_id=instrument_id, dataset=dataset,
                    event_at=event_at, decision_at=decision_at, computed_at=computed_at,
                    mark=mark, index=index, specification=specification,
                )
                if materialization is not None:
                    yield materialization

    def _cached_specification(
        self, instrument_id: str, decision_at: datetime
    ) -> Callable[[], CryptoInstrumentSpecification | None]:
        """Resolve the instrument specification once, lazily, at ``decision_at``."""
        cache: list[CryptoInstrumentSpecification | None] = []

        def specification() -> CryptoInstrumentSpecification | None:
            if not cache:
                cache.append(self._specification_or_none(instrument_id, decision_at))
            return cache[0]

        return specification

    def _iter_mark_index_pairs(
        self,
        *,
        dataset_version_id: UUID,
        instrument_id: str,
        requested: tuple[datetime, ...],
        decision_at: datetime,
    ) -> Generator[tuple[datetime, _ReferencePriceObservation, _ReferencePriceObservation], None, None]:
        """The one streamed, ranked MARK/INDEX pairing shared by the V2 and V3 paths."""
        statement = (
            "SELECT observation_kind, normalized_observation_id, raw_observation_id, "
            "provider_identifier, event_at, effective_at, ingested_at, revision, normalized_at, "
            "price, price_asset FROM (SELECT r.observation_kind, n.normalized_observation_id, "
            "r.raw_observation_id, r.provider_identifier, r.event_at, r.effective_at, "
            "r.ingested_at, r.revision, n.normalized_at, p.price, p.price_asset, ROW_NUMBER() OVER ("
            "PARTITION BY r.observation_kind, r.provider_identifier, r.event_at "
            "ORDER BY r.revision DESC, r.ingested_at DESC) AS rnk "
            "FROM historical_dataset_members m "
            "JOIN historical_normalized_observations n ON n.normalized_observation_id=m.normalized_observation_id "
            "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
            "JOIN crypto_reference_price_observations p ON p.normalized_observation_id=n.normalized_observation_id "
            "WHERE m.dataset_version_id=%s AND n.instrument_id=%s "
            "AND r.observation_kind IN ('MARK_PRICE','INDEX_PRICE') "
            "AND n.quality_status='VALIDATED' AND n.normalized_at<=%s AND r.ingested_at<=%s "
            "AND r.event_at>=%s AND r.event_at<=%s) ranked WHERE rnk=1 ORDER BY event_at"
        )
        params = (
            dataset_version_id, instrument_id, decision_at, decision_at, requested[0], requested[-1],
        )
        stream = _stream_rows(self._database, statement, params)
        groups = groupby(stream, key=lambda row: cast(datetime, row[4]))
        try:
            pending = next(groups, None)
            for event_at in requested:
                while pending is not None and pending[0] < event_at:
                    pending = next(groups, None)
                marks: list[_ReferencePriceObservation] = []
                indexes: list[_ReferencePriceObservation] = []
                if pending is not None and pending[0] == event_at:
                    for row in pending[1]:
                        target = marks if str(row[0]) == "MARK_PRICE" else indexes
                        target.append(_reference_price_from_row(row[1:]))
                    pending = next(groups, None)
                mark = _select_single_identity(marks, "ambiguous_mark_observation_identity")
                if mark is None:
                    continue
                index = _select_single_identity(indexes, "ambiguous_index_observation_identity")
                if index is None:
                    continue
                yield event_at, mark, index
        finally:
            stream.close()

    # ---- crypto_mark_index_basis, three-clock (Phase R2A.2) -----------------

    def _basis_v3_from_pair(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset: _DatasetInfo,
        evidence_tier: EvidenceTierVerdictV1,
        event_at: datetime,
        platform_as_of: datetime,
        computed_at: datetime | None,
        mark: _ReferencePriceObservation,
        index: _ReferencePriceObservation,
        specification: Callable[[], CryptoInstrumentSpecification | None],
    ) -> FeatureMaterializationV3 | None:
        """The V2 rule set and formula, with knowledge time from the doctrine.

        Eligibility, value, quantization and manifest are exactly
        :meth:`_basis_from_pair`'s. What differs is the clock: each input's
        market knowledge time is derived by
        :func:`~trade_platform.knowledge_time_doctrine_v1.derive_observation_knowledge_v1`
        from the dataset's evidence-tier verdict, and the feature's is their
        maximum. ``normalized_at`` and the dataset seal time enter only as the
        observation's ``platform_recorded_at`` -- audit, never knowledge.

        An observation's doctrine ``event_at`` is its ``effective_at`` -- the
        instant its value is complete. For the Bybit mark/index inputs the two
        already coincide (their ``event_at`` is the bar close); measuring from
        ``effective_at`` keeps the rule look-ahead-free for any source whose
        ``event_at`` is a bar open.
        """
        if mark.price_asset != index.price_asset:
            return None
        resolved_specification = specification()
        if (
            resolved_specification is None
            or resolved_specification.reference_price_requirement
            is not ReferencePriceRequirement.MARK_AND_INDEX
        ):
            return None

        def observed(observation: _ReferencePriceObservation) -> ObservationKnowledgeV1:
            return derive_observation_knowledge_v1(
                evidence_tier,
                dataset_version_id=dataset.dataset_version_id,
                dataset_content_hash=dataset.content_hash,
                observation_reference=(
                    f"historical_normalized_observation:{observation.normalized_observation_id}"
                ),
                event_at=observation.effective_at,
                platform_recorded_at=max(observation.normalized_at, dataset.created_at),
            )

        inputs = (observed(mark), observed(index))
        platform_recorded_at = max(item.platform_recorded_at for item in inputs)
        if platform_recorded_at > platform_as_of:
            raise CryptoDerivativesFeatureError("platform_recorded_at_exceeds_platform_as_of")
        value = ((mark.price - index.price) / index.price).quantize(_VALUE_SCALE)
        return FeatureMaterializationV3.create(
            feature_id=feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=instrument_id, dataset_version=str(dataset.dataset_version_id),
            event_at=event_at, effective_at=max(mark.effective_at, index.effective_at),
            inputs=inputs,
            computed_at=platform_recorded_at if computed_at is None else computed_at,
            source_observation_manifest=_mark_index_basis_manifest_tokens_v3(
                dataset=dataset, instrument_id=instrument_id, mark=mark, index=index,
                evidence_tier=evidence_tier,
            ),
            value=value, quality_status=FeatureQualityStatus.VALIDATED,
        )

    def iter_crypto_mark_index_basis_v3(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version_id: UUID,
        evidence_tier: EvidenceTierVerdictV1,
        event_ats: Sequence[datetime],
        platform_as_of: datetime,
        computed_at: datetime | None = None,
    ) -> Iterator[FeatureMaterializationV3]:
        """Three-clock form of :meth:`iter_crypto_mark_index_basis`. Read-only.

        ``platform_as_of`` is the V2 path's ``decision_at`` under its honest
        name: which rows *this platform* held when it computed (normalized and
        ingested by then, dataset sealed by then). It never becomes a market
        clock. ``evidence_tier`` must be the verdict of exactly this dataset;
        the doctrine then refuses it unless it is also bound to the dataset's
        content hash. A T1 dataset yields rows whose market knowledge time is
        undefined and whose claim is at most ``DESCRIPTIVE`` -- real values,
        no decision authority. ``computed_at`` defaults to the platform
        availability instant; it is not part of the V3 identity either way.
        """
        _aware(platform_as_of, "platform_as_of")
        if computed_at is not None:
            _aware(computed_at, "computed_at")
        if evidence_tier.dataset_version_id != dataset_version_id:
            raise CryptoDerivativesFeatureError("evidence_tier_verdict_dataset_mismatch")
        requested = _strictly_increasing(event_ats)
        if not requested:
            return
        dataset = self._load_dataset(dataset_version_id, platform_as_of)
        specification = self._cached_specification(instrument_id, platform_as_of)
        with closing(
            self._iter_mark_index_pairs(
                dataset_version_id=dataset_version_id, instrument_id=instrument_id,
                requested=requested, decision_at=platform_as_of,
            )
        ) as pairs:
            for event_at, mark, index in pairs:
                materialization = self._basis_v3_from_pair(
                    feature_id=feature_id, instrument_id=instrument_id, dataset=dataset,
                    evidence_tier=evidence_tier, event_at=event_at,
                    platform_as_of=platform_as_of, computed_at=computed_at, mark=mark,
                    index=index, specification=specification,
                )
                if materialization is not None:
                    yield materialization

    def materialize_crypto_mark_index_basis_v3_batch(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version_id: UUID,
        evidence_tier: EvidenceTierVerdictV1,
        event_ats: Sequence[datetime],
        platform_as_of: datetime,
        computed_at: datetime | None = None,
    ) -> int:
        """Write :meth:`iter_crypto_mark_index_basis_v3` through the V3 stream writer."""
        return self._feature_authority.materialize_subject_stream_v3(
            self.iter_crypto_mark_index_basis_v3(
                feature_id=feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset_version_id, evidence_tier=evidence_tier,
                event_ats=event_ats, platform_as_of=platform_as_of, computed_at=computed_at,
            )
        )

    def materialize_crypto_mark_index_basis_batch(
        self,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version_id: UUID,
        event_ats: Sequence[datetime],
        decision_at: datetime,
        computed_at: datetime | None = None,
    ) -> int:
        """Materialize :meth:`iter_crypto_mark_index_basis` in bounded chunks.

        Writes only through the canonical
        :meth:`PostgresFeatureAuthority.materialize_subject_stream`; returns the
        number of materializations written or reconciled as identical -- the same
        count the per-event loop's non-``None`` results would give.
        """
        return self._feature_authority.materialize_subject_stream(
            self.iter_crypto_mark_index_basis(
                feature_id=feature_id, instrument_id=instrument_id,
                dataset_version_id=dataset_version_id, event_ats=event_ats,
                decision_at=decision_at, computed_at=computed_at,
            )
        )

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
        materialization = self._basis_from_pair(
            feature_id=feature_id, instrument_id=instrument_id, dataset=dataset,
            event_at=event_at, decision_at=decision_at, computed_at=computed_at,
            mark=mark, index=index,
            specification=lambda: self._specification_or_none(instrument_id, decision_at),
        )
        if materialization is None:
            return None
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
