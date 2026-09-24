"""PostgreSQL authority for versioned, point-in-time feature materializations.

The older ``feature_platform`` module remains a small deterministic calculator.
This module is the durable authority: every definition version and every value
is explicit about dataset, knowledge time, input manifest and calculation code.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from .evidence_tier_authority_v1 import EvidenceTierVerdictV1
from .knowledge_time_doctrine_v1 import (
    ClaimCeilingV1,
    FeatureKnowledgeV1,
    KnowledgeTimeDoctrineError,
    ObservationKnowledgeV1,
    RestoredFeatureKnowledgeV1,
    SealedClockResolverV1,
    SealedObservationClocksV1,
    persisted_observation_knowledge_v1,
    persisted_observation_market_hash_v1,
    propagate_feature_knowledge_v1,
    rederive_observation_knowledge_v1,
    restore_persisted_feature_knowledge_v1,
)
from .persistence import PostgresDatabase


class FeatureAuthorityError(ValueError):
    pass


#: Upper bound on one :meth:`PostgresFeatureAuthority.materialize_subjects`
#: chunk -- one transaction, one array-bound INSERT and one array-bound
#: reconciliation read. Every column travels as a single array parameter, so
#: the statement's bind-parameter count is constant, never per-row.
FEATURE_MATERIALIZATION_BATCH_MAX = 5000


class FeatureFamily(StrEnum):
    PRICE_RETURNS = "PRICE_RETURNS"
    TREND = "TREND"
    MOMENTUM = "MOMENTUM"
    VOLATILITY = "VOLATILITY"
    LIQUIDITY = "LIQUIDITY"
    FUNDAMENTAL = "FUNDAMENTAL"
    MACRO = "MACRO"
    #: Module 3J.1. One family for every derivatives feature (futures curve,
    #: open interest, crypto mark/index/funding) -- see the 3J.1 proposal
    #: doc section 5/8 for why no per-shape family split is introduced.
    DERIVATIVES = "DERIVATIVES"


class FeatureQualityStatus(StrEnum):
    VALIDATED = "VALIDATED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


class FeatureSubjectType(StrEnum):
    """What a feature materialization is *about*. Module 3J.0.

    ``INSTRUMENT`` resolves against the existing canonical instrument
    authority (``professional_instruments``); ``FUTURES_SERIES`` resolves
    against the existing 3H.1 futures-series/root authority
    (``futures_contract_series``). No other subject kind is supported yet --
    an account, portfolio, strategy, market or sector subject fails closed
    rather than being silently accepted as text.
    """

    INSTRUMENT = "INSTRUMENT"
    FUTURES_SERIES = "FUTURES_SERIES"


class FeatureHashVersion(StrEnum):
    """Which content-hash formula produced a stored materialization's hash.

    ``V1`` is the pre-3J.0 formula (keyed on ``instrument_id``), produced by
    the unchanged :meth:`PostgresFeatureAuthority.materialize` /
    :meth:`FeatureMaterialization.create` path and never recomputed.  ``V2``
    is the generalized subject-aware formula (keyed on ``subject_type`` and
    ``subject_id`` explicitly), produced only by
    :meth:`PostgresFeatureAuthority.materialize_subject` /
    :meth:`FeatureMaterializationV2.create`. The two are structurally
    distinct payload shapes, not merely different field values, so a V2
    ``INSTRUMENT`` row can never collide with a V1 row for the same
    instrument.
    """

    V1 = "V1"
    V2 = "V2"
    #: Phase R2A.2 three-clock identity -- see :class:`FeatureMaterializationV3`.
    V3 = "V3"


@dataclass(frozen=True, slots=True)
class TransparentMarketWindow:
    """Offline, ordered market inputs used only for deterministic calculation."""

    closes: tuple[Decimal, ...]
    highs: tuple[Decimal, ...]
    lows: tuple[Decimal, ...]
    volumes: tuple[Decimal, ...]
    shares_outstanding: Decimal | None = None

    def validate(self) -> None:
        if (
            len(self.closes) < 2
            or len(self.closes) != len(self.highs) != len(self.lows) != len(self.volumes)
            or any(value <= 0 for value in self.closes)
            or any(value <= 0 for value in self.highs)
            or any(value <= 0 for value in self.lows)
            or any(value < 0 for value in self.volumes)
            or any(low > close or close > high for low, close, high in zip(self.lows, self.closes, self.highs, strict=True))
            or (self.shares_outstanding is not None and self.shares_outstanding <= 0)
        ):
            raise FeatureAuthorityError("invalid_transparent_market_window")


class TransparentFeatureCalculator:
    """Auditable baselines; it never fills an unavailable fundamental/macro input."""

    @staticmethod
    def calculate(name: str, window: TransparentMarketWindow) -> Decimal:
        window.validate()
        closes, highs, lows, volumes = window.closes, window.highs, window.lows, window.volumes
        returns = tuple(closes[index] / closes[index - 1] - Decimal("1") for index in range(1, len(closes)))
        average_close = sum(closes, Decimal("0")) / Decimal(len(closes))
        if name in {"simple_return", "multi_horizon_return", "multi_horizon_momentum"}:
            return closes[-1] / closes[0] - Decimal("1")
        if name == "log_return":
            return (closes[-1] / closes[0]).ln()
        if name == "sma":
            return average_close
        if name == "ema":
            alpha = Decimal("2") / Decimal(len(closes) + 1)
            result = closes[0]
            for close in closes[1:]:
                result = alpha * close + (Decimal("1") - alpha) * result
            return result
        if name == "moving_average_distance":
            return closes[-1] / average_close - Decimal("1")
        if name == "breakout":
            return closes[-1] / max(closes) - Decimal("1")
        if name == "trend_slope":
            indexes: tuple[Decimal, ...] = tuple(Decimal(index) for index in range(len(closes)))
            mean_index = sum(indexes, Decimal("0")) / Decimal(len(indexes))
            numerator = sum(((index - mean_index) * (close - average_close) for index, close in zip(indexes, closes, strict=True)), Decimal("0"))
            denominator = sum(((index - mean_index) ** 2 for index in indexes), Decimal("0"))
            return numerator / denominator
        if name in {"realized_volatility", "downside_volatility"}:
            selected = returns if name == "realized_volatility" else tuple(min(item, Decimal("0")) for item in returns)
            average = sum(selected, Decimal("0")) / Decimal(len(selected))
            return (sum((item - average) ** 2 for item in selected) / Decimal(len(selected))).sqrt()
        if name == "range_volatility":
            return sum(((high - low) / close for high, low, close in zip(highs, lows, closes, strict=True)), Decimal("0")) / Decimal(len(closes))
        if name == "risk_adjusted_momentum":
            volatility = TransparentFeatureCalculator.calculate("realized_volatility", window)
            if volatility == 0:
                raise FeatureAuthorityError("risk_adjusted_momentum_zero_volatility")
            return TransparentFeatureCalculator.calculate("multi_horizon_momentum", window) / volatility
        if name == "dollar_volume":
            return closes[-1] * volumes[-1]
        if name == "adv":
            return sum((close * volume for close, volume in zip(closes, volumes, strict=True)), Decimal("0")) / Decimal(len(closes))
        if name == "turnover":
            if window.shares_outstanding is None:
                raise FeatureAuthorityError("turnover_requires_shares_outstanding")
            return volumes[-1] / window.shares_outstanding
        if name == "amihud_proxy":
            denominators = tuple(close * volume for close, volume in zip(closes[1:], volumes[1:], strict=True))
            if any(item == 0 for item in denominators):
                raise FeatureAuthorityError("amihud_requires_positive_dollar_volume")
            return sum((abs(change) / dollar_volume for change, dollar_volume in zip(returns, denominators, strict=True)), Decimal("0")) / Decimal(len(returns))
        raise FeatureAuthorityError("unsupported_transparent_feature")


def transparent_feature_definitions(created_at: datetime) -> tuple[FeatureDefinitionVersion, ...]:
    """Definitions, not values: unavailable real inputs intentionally stay absent."""
    market = (
        ("simple_return", FeatureFamily.PRICE_RETURNS), ("log_return", FeatureFamily.PRICE_RETURNS),
        ("multi_horizon_return", FeatureFamily.PRICE_RETURNS), ("sma", FeatureFamily.TREND),
        ("ema", FeatureFamily.TREND), ("moving_average_distance", FeatureFamily.TREND),
        ("breakout", FeatureFamily.TREND), ("trend_slope", FeatureFamily.TREND),
        ("multi_horizon_momentum", FeatureFamily.MOMENTUM), ("risk_adjusted_momentum", FeatureFamily.MOMENTUM),
        ("realized_volatility", FeatureFamily.VOLATILITY), ("downside_volatility", FeatureFamily.VOLATILITY),
        ("range_volatility", FeatureFamily.VOLATILITY), ("dollar_volume", FeatureFamily.LIQUIDITY),
        ("adv", FeatureFamily.LIQUIDITY), ("turnover", FeatureFamily.LIQUIDITY),
        ("amihud_proxy", FeatureFamily.LIQUIDITY),
    )
    fundamentals = ("revenue_growth", "operating_margin", "fcf", "debt", "dilution", "roic_components")
    macro = ("rate_level_change", "curve_slope", "inflation_change", "employment_change", "revision_or_surprise")
    definitions: list[FeatureDefinitionVersion] = []
    for name, family in market:
        definitions.append(FeatureDefinitionVersion(name, family, "1.0.0", "quant", f"Transparent {name} baseline.", ("OHLCV",), ("close", "event_at", "knowledge_at"), "1d", "event/effective/knowledge bounded", 1, {}, "reject", "reject", "reject_future_knowledge", None, None, "decimal", "transparent-market-v1", created_at))
    for name, family, dataset, fields in (
        *((name, FeatureFamily.FUNDAMENTAL, "FUNDAMENTALS", ("accepted_at", "value", "revision")) for name in fundamentals),
        *((name, FeatureFamily.MACRO, "MACRO", ("release_at", "actual", "revision")) for name in macro),
    ):
        definitions.append(FeatureDefinitionVersion(name, family, "1.0.0", "quant", f"Declared {name} baseline; materialize only with authorized PIT input.", (dataset,), fields, "release", "event/effective/knowledge bounded", 0, {}, "reject", "reject", "reject_future_knowledge", None, None, "decimal", "transparent-input-v1", created_at))
    return tuple(definitions)


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FeatureAuthorityError("feature_timestamp_must_be_timezone_aware")


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class FeatureDefinitionVersion:
    name: str
    family: FeatureFamily
    semantic_version: str
    owner: str
    description: str
    required_dataset_types: tuple[str, ...]
    required_fields: tuple[str, ...]
    frequency: str
    timestamp_semantics: str
    lookback: int
    parameters: Mapping[str, Any]
    missing_value_policy: str
    outlier_policy: str
    leakage_policy: str
    expected_minimum: Decimal | None
    expected_maximum: Decimal | None
    units: str
    calculation_version: str
    created_at: datetime
    retired_at: datetime | None = None
    feature_id: UUID = field(default_factory=uuid4)

    def validate(self) -> None:
        required = (
            self.name, self.semantic_version, self.owner, self.description,
            self.frequency, self.timestamp_semantics, self.missing_value_policy,
            self.outlier_policy, self.leakage_policy, self.units,
            self.calculation_version,
        )
        if not all(item.strip() for item in required) or self.lookback < 0:
            raise FeatureAuthorityError("invalid_feature_definition")
        if not self.required_dataset_types or not self.required_fields:
            raise FeatureAuthorityError("feature_requirements_missing")
        if (
            self.expected_minimum is not None
            and self.expected_maximum is not None
            and self.expected_minimum > self.expected_maximum
        ):
            raise FeatureAuthorityError("invalid_feature_expected_range")
        _aware(self.created_at)
        if self.retired_at is not None:
            _aware(self.retired_at)
            if self.retired_at < self.created_at:
                raise FeatureAuthorityError("feature_retired_before_creation")


@dataclass(frozen=True, slots=True)
class FeatureMaterialization:
    feature_id: UUID
    instrument_id: str
    dataset_version: str
    event_at: datetime
    effective_at: datetime
    knowledge_at: datetime
    computed_at: datetime
    source_observation_manifest: tuple[str, ...]
    value: Decimal | None
    quality_status: FeatureQualityStatus
    content_hash: str
    materialization_id: UUID = field(default_factory=uuid4)

    @classmethod
    def create(
        cls,
        *,
        feature_id: UUID,
        instrument_id: str,
        dataset_version: str,
        event_at: datetime,
        effective_at: datetime,
        knowledge_at: datetime,
        computed_at: datetime,
        source_observation_manifest: tuple[str, ...],
        value: Decimal | None,
        quality_status: FeatureQualityStatus,
    ) -> FeatureMaterialization:
        payload = {
            "feature_id": str(feature_id), "instrument_id": instrument_id,
            "dataset_version": dataset_version, "event_at": event_at.isoformat(),
            "effective_at": effective_at.isoformat(), "knowledge_at": knowledge_at.isoformat(),
            "computed_at": computed_at.isoformat(),
            "source_observation_manifest": source_observation_manifest,
            "value": None if value is None else str(value),
            "quality_status": quality_status.value,
        }
        return cls(
            feature_id, instrument_id, dataset_version, event_at, effective_at,
            knowledge_at, computed_at, source_observation_manifest, value,
            quality_status, hashlib.sha256(_canonical(payload).encode()).hexdigest(),
        )

    def validate(self) -> None:
        if not self.instrument_id.strip() or not self.dataset_version.strip():
            raise FeatureAuthorityError("feature_materialization_identity_missing")
        if len(self.content_hash) != 64 or not self.source_observation_manifest:
            raise FeatureAuthorityError("feature_materialization_provenance_missing")
        for timestamp in (self.event_at, self.effective_at, self.knowledge_at, self.computed_at):
            _aware(timestamp)
        if self.effective_at < self.event_at or self.knowledge_at < self.effective_at:
            raise FeatureAuthorityError("feature_materialization_invalid_temporal_order")
        if self.computed_at < self.knowledge_at:
            raise FeatureAuthorityError("feature_computed_before_knowledge")
        if self.quality_status is FeatureQualityStatus.VALIDATED and self.value is None:
            raise FeatureAuthorityError("validated_feature_requires_value")


@dataclass(frozen=True, slots=True)
class FeatureMaterializationV2:
    """The generalized subject-aware materialization. Module 3J.0.

    Identical PIT/immutability contract to :class:`FeatureMaterialization`,
    but identified by the canonical ``(subject_type, subject_id)`` pair
    instead of a bare ``instrument_id`` -- so the same shape covers an
    instrument feature and a futures-series feature (e.g. the 3I.3 term
    structure) without a second authority or a fabricated instrument.
    """

    feature_id: UUID
    subject_type: FeatureSubjectType
    subject_id: str
    dataset_version: str
    event_at: datetime
    effective_at: datetime
    knowledge_at: datetime
    computed_at: datetime
    source_observation_manifest: tuple[str, ...]
    value: Decimal | None
    quality_status: FeatureQualityStatus
    content_hash: str
    materialization_id: UUID = field(default_factory=uuid4)

    @classmethod
    def create(
        cls,
        *,
        feature_id: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        dataset_version: str,
        event_at: datetime,
        effective_at: datetime,
        knowledge_at: datetime,
        computed_at: datetime,
        source_observation_manifest: tuple[str, ...],
        value: Decimal | None,
        quality_status: FeatureQualityStatus,
    ) -> FeatureMaterializationV2:
        payload = {
            "hash_version": FeatureHashVersion.V2.value,
            "feature_id": str(feature_id), "subject_type": subject_type.value,
            "subject_id": subject_id, "dataset_version": dataset_version,
            "event_at": event_at.isoformat(), "effective_at": effective_at.isoformat(),
            "knowledge_at": knowledge_at.isoformat(), "computed_at": computed_at.isoformat(),
            "source_observation_manifest": source_observation_manifest,
            "value": None if value is None else str(value),
            "quality_status": quality_status.value,
        }
        return cls(
            feature_id, subject_type, subject_id, dataset_version, event_at, effective_at,
            knowledge_at, computed_at, source_observation_manifest, value, quality_status,
            hashlib.sha256(_canonical(payload).encode()).hexdigest(),
        )

    def validate(self) -> None:
        if not self.subject_id.strip() or not self.dataset_version.strip():
            raise FeatureAuthorityError("feature_materialization_identity_missing")
        if len(self.content_hash) != 64 or not self.source_observation_manifest:
            raise FeatureAuthorityError("feature_materialization_provenance_missing")
        for timestamp in (self.event_at, self.effective_at, self.knowledge_at, self.computed_at):
            _aware(timestamp)
        if self.effective_at < self.event_at or self.knowledge_at < self.effective_at:
            raise FeatureAuthorityError("feature_materialization_invalid_temporal_order")
        if self.computed_at < self.knowledge_at:
            raise FeatureAuthorityError("feature_computed_before_knowledge")
        if self.quality_status is FeatureQualityStatus.VALIDATED and self.value is None:
            raise FeatureAuthorityError("validated_feature_requires_value")


def _utc_iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _knowledge_payload_hash(payload: Mapping[str, Any]) -> str:
    # The doctrine's own canonical encoding, so the stored hash is the doctrine's
    # market_content_hash and restore_persisted_feature_knowledge_v1 can check it.
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FeatureMaterializationV3:
    """Phase R2A.2 -- a materialization that carries the three clocks.

    ``V1``/``V2`` rows store one ``knowledge_at`` that was
    ``max(normalized_at, dataset.created_at)``: the platform's own
    ingestion/seal instant. That is kept, under its honest name, as
    :attr:`platform_recorded_at` (and mirrored into the legacy ``knowledge_at``
    column so the table's existing temporal CHECK and uniqueness key keep
    their meaning). Market knowability is a separate column,
    :attr:`market_knowledge_at`, taken from an issued
    :class:`~trade_platform.knowledge_time_doctrine_v1.FeatureKnowledgeV1`:
    ``None`` whenever the doctrine says it is undefined (T0/T1, or any input
    whose clock evidence is missing), never a platform instant standing in.

    Identity. :attr:`content_hash` is the V3 formula and covers the market
    identity only: subject, dataset, event/effective time, market knowledge
    time, claim ceiling, the doctrine's feature-knowledge hash, manifest and
    value. It deliberately excludes ``computed_at`` and
    ``platform_recorded_at``, so recomputing the same feature from the same
    sealed evidence at any later wall time is the *same* materialization
    (idempotent), and neither operational clock can move a historical decision
    time. V1/V2 hashes are untouched and never recomputed.

    Provenance. A row also stores :attr:`knowledge_inputs` -- every input
    observation's recorded clock facts, whose market hashes the feature
    knowledge binds. Integrity checks (:meth:`validate`) prove the row is
    self-consistent, which a hand-built row can also be. Decision authority
    therefore never rests on them: :meth:`verified_feature_knowledge_v1`
    re-derives every input from its *genuine* evidence-tier verdict and
    re-propagates, so a claim or knowledge time the verdict does not support
    cannot survive.
    """

    feature_id: UUID
    subject_type: FeatureSubjectType
    subject_id: str
    dataset_version: str
    event_at: datetime
    effective_at: datetime
    market_knowledge_at: datetime | None
    platform_recorded_at: datetime
    computed_at: datetime
    claim_ceiling: ClaimCeilingV1
    feature_knowledge: Mapping[str, Any]
    feature_knowledge_hash: str
    knowledge_inputs: tuple[Mapping[str, Any], ...]
    source_observation_manifest: tuple[str, ...]
    value: Decimal | None
    quality_status: FeatureQualityStatus
    content_hash: str
    materialization_id: UUID = field(default_factory=uuid4)

    @property
    def knowledge_at(self) -> datetime:
        """The legacy column's value: platform availability, never market knowledge."""
        return self.platform_recorded_at

    @staticmethod
    def _hash(
        *,
        feature_id: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        dataset_version: str,
        event_at: datetime,
        effective_at: datetime,
        market_knowledge_at: datetime | None,
        claim_ceiling: ClaimCeilingV1,
        feature_knowledge_hash: str,
        source_observation_manifest: tuple[str, ...],
        value: Decimal | None,
        quality_status: FeatureQualityStatus,
    ) -> str:
        payload = {
            "hash_version": FeatureHashVersion.V3.value,
            "feature_id": str(feature_id), "subject_type": subject_type.value,
            "subject_id": subject_id, "dataset_version": dataset_version,
            "event_at": _utc_iso(event_at), "effective_at": _utc_iso(effective_at),
            "market_knowledge_at": _utc_iso(market_knowledge_at),
            "claim_ceiling": claim_ceiling.name,
            "feature_knowledge_hash": feature_knowledge_hash,
            "source_observation_manifest": list(source_observation_manifest),
            "value": None if value is None else str(value),
            "quality_status": quality_status.value,
        }
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        feature_id: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        dataset_version: str,
        event_at: datetime,
        effective_at: datetime,
        inputs: Sequence[ObservationKnowledgeV1],
        computed_at: datetime,
        source_observation_manifest: tuple[str, ...],
        value: Decimal | None,
        quality_status: FeatureQualityStatus,
    ) -> FeatureMaterializationV3:
        """Build a V3 row from issued observation knowledge; the doctrine propagates it."""
        try:
            knowledge = propagate_feature_knowledge_v1(inputs, event_at=event_at)
            persisted_inputs = tuple(
                sorted(
                    (persisted_observation_knowledge_v1(item) for item in inputs),
                    key=persisted_observation_market_hash_v1,
                )
            )
        except KnowledgeTimeDoctrineError as error:
            raise FeatureAuthorityError(f"feature_knowledge_invalid:{error}") from error
        payload = knowledge.market_identity_payload()
        content_hash = cls._hash(
            feature_id=feature_id, subject_type=subject_type, subject_id=subject_id,
            dataset_version=dataset_version, event_at=event_at, effective_at=effective_at,
            market_knowledge_at=knowledge.market_knowledge_at,
            claim_ceiling=knowledge.claim_ceiling,
            feature_knowledge_hash=knowledge.market_content_hash,
            source_observation_manifest=source_observation_manifest, value=value,
            quality_status=quality_status,
        )
        return cls(
            feature_id, subject_type, subject_id, dataset_version, event_at, effective_at,
            knowledge.market_knowledge_at, knowledge.platform_recorded_at, computed_at,
            knowledge.claim_ceiling, payload, knowledge.market_content_hash, persisted_inputs,
            source_observation_manifest, value, quality_status, content_hash,
        )

    def verified_feature_knowledge_v1(
        self,
        verdicts: Mapping[UUID, EvidenceTierVerdictV1],
        clock_resolver: SealedClockResolverV1,
    ) -> FeatureKnowledgeV1:
        """This row's clocks, re-derived from sealed evidence -- the provenance check.

        Every stored input is re-derived by the doctrine from the genuine
        verdict its payload names (which must be supplied) and from the clock
        facts ``clock_resolver`` reads out of the *sealed dataset* for that
        observation reference -- never from the row itself. The inputs are
        re-propagated and must reproduce this row's feature-knowledge hash.
        Only the object this returns may carry decision authority.
        """
        self.validate()
        try:
            rederived = []
            by_dataset: dict[UUID, list[tuple[str, Mapping[str, Any], EvidenceTierVerdictV1]]] = {}
            for persisted in self.knowledge_inputs:
                market = persisted.get("market")
                if not isinstance(market, Mapping):
                    raise FeatureAuthorityError("feature_knowledge_inputs_malformed")
                verdict = verdicts.get(UUID(str(market.get("verdict_evidence_id"))))
                if verdict is None:
                    raise FeatureAuthorityError("feature_knowledge_input_verdict_not_supplied")
                by_dataset.setdefault(UUID(str(market["dataset_version_id"])), []).append(
                    (str(market["observation_reference"]), persisted, verdict)
                )
            for dataset_version_id, items in by_dataset.items():
                sealed = clock_resolver(dataset_version_id, [reference for reference, _, _ in items])
                for reference, persisted, verdict in items:
                    clocks = sealed.get(reference)
                    if clocks is None:
                        raise FeatureAuthorityError("feature_knowledge_input_not_in_sealed_evidence")
                    rederived.append(
                        rederive_observation_knowledge_v1(verdict, persisted, sealed=clocks)
                    )
            knowledge = propagate_feature_knowledge_v1(rederived, event_at=self.event_at)
        except (KnowledgeTimeDoctrineError, KeyError, TypeError, ValueError) as error:
            raise FeatureAuthorityError(f"feature_knowledge_not_verified:{error}") from error
        if knowledge.market_content_hash != self.feature_knowledge_hash:
            raise FeatureAuthorityError("feature_knowledge_not_derivable_from_verdicts")
        return knowledge

    def integrity_checked_knowledge_v1(self) -> RestoredFeatureKnowledgeV1:
        """This row's stored clocks, checked for integrity and coherence only.

        Never decision authority -- see :meth:`verified_feature_knowledge_v1`.
        """
        try:
            return restore_persisted_feature_knowledge_v1(
                self.feature_knowledge,
                platform_recorded_at=self.platform_recorded_at,
                expected_market_content_hash=self.feature_knowledge_hash,
            )
        except KnowledgeTimeDoctrineError as error:
            raise FeatureAuthorityError(f"feature_knowledge_invalid:{error}") from error

    def validate(self) -> None:
        if not self.subject_id.strip() or not self.dataset_version.strip():
            raise FeatureAuthorityError("feature_materialization_identity_missing")
        if len(self.content_hash) != 64 or not self.source_observation_manifest:
            raise FeatureAuthorityError("feature_materialization_provenance_missing")
        for timestamp in (self.event_at, self.effective_at, self.platform_recorded_at, self.computed_at):
            _aware(timestamp)
        if self.market_knowledge_at is not None:
            _aware(self.market_knowledge_at)
            # A value cannot be known before it is complete.
            if self.market_knowledge_at < self.effective_at:
                raise FeatureAuthorityError("feature_market_knowledge_precedes_effective_at")
        if self.effective_at < self.event_at or self.platform_recorded_at < self.effective_at:
            raise FeatureAuthorityError("feature_materialization_invalid_temporal_order")
        if self.computed_at < self.platform_recorded_at:
            raise FeatureAuthorityError("feature_computed_before_platform_recorded")
        if self.quality_status is FeatureQualityStatus.VALIDATED and self.value is None:
            raise FeatureAuthorityError("validated_feature_requires_value")
        if _knowledge_payload_hash(self.feature_knowledge) != self.feature_knowledge_hash:
            raise FeatureAuthorityError("feature_knowledge_payload_hash_mismatch")
        knowledge = self.integrity_checked_knowledge_v1()
        if (
            knowledge.event_at != self.event_at
            or knowledge.market_knowledge_at != self.market_knowledge_at
            or knowledge.claim_ceiling is not self.claim_ceiling
        ):
            raise FeatureAuthorityError("feature_knowledge_columns_disagree_with_payload")
        try:
            input_hashes = [persisted_observation_market_hash_v1(item) for item in self.knowledge_inputs]
            input_recorded = [
                datetime.fromisoformat(str(item["platform_recorded_at"]))
                for item in self.knowledge_inputs
            ]
        except (KnowledgeTimeDoctrineError, KeyError, TypeError, ValueError) as error:
            raise FeatureAuthorityError("feature_knowledge_inputs_malformed") from error
        if tuple(input_hashes) != knowledge.input_knowledge_hashes:
            raise FeatureAuthorityError("feature_knowledge_inputs_disagree_with_payload")
        if max(input_recorded) != self.platform_recorded_at:
            raise FeatureAuthorityError("feature_platform_recorded_at_disagrees_with_inputs")
        expected = self._hash(
            feature_id=self.feature_id, subject_type=self.subject_type,
            subject_id=self.subject_id, dataset_version=self.dataset_version,
            event_at=self.event_at, effective_at=self.effective_at,
            market_knowledge_at=self.market_knowledge_at, claim_ceiling=self.claim_ceiling,
            feature_knowledge_hash=self.feature_knowledge_hash,
            source_observation_manifest=self.source_observation_manifest, value=self.value,
            quality_status=self.quality_status,
        )
        if expected != self.content_hash:
            raise FeatureAuthorityError("feature_materialization_v3_content_hash_mismatch")


