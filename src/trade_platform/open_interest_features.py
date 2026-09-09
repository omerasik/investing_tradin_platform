"""Deterministic Module 3J.1b open-interest-change feature calculator.

**Feature Authority extension, not a second authority.** This module
registers exactly one ``FeatureFamily.DERIVATIVES`` feature definition
(``open_interest_change``, from the 3J.1 proposal
``docs/MODULE_3J1_PROPOSAL_MULTI_ASSET_DERIVATIVES_FEATURE_PACK.md`` section
3.4) and computes its value, but every durable value it produces is written
through the existing, unmodified
:class:`trade_platform.feature_authority.PostgresFeatureAuthority`
(``register`` / ``materialize_subject``). This module owns no table, no
subject registry and no dataset registry of its own -- it is a stateless
calculator over already-authoritative evidence.

**One cross-asset definition.** ``open_interest_change`` is registered once
and applies uniformly to a futures contract (``CONTRACTS``) and a crypto
instrument (``CONTRACTS`` / ``BASE_ASSET`` / ``QUOTE_NOTIONAL``), because
3I.1/3I.2 already created one canonical, cross-asset
``open_interest_observations`` authority. No ``futures_open_interest_change``
or ``crypto_open_interest_change`` split is introduced, and raw OI *level* is
never separately materialized -- only the change.

**Canonical evidence chain.** Every observation this module reads resolves
strictly through ``historical_dataset_members`` ->
``historical_normalized_observations`` -> ``historical_raw_observations`` ->
``open_interest_observations``. The canonical numeric OI value comes only
from ``open_interest_observations.open_interest`` -- never the envelope's
``normalized_value`` pointer marker.

**Dataset identity.** ``FeatureMaterializationV2.dataset_version`` is always
``str(dataset_version_id)`` -- the caller-declared sealed
``historical_dataset_versions.dataset_version_id`` both OI observations must
be members of. No cross-dataset pair is ever formed: both the current and
the prior observation queries are scoped to this exact dataset.

**Unit semantics.** OI keeps its native canonical unit; no conversion is
ever performed. The prior observation is only eligible when its ``unit`` and
``unit_asset`` are identical to the current observation's -- this is baked
directly into the prior-candidate query, not a post-hoc check, so a
unit-mismatched candidate is simply not "eligible" and the search does not
fall back to it.

**Prior selection.** ``OI[t-1]`` is the most recent eligible prior
observation for the same instrument, same sealed dataset, same unit and
unit_asset, ``event_at < current_event_at``, individually PIT-visible at
``decision_at``. Revisions of the same canonical observation (same
``provider_identifier``, same ``event_at``) are resolved by
``revision DESC, ingested_at DESC`` -- the same convention the existing
historical authority already uses (``research_query`` /
``PostgresHistoricalMarketDataPipeline``). If multiple independent
``provider_identifier``s remain after revision resolution for the same
instrument at the same event instant, the pairing is ambiguous and this
module fails closed rather than choosing arbitrarily -- for both the current
observation and the selected prior. If no eligible prior observation exists,
no feature materialization is written; this is not an error.

**PIT semantics.** Every input independently satisfies
``normalized_at <= decision_at`` and ``ingested_at <= decision_at``, and the
sealed dataset itself must be knowable (``created_at <= decision_at``).
``event_at`` is the current OI event instant; ``effective_at`` is never
earlier than either input's own ``effective_at``; ``knowledge_at`` is the
maximum of both inputs' ``normalized_at`` and the dataset's ``created_at``;
``computed_at >= knowledge_at`` (defaults to ``knowledge_at``). A later,
independently-ingested revision can never mutate or backdate an earlier
materialization -- the underlying Feature Authority is already append-only
and PIT-gated.

**Quality semantics.** Every materialization this module writes is
``VALIDATED`` -- fail-closed conditions (missing prior, unit/unit_asset
mismatch, ambiguous identity, unsealed/unknowable dataset, missing current
observation) never produce a ``DEGRADED`` or ``REJECTED`` row; they either
raise :class:`OpenInterestFeatureError` (a definite precondition failure) or,
for a genuinely absent prior observation, silently produce no
materialization at all.

**No AI/ML.** The formula is a closed-form deterministic subtraction over
already-authorized evidence. No provider/network call is made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
from .persistence import PostgresDatabase

#: Identifies exactly this 3J.1b implementation. Never reused across a
#: behavioural change to the formula below -- a changed formula requires a
#: new calculation_version (and a new semantic_version).
CALCULATION_VERSION = "derivatives-open-interest-change-3j1b-v1"

OPEN_INTEREST_CHANGE = "open_interest_change"

#: ``feature_materializations.value`` is ``NUMERIC(38,12)`` while
#: ``open_interest_observations.open_interest`` is ``NUMERIC(38,18)``. The
#: exact native delta is computed first, then quantized to this exact column
#: scale before it is hashed and written -- otherwise the value PostgreSQL
#: actually stores would silently diverge from the one this module hashed.
#: Same convention as ``derivatives_features._VALUE_SCALE``.
_VALUE_SCALE = Decimal("1E-12")

_REQUIRED_FIELDS = ("open_interest", "unit", "unit_asset", "event_at", "revision", "ingested_at")


class OpenInterestFeatureError(ValueError):
    """Base class: every failure path in this module is fail-closed."""


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OpenInterestFeatureError(f"{name}_must_be_timezone_aware")


def open_interest_change_definition(created_at: datetime) -> FeatureDefinitionVersion:
    """The single v1 3J.1b ``open_interest_change`` feature definition. Register once."""
    return FeatureDefinitionVersion(
        OPEN_INTEREST_CHANGE, FeatureFamily.DERIVATIVES, "1.0.0", "quant",
        "Cross-asset, per-instrument open-interest change: OI[t] - OI[t-1]. Both "
        "observations must be members of the exact same sealed historical dataset "
        "version, share identical unit and unit_asset (never converted), and be "
        "individually PIT-visible. Applies uniformly to a futures contract "
        "(CONTRACTS) and a crypto instrument (CONTRACTS/BASE_ASSET/QUOTE_NOTIONAL) "
        "via the single canonical open_interest_observations authority. Raw OI "
        "level is not separately materialized as its own feature.",
        ("OPEN_INTEREST",), _REQUIRED_FIELDS, "as_observed",
        "event=current OI event_at; effective_at>=max(current,prior effective_at); "
        "knowledge_at=max(current,prior normalized_at, dataset.created_at); "
        "computed_at>=knowledge_at", 1, {}, "fail_closed_no_materialization", "reject",
        "reject_future_knowledge", None, None, "native_open_interest_unit",
        CALCULATION_VERSION, created_at,
    )


@dataclass(frozen=True, slots=True)
class _DatasetInfo:
    dataset_version_id: UUID
    content_hash: str
    source_id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _OIObservation:
    normalized_observation_id: UUID
    raw_observation_id: UUID
    provider_identifier: str
    event_at: datetime
    effective_at: datetime
    ingested_at: datetime
    revision: int
    normalized_at: datetime
    open_interest: Decimal
    unit: str
    unit_asset: str | None


def _manifest_tokens(
    *, dataset: _DatasetInfo, instrument_id: str, current: _OIObservation, prior: _OIObservation,
) -> tuple[str, ...]:
    """Deterministic canonical provenance tokens. Same evidence -> identical manifest.

    Every token is a resolvable canonical id, never a human-readable label
    alone, per the 3J.1 proposal section 6/11 manifest contract. Fixed tuple
    order -- never dependent on iteration/dict order.
    """
    return (
        f"historical_dataset_version_id:{dataset.dataset_version_id}",
        f"historical_dataset_content_hash:{dataset.content_hash}",
        f"source_id:{dataset.source_id}",
        f"instrument_id:{instrument_id}",
        f"current_normalized_observation_id:{current.normalized_observation_id}",
        f"current_raw_observation_id:{current.raw_observation_id}",
        f"current_event_at:{current.event_at.isoformat()}",
        f"current_revision:{current.revision}",
        f"current_ingested_at:{current.ingested_at.isoformat()}",
        f"prior_normalized_observation_id:{prior.normalized_observation_id}",
        f"prior_raw_observation_id:{prior.raw_observation_id}",
        f"prior_event_at:{prior.event_at.isoformat()}",
        f"prior_revision:{prior.revision}",
        f"prior_ingested_at:{prior.ingested_at.isoformat()}",
        f"unit:{current.unit}",
        f"unit_asset:{current.unit_asset if current.unit_asset is not None else 'NULL'}",
    )


class PostgresOpenInterestFeatureCalculator:
    """Computes and materializes the 3J.1b ``open_interest_change`` feature."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._feature_authority = PostgresFeatureAuthority(database)

    def _load_dataset(self, dataset_version_id: UUID, decision_at: datetime) -> _DatasetInfo:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, created_at, content_hash, source_id FROM "
                "historical_dataset_versions WHERE dataset_version_id=%s",
                (dataset_version_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise OpenInterestFeatureError("dataset_not_found")
        if str(row[0]) != "SEALED":
            raise OpenInterestFeatureError("dataset_not_sealed")
        created_at = cast(datetime, row[1])
        if created_at > decision_at:
            raise OpenInterestFeatureError("dataset_not_knowable_at_decision_at")
        return _DatasetInfo(dataset_version_id, str(row[2]), cast(UUID, row[3]), created_at)

    def _eligible_rows(
        self,
        *,
        dataset_version_id: UUID,
        instrument_id: str,
        decision_at: datetime,
        event_at_predicate: str,
        predicate_params: tuple[object, ...],
    ) -> tuple[_OIObservation, ...]:
        statement = (
            "SELECT normalized_observation_id, raw_observation_id, provider_identifier, "
            "event_at, effective_at, ingested_at, revision, normalized_at, open_interest, "
            "unit, unit_asset FROM (SELECT n.normalized_observation_id, r.raw_observation_id, "
            "r.provider_identifier, r.event_at, r.effective_at, r.ingested_at, r.revision, "
            "n.normalized_at, o.open_interest, o.unit, o.unit_asset, ROW_NUMBER() OVER ("
            "PARTITION BY r.provider_identifier, r.event_at "
            "ORDER BY r.revision DESC, r.ingested_at DESC) AS rnk "
            "FROM historical_dataset_members m "
            "JOIN historical_normalized_observations n ON n.normalized_observation_id=m.normalized_observation_id "
            "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
            "JOIN open_interest_observations o ON o.normalized_observation_id=n.normalized_observation_id "
            "WHERE m.dataset_version_id=%s AND n.instrument_id=%s "
            "AND r.observation_kind='OPEN_INTEREST' AND n.quality_status='VALIDATED' "
            "AND n.normalized_at<=%s AND r.ingested_at<=%s "
            f"AND {event_at_predicate}"  # nosec B608 - fixed policy fragments only
            ") ranked WHERE rnk=1 ORDER BY event_at DESC"
        )
        params = (dataset_version_id, instrument_id, decision_at, decision_at, *predicate_params)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(statement, params)
            rows = cursor.fetchall()
        return tuple(
            _OIObservation(
                UUID(str(row[0])), UUID(str(row[1])), str(row[2]), row[3], row[4], row[5],
                int(str(row[6])), row[7], Decimal(str(row[8])), str(row[9]),
                None if row[10] is None else str(row[10]),
            )
            for row in rows
        )

    def _select_current(
        self, *, dataset_version_id: UUID, instrument_id: str, event_at: datetime, decision_at: datetime,
    ) -> _OIObservation:
        rows = self._eligible_rows(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            decision_at=decision_at, event_at_predicate="r.event_at=%s",
            predicate_params=(event_at,),
        )
        if not rows:
            raise OpenInterestFeatureError("current_observation_not_found")
        if len({row.provider_identifier for row in rows}) > 1:
            raise OpenInterestFeatureError("ambiguous_current_observation_identity")
        return rows[0]

    def _select_prior(
        self,
        *,
        dataset_version_id: UUID,
        instrument_id: str,
        current: _OIObservation,
        decision_at: datetime,
    ) -> _OIObservation | None:
        rows = self._eligible_rows(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            decision_at=decision_at,
            event_at_predicate="r.event_at<%s AND o.unit=%s AND o.unit_asset IS NOT DISTINCT FROM %s",
            predicate_params=(current.event_at, current.unit, current.unit_asset),
        )
        if not rows:
            return None
        latest_event_at = rows[0].event_at
        top_group = tuple(row for row in rows if row.event_at == latest_event_at)
        if len({row.provider_identifier for row in top_group}) > 1:
            raise OpenInterestFeatureError("ambiguous_prior_observation_identity")
        return top_group[0]

    def materialize_open_interest_change(
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
        current = self._select_current(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            event_at=event_at, decision_at=decision_at,
        )
        prior = self._select_prior(
            dataset_version_id=dataset_version_id, instrument_id=instrument_id,
            current=current, decision_at=decision_at,
        )
        if prior is None:
            return None

        knowledge_at = max(current.normalized_at, prior.normalized_at, dataset.created_at)
        if knowledge_at > decision_at:
            raise OpenInterestFeatureError("knowledge_at_exceeds_decision_at")
        effective_at = max(current.effective_at, prior.effective_at)
        resolved_computed_at = knowledge_at if computed_at is None else computed_at
        value = (current.open_interest - prior.open_interest).quantize(_VALUE_SCALE)

        materialization = FeatureMaterializationV2.create(
            feature_id=feature_id, subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=instrument_id, dataset_version=str(dataset_version_id),
            event_at=event_at, effective_at=effective_at, knowledge_at=knowledge_at,
            computed_at=resolved_computed_at,
            source_observation_manifest=_manifest_tokens(
                dataset=dataset, instrument_id=instrument_id, current=current, prior=prior,
            ),
            value=value, quality_status=FeatureQualityStatus.VALIDATED,
        )
        self._feature_authority.materialize_subject(materialization)
        return materialization