_V3_COLUMNS = (
    "materialization_id,feature_id,subject_type,subject_id,dataset_version,event_at,"
    "effective_at,market_knowledge_at,platform_recorded_at,computed_at,claim_ceiling,"
    "feature_knowledge,feature_knowledge_hash,source_observation_manifest,value,"
    "quality_status,content_hash,knowledge_inputs"
)


def _json(value: Any) -> Any:
    return value if isinstance(value, (dict, list)) else json.loads(value)


def _row_v3(row: tuple[Any, ...]) -> FeatureMaterializationV3:
    manifest = _json(row[13])
    value = FeatureMaterializationV3(
        feature_id=UUID(str(row[1])), subject_type=FeatureSubjectType(str(row[2])),
        subject_id=str(row[3]), dataset_version=str(row[4]), event_at=row[5],
        effective_at=row[6], market_knowledge_at=row[7], platform_recorded_at=row[8],
        computed_at=row[9], claim_ceiling=ClaimCeilingV1[str(row[10])],
        feature_knowledge=_json(row[11]), feature_knowledge_hash=str(row[12]),
        knowledge_inputs=tuple(_json(row[17])),
        source_observation_manifest=tuple(str(item) for item in manifest),
        value=None if row[14] is None else Decimal(str(row[14])),
        quality_status=FeatureQualityStatus(str(row[15])), content_hash=str(row[16]),
        materialization_id=UUID(str(row[0])),
    )
    # A stored row is re-verified on every read: its payload must still re-hash,
    # restore through the doctrine and agree with its own columns.
    value.validate()
    return value


def _write_in_chunks(
    values: Iterable[Any], batch_size: int, write: Callable[[Sequence[Any]], None]
) -> int:
    """Write an ordered stream in bounded chunks; flush what was produced before a source error."""
    if not 1 <= batch_size <= FEATURE_MATERIALIZATION_BATCH_MAX:
        raise FeatureAuthorityError("invalid_feature_materialization_batch_size")
    iterator = iter(values)
    pending: list[Any] = []
    count = 0
    try:
        while True:
            try:
                value = next(iterator)
            except StopIteration:
                break
            except Exception:
                write(pending)
                raise
            pending.append(value)
            if len(pending) == batch_size:
                write(pending)
                count += len(pending)
                pending = []
        write(pending)
        count += len(pending)
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()
    return count


_OBSERVATION_REFERENCE_PREFIX = "historical_normalized_observation:"


def historical_observation_reference_v1(normalized_observation_id: UUID) -> str:
    """The doctrine ``observation_reference`` of one sealed normalized observation."""
    return f"{_OBSERVATION_REFERENCE_PREFIX}{normalized_observation_id}"


class PostgresSealedObservationClockResolverV1:
    """Sealed clock facts for historical observations, read from the sealed dataset.

    Phase R2A.2. For a member of a ``SEALED`` historical dataset it returns
    ``event_at`` = the raw observation's ``effective_at`` (the instant its
    value was complete) and ``platform_recorded_at`` = ``max(normalized_at,
    dataset.created_at)``. The historical tables record no publisher
    publication time and no recorder arrival or clock bound, so those are
    ``None``: a T3/T4 claim over this evidence cannot be verified and fails
    closed. First-party T4 sealing (R3A) supplies its own resolver.

    Results are cached per dataset; :meth:`preload` fetches a whole dataset's
    members in one query for bulk verification.
    """

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database
        self._cache: dict[UUID, dict[str, SealedObservationClocksV1]] = {}
        self._preloaded: set[UUID] = set()

    _SELECT = (
        "SELECT n.normalized_observation_id, r.effective_at, n.normalized_at, d.created_at "
        "FROM historical_dataset_members m "
        "JOIN historical_dataset_versions d ON d.dataset_version_id=m.dataset_version_id "
        "JOIN historical_normalized_observations n ON n.normalized_observation_id=m.normalized_observation_id "
        "JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
        "WHERE m.dataset_version_id=%s AND d.status='SEALED'"
    )

    def _load(self, dataset_version_id: UUID, ids: Sequence[UUID] | None) -> None:
        statement = self._SELECT + ("" if ids is None else " AND m.normalized_observation_id=ANY(%s)")
        params: tuple[object, ...] = (
            (dataset_version_id,) if ids is None else (dataset_version_id, list(ids))
        )
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(statement, params)
            rows = cursor.fetchall()
        cache = self._cache.setdefault(dataset_version_id, {})
        for row in rows:
            cache[historical_observation_reference_v1(UUID(str(row[0])))] = SealedObservationClocksV1(
                event_at=row[1], platform_recorded_at=max(row[2], row[3])
            )

    def preload(self, dataset_version_id: UUID) -> None:
        if dataset_version_id not in self._preloaded:
            self._load(dataset_version_id, None)
            self._preloaded.add(dataset_version_id)

    def __call__(
        self, dataset_version_id: UUID, references: Sequence[str]
    ) -> Mapping[str, SealedObservationClocksV1]:
        cache = self._cache.get(dataset_version_id, {})
        if dataset_version_id not in self._preloaded:
            missing: list[UUID] = []
            for reference in references:
                if reference in cache or not reference.startswith(_OBSERVATION_REFERENCE_PREFIX):
                    continue
                try:
                    missing.append(UUID(reference[len(_OBSERVATION_REFERENCE_PREFIX):]))
                except ValueError:
                    continue
            if missing:
                self._load(dataset_version_id, missing)
                cache = self._cache.get(dataset_version_id, {})
        return {reference: cache[reference] for reference in references if reference in cache}


def authorized_sealed_clock_resolver_types_v1() -> tuple[type, ...]:
    """The closed set of resolvers whose clock facts may back a *professional* claim.

    A resolver is where T3/T4 clock facts enter; a caller-supplied callable
    could hand the doctrine invented arrivals. The professional gate therefore
    admits only these reviewed implementations. Phase R3A adds the first-party
    T4 resolver, which only a seal rebuilt from raw capture can construct; it is
    imported lazily because it needs the analytics extra (pyarrow), which the
    runtime images do not install -- where it is absent, T4 evidence cannot
    exist and the set is simply the PostgreSQL resolver. Extending the set
    ships as code, like a timing contract.
    """
    try:
        from .first_party_t4_dataset_v1 import FirstPartyT4SealedClockResolverV1
    except ImportError:  # pragma: no cover - analytics extra not installed
        return (PostgresSealedObservationClockResolverV1,)
    return (PostgresSealedObservationClockResolverV1, FirstPartyT4SealedClockResolverV1)


def require_authorized_sealed_clock_resolver_v1(resolver: object) -> None:
    if type(resolver) not in authorized_sealed_clock_resolver_types_v1():
        raise FeatureAuthorityError("sealed_clock_resolver_not_authorized")


class PostgresFeatureAuthority:
    """Append-only definition/value authority with strict decision-time reads."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def register(self, definition: FeatureDefinitionVersion) -> None:
        definition.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO feature_definition_versions VALUES ("
                    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        definition.feature_id, definition.name, definition.family.value,
                        definition.semantic_version, definition.owner, definition.description,
                        json.dumps(definition.required_dataset_types), json.dumps(definition.required_fields),
                        definition.frequency, definition.timestamp_semantics, definition.lookback,
                        json.dumps(dict(definition.parameters), sort_keys=True),
                        definition.missing_value_policy, definition.outlier_policy,
                        definition.leakage_policy, definition.expected_minimum,
                        definition.expected_maximum, definition.units, definition.calculation_version,
                        definition.created_at, definition.retired_at,
                    ),
                )
        except Exception as error:
            raise FeatureAuthorityError("feature_definition_registration_failed") from error

    def materialize(self, value: FeatureMaterialization) -> None:
        """The unchanged INSTRUMENT convenience wrapper -- V1 identity, forever.

        Every row this writes is ``subject_type='INSTRUMENT'``,
        ``subject_id=instrument_id``, ``hash_version='V1'``, and its
        ``content_hash`` is exactly the pre-3J.0 formula computed by
        :meth:`FeatureMaterialization.create`. Callers written against this
        method need no change: the subject columns are populated internally.
        """
        value.validate()
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO feature_materializations "
                    "(materialization_id,feature_id,instrument_id,dataset_version,event_at,"
                    "effective_at,knowledge_at,computed_at,source_observation_manifest,value,"
                    "quality_status,content_hash,subject_type,subject_id,hash_version) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (feature_id,subject_type,subject_id,dataset_version,event_at,"
                    "effective_at,knowledge_at) DO NOTHING "
                    "RETURNING content_hash",
                    (
                        value.materialization_id, value.feature_id, value.instrument_id,
                        value.dataset_version, value.event_at, value.effective_at,
                        value.knowledge_at, value.computed_at,
                        json.dumps(value.source_observation_manifest), value.value,
                        value.quality_status.value, value.content_hash,
                        FeatureSubjectType.INSTRUMENT.value, value.instrument_id,
                        FeatureHashVersion.V1.value,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT content_hash FROM feature_materializations WHERE "
                        "feature_id=%s AND subject_type=%s AND subject_id=%s AND dataset_version=%s "
                        "AND event_at=%s AND effective_at=%s AND knowledge_at=%s",
                        (value.feature_id, FeatureSubjectType.INSTRUMENT.value, value.instrument_id,
                         value.dataset_version, value.event_at, value.effective_at, value.knowledge_at),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != value.content_hash:
                        raise FeatureAuthorityError("feature_materialization_conflict")
        except FeatureAuthorityError:
            raise
        except Exception as error:
            raise FeatureAuthorityError("feature_materialization_failed") from error

    def materialize_subject(self, value: FeatureMaterializationV2) -> None:
        """The canonical generalized write path -- any supported subject type.

        Writes ``hash_version='V2'``, which is what makes
        ``require_valid_feature_subject`` (migration ``20260908_0046``) prove
        at COMMIT that the subject actually exists: an ``INSTRUMENT`` in
        ``professional_instruments``, or a ``FUTURES_SERIES`` in
        ``futures_contract_series``. An ``INSTRUMENT`` row also populates the
        legacy ``instrument_id`` column (kept coherent by a CHECK); a
        ``FUTURES_SERIES`` row leaves it ``NULL``.
        """
        value.validate()
        instrument_id = (
            value.subject_id if value.subject_type is FeatureSubjectType.INSTRUMENT else None
        )
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO feature_materializations "
                    "(materialization_id,feature_id,instrument_id,dataset_version,event_at,"
                    "effective_at,knowledge_at,computed_at,source_observation_manifest,value,"
                    "quality_status,content_hash,subject_type,subject_id,hash_version) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (feature_id,subject_type,subject_id,dataset_version,event_at,"
                    "effective_at,knowledge_at) DO NOTHING "
                    "RETURNING content_hash",
                    (
                        value.materialization_id, value.feature_id, instrument_id,
                        value.dataset_version, value.event_at, value.effective_at,
                        value.knowledge_at, value.computed_at,
                        json.dumps(value.source_observation_manifest), value.value,
                        value.quality_status.value, value.content_hash,
                        value.subject_type.value, value.subject_id, FeatureHashVersion.V2.value,
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT content_hash FROM feature_materializations WHERE "
                        "feature_id=%s AND subject_type=%s AND subject_id=%s AND dataset_version=%s "
                        "AND event_at=%s AND effective_at=%s AND knowledge_at=%s",
                        (value.feature_id, value.subject_type.value, value.subject_id,
                         value.dataset_version, value.event_at, value.effective_at,
                         value.knowledge_at),
                    )
                    existing = cursor.fetchone()
                    if existing is None or str(existing[0]) != value.content_hash:
                        raise FeatureAuthorityError("feature_materialization_conflict")
        except FeatureAuthorityError:
            raise
        except Exception as error:
            raise FeatureAuthorityError("feature_materialization_failed") from error

    def materialize_subjects(self, values: Sequence[FeatureMaterializationV2]) -> None:
        """Bounded batch form of :meth:`materialize_subject` -- the same contract per row.

        One transaction per call (at most :data:`FEATURE_MATERIALIZATION_BATCH_MAX`
        rows). Every value is validated exactly as :meth:`materialize_subject`
        validates it before anything is written; rows are written ``hash_version
        ='V2'`` with the same subject/legacy-``instrument_id`` coherence, through
        the same natural-identity ``ON CONFLICT ... DO NOTHING``. The ``DO
        NOTHING`` is never trusted on its own: every row's stored
        ``content_hash`` is then re-read by that exact natural identity and must
        equal the row's own hash, so an identical replay is idempotent and a
        same-identity/different-hash row fails closed with
        ``feature_materialization_conflict`` -- rolling back the whole chunk.
        Deferred subject constraints are proved at this transaction's COMMIT,
        exactly as for a single row.
        """
        if len(values) > FEATURE_MATERIALIZATION_BATCH_MAX:
            raise FeatureAuthorityError("feature_materialization_batch_too_large")
        if not values:
            return
        for value in values:
            value.validate()
        instrument_ids = [
            value.subject_id if value.subject_type is FeatureSubjectType.INSTRUMENT else None
            for value in values
        ]
        feature_ids = [value.feature_id for value in values]
        subject_types = [value.subject_type.value for value in values]
        subject_ids = [value.subject_id for value in values]
        dataset_versions = [value.dataset_version for value in values]
        event_ats = [value.event_at for value in values]
        effective_ats = [value.effective_at for value in values]
        knowledge_ats = [value.knowledge_at for value in values]
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO feature_materializations "
                    "(materialization_id,feature_id,instrument_id,dataset_version,event_at,"
                    "effective_at,knowledge_at,computed_at,source_observation_manifest,value,"
                    "quality_status,content_hash,subject_type,subject_id,hash_version) "
                    "SELECT materialization_id,feature_id,instrument_id,dataset_version,event_at,"
                    "effective_at,knowledge_at,computed_at,source_observation_manifest::jsonb,value,"
                    "quality_status,content_hash,subject_type,subject_id,%s FROM unnest("
                    "%s::uuid[],%s::uuid[],%s::text[],%s::text[],%s::timestamptz[],"
                    "%s::timestamptz[],%s::timestamptz[],%s::timestamptz[],%s::text[],"
                    "%s::numeric[],%s::text[],%s::text[],%s::text[],%s::text[]) AS v("
                    "materialization_id,feature_id,instrument_id,dataset_version,event_at,"
                    "effective_at,knowledge_at,computed_at,source_observation_manifest,value,"
                    "quality_status,content_hash,subject_type,subject_id) "
                    "ON CONFLICT (feature_id,subject_type,subject_id,dataset_version,event_at,"
                    "effective_at,knowledge_at) DO NOTHING",
                    (
                        FeatureHashVersion.V2.value,
                        [value.materialization_id for value in values], feature_ids,
                        instrument_ids, dataset_versions, event_ats, effective_ats,
                        knowledge_ats, [value.computed_at for value in values],
                        [json.dumps(value.source_observation_manifest) for value in values],
                        [value.value for value in values],
                        [value.quality_status.value for value in values],
                        [value.content_hash for value in values], subject_types, subject_ids,
                    ),
                )
                cursor.execute(
                    "SELECT v.ordinal, f.content_hash FROM unnest("
                    "%s::uuid[],%s::text[],%s::text[],%s::text[],%s::timestamptz[],"
                    "%s::timestamptz[],%s::timestamptz[]) WITH ORDINALITY AS v("
                    "feature_id,subject_type,subject_id,dataset_version,event_at,effective_at,"
                    "knowledge_at,ordinal) LEFT JOIN feature_materializations f ON "
                    "f.feature_id=v.feature_id AND f.subject_type=v.subject_type AND "
                    "f.subject_id=v.subject_id AND f.dataset_version=v.dataset_version AND "
                    "f.event_at=v.event_at AND f.effective_at=v.effective_at AND "
                    "f.knowledge_at=v.knowledge_at ORDER BY v.ordinal",
                    (
                        feature_ids, subject_types, subject_ids, dataset_versions, event_ats,
                        effective_ats, knowledge_ats,
                    ),
                )
                stored = cursor.fetchall()
                if len(stored) != len(values) or any(
                    row[1] is None or str(row[1]) != values[int(row[0]) - 1].content_hash
                    for row in stored
                ):
                    raise FeatureAuthorityError("feature_materialization_conflict")
        except FeatureAuthorityError:
            raise
        except Exception as error:
            raise FeatureAuthorityError("feature_materialization_failed") from error

    def materialize_subject_stream(
        self,
        values: Iterable[FeatureMaterializationV2],
        *,
        batch_size: int = FEATURE_MATERIALIZATION_BATCH_MAX,
    ) -> int:
        """Write an ordered stream of materializations in bounded chunks.

        Holds at most ``batch_size`` materializations in memory and writes each
        full chunk through :meth:`materialize_subjects` (one transaction per
        chunk). If the *source* stream raises -- a calculator's fail-closed
        precondition at a later event -- every materialization it had already
        produced is written first and the original error is then re-raised,
        which is exactly the durable state the per-row
        :meth:`materialize_subject` loop leaves behind. Returns the number of
        materializations written or reconciled as identical.
        """
        return _write_in_chunks(values, batch_size, self.materialize_subjects)

    def materialize_subjects_v3(self, values: Sequence[FeatureMaterializationV3]) -> None:
        """Phase R2A.2 batch write of three-clock rows -- ``hash_version='V3'``.

        Same contract as :meth:`materialize_subjects` (one transaction, array
        binds, deferred subject proof at COMMIT, ``DO NOTHING`` never trusted
        on its own), but reconciled on the V3 natural key -- subject, dataset,
        ``event_at``, ``effective_at`` -- which excludes every operational
        clock. Recomputing the same evidence later therefore reconciles as the
        identical row, and a same-key/different-hash row fails closed with
        ``feature_materialization_conflict``.
        """
        if len(values) > FEATURE_MATERIALIZATION_BATCH_MAX:
            raise FeatureAuthorityError("feature_materialization_batch_too_large")
        if not values:
            return
        for value in values:
            value.validate()
        feature_ids = [value.feature_id for value in values]
        subject_types = [value.subject_type.value for value in values]
        subject_ids = [value.subject_id for value in values]
        dataset_versions = [value.dataset_version for value in values]
        event_ats = [value.event_at for value in values]
        effective_ats = [value.effective_at for value in values]
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO feature_materializations "
                    "(materialization_id,feature_id,instrument_id,dataset_version,event_at,"
                    "effective_at,knowledge_at,computed_at,source_observation_manifest,value,"
                    "quality_status,content_hash,subject_type,subject_id,hash_version,"
                    "market_knowledge_at,platform_recorded_at,claim_ceiling,feature_knowledge,"
                    "feature_knowledge_hash,knowledge_inputs) "
                    "SELECT materialization_id,feature_id,instrument_id,dataset_version,event_at,"
                    "effective_at,platform_recorded_at,computed_at,source_observation_manifest::jsonb,"
                    "value,quality_status,content_hash,subject_type,subject_id,%s,"
                    "market_knowledge_at,platform_recorded_at,claim_ceiling,feature_knowledge::jsonb,"
                    "feature_knowledge_hash,knowledge_inputs::jsonb FROM unnest("
                    "%s::uuid[],%s::uuid[],%s::text[],%s::text[],%s::timestamptz[],"
                    "%s::timestamptz[],%s::timestamptz[],%s::text[],%s::numeric[],%s::text[],"
                    "%s::text[],%s::text[],%s::text[],%s::timestamptz[],%s::timestamptz[],"
                    "%s::text[],%s::text[],%s::text[],%s::text[]) AS v("
                    "materialization_id,feature_id,instrument_id,dataset_version,event_at,"
                    "effective_at,computed_at,source_observation_manifest,value,quality_status,"
                    "content_hash,subject_type,subject_id,market_knowledge_at,platform_recorded_at,"
                    "claim_ceiling,feature_knowledge,feature_knowledge_hash,knowledge_inputs) "
                    "ON CONFLICT (feature_id,subject_type,subject_id,dataset_version,event_at,"
                    "effective_at) WHERE hash_version='V3' DO NOTHING",
                    (
                        FeatureHashVersion.V3.value,
                        [value.materialization_id for value in values], feature_ids,
                        [
                            value.subject_id
                            if value.subject_type is FeatureSubjectType.INSTRUMENT else None
                            for value in values
                        ],
                        dataset_versions, event_ats, effective_ats,
                        [value.computed_at for value in values],
                        [json.dumps(value.source_observation_manifest) for value in values],
                        [value.value for value in values],
                        [value.quality_status.value for value in values],
                        [value.content_hash for value in values], subject_types, subject_ids,
                        [value.market_knowledge_at for value in values],
                        [value.platform_recorded_at for value in values],
                        [value.claim_ceiling.name for value in values],
                        [_canonical(value.feature_knowledge) for value in values],
                        [value.feature_knowledge_hash for value in values],
                        [json.dumps(list(value.knowledge_inputs), sort_keys=True) for value in values],
                    ),
                )
                cursor.execute(
                    "SELECT v.ordinal, f.content_hash FROM unnest("
                    "%s::uuid[],%s::text[],%s::text[],%s::text[],%s::timestamptz[],"
                    "%s::timestamptz[]) WITH ORDINALITY AS v("
                    "feature_id,subject_type,subject_id,dataset_version,event_at,effective_at,"
                    "ordinal) LEFT JOIN feature_materializations f ON f.hash_version='V3' AND "
                    "f.feature_id=v.feature_id AND f.subject_type=v.subject_type AND "
                    "f.subject_id=v.subject_id AND f.dataset_version=v.dataset_version AND "
                    "f.event_at=v.event_at AND f.effective_at=v.effective_at ORDER BY v.ordinal",
                    (feature_ids, subject_types, subject_ids, dataset_versions, event_ats,
                     effective_ats),
                )
                stored = cursor.fetchall()
                if len(stored) != len(values) or any(
                    row[1] is None or str(row[1]) != values[int(row[0]) - 1].content_hash
                    for row in stored
                ):
                    raise FeatureAuthorityError("feature_materialization_conflict")
        except FeatureAuthorityError:
            raise
        except Exception as error:
            raise FeatureAuthorityError("feature_materialization_failed") from error

    def materialize_subject_stream_v3(
        self,
        values: Iterable[FeatureMaterializationV3],
        *,
        batch_size: int = FEATURE_MATERIALIZATION_BATCH_MAX,
    ) -> int:
        """:meth:`materialize_subject_stream` for V3 rows -- identical chunk semantics."""
        return _write_in_chunks(values, batch_size, self.materialize_subjects_v3)

    def historical_as_of_subject_v3(
        self,
        feature_id: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        dataset_version: str,
        market_as_of: datetime,
        *,
        minimum_claim: ClaimCeilingV1,
        evidence_tiers: Mapping[UUID, EvidenceTierVerdictV1],
        clock_resolver: SealedClockResolverV1,
    ) -> tuple[FeatureMaterializationV3, ...]:
        """Historical replay read: what the *market* could know at ``market_as_of``.

        Every returned row has been provenance-verified
        (:meth:`FeatureMaterializationV3.verified_feature_knowledge_v1`)
        against ``evidence_tiers`` and the sealed evidence ``clock_resolver``
        reads; one row that fails verification fails the whole read rather
        than being silently dropped.

        Gated on ``event_at``, ``effective_at`` and ``market_knowledge_at`` only -- never on
        ``platform_recorded_at`` or ``computed_at``, which say when this
        platform wrote the row, not when the value was knowable. A row whose
        market knowledge time is undefined (T0/T1, missing clock evidence) is
        never returned, whatever its claim, and nor is a row below
        ``minimum_claim`` (which must be at least ``CONDITIONAL``: nothing with
        an undefined knowledge time can be replayed historically).
        """
        _aware(market_as_of)
        if minimum_claim < ClaimCeilingV1.CONDITIONAL:
            raise FeatureAuthorityError("historical_read_requires_at_least_conditional_claim")
        admitted = [claim.name for claim in ClaimCeilingV1 if claim >= minimum_claim]
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_V3_COLUMNS} FROM feature_materializations "  # nosec B608 - constant column list
                "WHERE hash_version='V3' AND feature_id=%s AND subject_type=%s AND subject_id=%s "
                "AND dataset_version=%s AND market_knowledge_at IS NOT NULL "
                "AND event_at<=%s AND effective_at<=%s AND market_knowledge_at<=%s "
                "AND claim_ceiling=ANY(%s) "
                "ORDER BY event_at, effective_at",
                (feature_id, subject_type.value, subject_id, dataset_version, market_as_of,
                 market_as_of, market_as_of, admitted),
            )
            rows = cursor.fetchall()
        values = tuple(_row_v3(row) for row in rows)
        for value in values:
            knowledge = value.verified_feature_knowledge_v1(evidence_tiers, clock_resolver)
            if knowledge.claim_ceiling < minimum_claim or knowledge.market_knowledge_at is None:
                raise FeatureAuthorityError("historical_read_row_failed_verification")
        return values

    def v3_rows_for_dataset(
        self, feature_id: UUID, subject_type: FeatureSubjectType, subject_id: str,
        dataset_version: str,
    ) -> tuple[FeatureMaterializationV3, ...]:
        """Every V3 row of one feature/subject/dataset, whatever its claim. Audit reads."""
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {_V3_COLUMNS} FROM feature_materializations "  # nosec B608 - constant column list
                "WHERE hash_version='V3' AND feature_id=%s AND subject_type=%s AND subject_id=%s "
                "AND dataset_version=%s ORDER BY event_at, effective_at",
                (feature_id, subject_type.value, subject_id, dataset_version),
            )
            rows = cursor.fetchall()
        return tuple(_row_v3(row) for row in rows)

    def definition(self, feature_id: UUID) -> FeatureDefinitionVersion:
        """Resolve the immutable definition instead of trusting caller metadata."""
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM feature_definition_versions WHERE feature_id=%s",
                (feature_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise FeatureAuthorityError("feature_definition_not_found")
        datasets = row[6] if isinstance(row[6], list) else json.loads(row[6])
        fields = row[7] if isinstance(row[7], list) else json.loads(row[7])
        parameters = row[11] if isinstance(row[11], dict) else json.loads(row[11])
        return FeatureDefinitionVersion(
            str(row[1]), FeatureFamily(str(row[2])), str(row[3]), str(row[4]), str(row[5]),
            tuple(str(item) for item in datasets), tuple(str(item) for item in fields),
            str(row[8]), str(row[9]), int(row[10]), parameters, str(row[12]), str(row[13]),
            str(row[14]), None if row[15] is None else Decimal(str(row[15])),
            None if row[16] is None else Decimal(str(row[16])), str(row[17]), str(row[18]),
            row[19], row[20], UUID(str(row[0])),
        )

    def latest_as_of(
        self, feature_id: UUID, instrument_id: str, dataset_version: str, decision_at: datetime
    ) -> tuple[FeatureMaterialization, ...]:
        _aware(decision_at)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY event_at "
                "ORDER BY knowledge_at DESC, computed_at DESC) rank FROM feature_materializations m "
                "WHERE feature_id=%s AND instrument_id=%s AND dataset_version=%s "
                "AND hash_version<>'V3' "
                "AND event_at<=%s AND effective_at<=%s AND knowledge_at<=%s AND computed_at<=%s) x "
                "WHERE rank=1 ORDER BY event_at",
                (feature_id, instrument_id, dataset_version, decision_at, decision_at,
                 decision_at, decision_at),
            )
            rows = cursor.fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: tuple[Any, ...]) -> FeatureMaterialization:
        manifest = row[8] if isinstance(row[8], list) else json.loads(row[8])
        return FeatureMaterialization(
            UUID(str(row[1])), str(row[2]), str(row[3]), row[4], row[5], row[6], row[7],
            tuple(str(item) for item in manifest), None if row[9] is None else Decimal(str(row[9])),
            FeatureQualityStatus(str(row[10])), str(row[11]), UUID(str(row[0])),
        )

    def latest_as_of_subject(
        self,
        feature_id: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        dataset_version: str,
        decision_at: datetime,
    ) -> tuple[FeatureMaterializationV2, ...]:
        """The canonical generalized read API. Reads every stored row for the
        subject regardless of which hash formula produced it -- ``hash_version``
        marks how a row's own identity was computed, not which rows are
        readable, so an ``INSTRUMENT`` subject materialized through either
        :meth:`materialize` or :meth:`materialize_subject` is visible here.

        Gating is identical to :meth:`latest_as_of`: no row whose ``event_at``,
        ``effective_at``, ``knowledge_at`` or ``computed_at`` is after
        ``decision_at`` can ever be returned.

        ``V3`` rows are never returned here: their ``knowledge_at`` is platform
        availability and their identity is the V3 formula. They are read only
        through :meth:`historical_as_of_subject_v3`.
        """
        _aware(decision_at)
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT materialization_id,feature_id,subject_type,subject_id,dataset_version,"
                "event_at,effective_at,knowledge_at,computed_at,source_observation_manifest,value,"
                "quality_status,content_hash FROM (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY event_at "
                "ORDER BY knowledge_at DESC, computed_at DESC) rank FROM feature_materializations m "
                "WHERE feature_id=%s AND subject_type=%s AND subject_id=%s AND dataset_version=%s "
                "AND hash_version<>'V3' "
                "AND event_at<=%s AND effective_at<=%s AND knowledge_at<=%s AND computed_at<=%s) x "
                "WHERE rank=1 ORDER BY event_at",
                (feature_id, subject_type.value, subject_id, dataset_version, decision_at,
                 decision_at, decision_at, decision_at),
            )
            rows = cursor.fetchall()
        return tuple(self._row_v2(row) for row in rows)

    @staticmethod
    def _row_v2(row: tuple[Any, ...]) -> FeatureMaterializationV2:
        manifest = row[9] if isinstance(row[9], list) else json.loads(row[9])
        return FeatureMaterializationV2(
            feature_id=UUID(str(row[1])), subject_type=FeatureSubjectType(str(row[2])),
            subject_id=str(row[3]), dataset_version=str(row[4]), event_at=row[5],
            effective_at=row[6], knowledge_at=row[7], computed_at=row[8],
            source_observation_manifest=tuple(str(item) for item in manifest),
            value=None if row[10] is None else Decimal(str(row[10])),
            quality_status=FeatureQualityStatus(str(row[11])), content_hash=str(row[12]),
            materialization_id=UUID(str(row[0])),
        )
