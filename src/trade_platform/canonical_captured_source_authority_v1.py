"""Phase 3D.9S.2B -- the canonical captured-source authority.

Authorizes the *exact* Tardis-recorded Bybit V5 public WebSocket semantics that
Phase 3D.9S.2A proved, as a second research-data source contract alongside the
existing Bybit V5 REST authority. It authorizes a **contract**, nothing else: no
paid subscription, no acquisition, no dataset, no broker, account, order,
execution or paper-trading path exists here or is implied by it.

**Two actors, never merged.** Tardis is the *capture provider*; Bybit is the
*originating exchange*. They are separate fields with separate vocabularies, and
``capture_provider_exchange_identity`` (Tardis' own string ``"bybit"``) is a
third, distinct thing again -- a vendor label, not a venue. Collapsing any of
the three would let one actor's reputation stand in for another's evidence.

**The existing REST authority is untouched.**
:func:`trade_platform.real_market_data_provenance_v1.canonical_bybit_source_contract_v1`
keeps its source id, its field values and its content hash byte for byte. This
module adds a *second* authority; it never widens the first, and it adds no
provider-name allowlist. Free text is never authority: a persisted source row
saying ``provider = "tardis"`` proves nothing here, exactly as ``"bybit"``
proves nothing there.

**A separate contract, not a widened one.**
:class:`CanonicalSourceContractV1` stays the common *persisted-source
projection* -- the eight fields a ``historical_data_sources`` row can actually
hold -- and its hash payload is unchanged. Capture semantics that no column can
represent (transport, channels, recorder clock, generated-record policy, the
availability rules) live in :class:`CanonicalCapturedSourceContractV1`, an
immutable companion authority with its own deterministic identity and content
hash. Nothing ambiguous is stuffed into the V1 fields to make it fit.

**Provider-published and platform-derived are different evidence.** The capture
carries exactly three provider observations: Bybit mark price updates, Bybit
index price updates and Bybit public trades. The PIT mark/index basis, the
reconstructed 1m OHLCV and any later strategy feature are *platform-derived*
artifacts computed from those, and this contract says so in two separate lists.
No reconstructed bar is ever describable as published by Bybit or by Tardis.
Because ``PUBLIC_TRADE`` is not an :class:`ObservationKind`, the persisted
projection declares only the two published kinds it can honestly name; captured
trades stay raw evidence with their manifests, and the bars derived from them
stay derived.

**Availability is measured, and never moved earlier.** ``source_available_at``
is the recorder's own arrival timestamp for one captured message. It is not
``event_at`` (the exchange event), not ``effective_at`` (existing provider
economic effectiveness), not ``ingested_at`` (this platform importing the
evidence, which happens later), not ``normalized_at``, not dataset sealing time
and not feature computation time. Those seven concepts stay seven concepts --
see :data:`DISTINCT_TEMPORAL_CONCEPTS_V1` -- and this module writes none of them
into another. Comparisons are in exact integer nanoseconds because the recorder
clock is 100 ns and a ``datetime`` round trip truncates, which moves an instant
*earlier*: the unsafe direction.

**The two availability rules this authority accepts, and no others.** For a
captured ticker basis, ``source_available_at =
max(mark.source_available_at, index.source_available_at)``, with both component
evidence identities bound in; there is no interpolation and no assumption that
one message carried both sides. For a reconstructed bar, OPEN availability is
the recorder arrival of the deterministically selected first trade, and the
*completed* aggregate is available only at
``max(bar_close_boundary, latest contributing trade arrival)`` -- the last
arrival alone never proves the minute finished. Mark and index are reference
prices and are never tradable execution prices.

**Coverage stays positive.** Declared half-open capture windows, explicit gaps,
reconnect snapshot resets, and cross-sequence evidence used only where it truly
proves loss or disorder. Proximity of timestamps is not continuity, and a future
canonical dataset may not silently span a capture gap.

**Authorization is proven per source, fail closed.**
:func:`evaluate_captured_source_authority_v1` returns a content-hashed verdict
that only this module can issue: every declared capture semantic must equal this
contract's, generated vendor records are refused outright, a missing recorder
timestamp is refused, a malformed capture manifest hash is refused, and an
unproven gap is refused. A verdict whose fields are edited fails its own
integrity check.

**This authorizes no data.** Phase 3D.9S.2A proved engineering feasibility;
3D.9S.2B authorizes the contract. Neither clears
``UNPROVEN_DISTINCT_FEATURE_DECISION_TIMES``: that needs authorized full
historical evidence actually acquired, sealed, provenance-proven, materialized
across the research span, with distinct valid historical decision times proven
for that exact dataset. Free-sample evidence is not a substitute, and the next
gate is the owner's paid/full-data acquisition decision.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .bybit_ticker_state_reconstruction_v1 import PitBasisObservationV1
from .bybit_trade_bar_reconstruction_v1 import (
    BAR_BUILDER_SEMANTIC_VERSION_V1,
    ReconstructedTradeBarV1,
    bar_close_nanos,
)
from .tardis_capture_evidence_v1 import (
    ORIGINATING_EXCHANGE_BYBIT,
    TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
    TARDIS_EXCHANGE_ID_BYBIT,
    TardisChannelV1,
)

CAPTURED_SOURCE_CONTRACT_SCHEMA_VERSION: Final = "canonical-captured-source-contract-v1"
CAPTURED_SOURCE_AUTHORITY_SCHEMA_VERSION: Final = "canonical-captured-source-authority-v1"

STATUS_CAPTURED_SOURCE_AUTHORIZED: Final = "CAPTURED_SOURCE_AUTHORIZED"
STATUS_CAPTURED_SOURCE_UNAUTHORIZED: Final = "CAPTURED_SOURCE_UNAUTHORIZED"

#: The capture provider: who recorded the feed. Never the venue that produced it.
CAPTURE_PROVIDER_TARDIS: Final = "tardis"

#: The pilot instrument, in the platform's own canonical instrument identity.
CAPTURED_INSTRUMENT_SCOPE_V1: Final = "CRYPTO:BYBIT:BTCUSDT:PERP"

#: The seven distinct temporal concepts this phase must keep distinct. Listed so
#: a reviewer can see that ``source_available_at`` is an addition, not an alias.
DISTINCT_TEMPORAL_CONCEPTS_V1: Final = (
    "event_at",
    "effective_at",
    "source_available_at",
    "ingested_at",
    "normalized_at",
    "dataset_sealed_at",
    "feature_computed_at",
)

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.canonical_captured_source_authority_v1")

#: Only this module may issue a contract or a verdict.
_ISSUER: Final = object()


class CanonicalCapturedSourceAuthorityError(ValueError):
    """Raised when a contract or verdict is fabricated outside this authority."""


class OriginTransportV1(StrEnum):
    """How the originating exchange published the messages that were recorded."""

    BYBIT_V5_PUBLIC_WEBSOCKET = "BYBIT_V5_PUBLIC_WEBSOCKET"


class CaptureMethodologyV1(StrEnum):
    """How the capture provider obtained them. A rebuild from REST is not this."""

    HISTORICALLY_RECORDED_REALTIME_WEBSOCKET = "HISTORICALLY_RECORDED_REALTIME_WEBSOCKET"


class SourceAvailabilityClockV1(StrEnum):
    """Which measured clock stamps ``source_available_at``."""

    TARDIS_RECORDER_LOCAL_TIMESTAMP = "TARDIS_RECORDER_LOCAL_TIMESTAMP"


class ExchangeTimestampSemanticsV1(StrEnum):
    """What the exchange-side timestamps inside a captured payload mean."""

    BYBIT_MESSAGE_AND_TRADE_TIMESTAMPS = "BYBIT_MESSAGE_AND_TRADE_TIMESTAMPS"


#: What the provider actually published and the recorder actually observed.
PROVIDER_CAPTURED_OBSERVATIONS_V1: Final = (
    "BYBIT_INDEX_PRICE_UPDATE",
    "BYBIT_MARK_PRICE_UPDATE",
    "BYBIT_PUBLIC_TRADE",
)

#: What this platform computes from them. Never attributable to Bybit or Tardis.
PLATFORM_DERIVED_ARTIFACTS_V1: Final = (
    "PLATFORM_PIT_MARK_INDEX_BASIS",
    "PLATFORM_RECONSTRUCTED_1M_OHLCV",
    "PLATFORM_STRATEGY_FEATURES",
)

TICKER_AVAILABILITY_RULES_V1: Final = (
    "snapshot_initializes_authoritative_state",
    "snapshot_replaces_prior_state",
    "delta_changes_only_fields_present",
    "mark_and_index_are_independent",
    "no_basis_before_both_components_observed",
    "no_future_observation_fills_earlier_state",
    "component_source_available_at_is_its_exact_recorder_arrival",
    "basis_source_available_at_is_max_of_component_arrivals",
    "both_component_evidence_identities_bound_into_the_basis",
    "no_interpolation_and_no_assumed_same_message_synchronization",
)

BAR_TEMPORAL_RULES_V1: Final = (
    "exchange_trade_time_recorder_arrival_and_minute_boundaries_stay_separate",
    "open_available_at_is_the_selected_first_trade_recorder_arrival",
    "bar_complete_available_at_is_max_of_close_boundary_and_last_arrival",
    "last_trade_arrival_alone_is_not_completed_bar_availability",
    "mark_and_index_prices_are_not_tradable_execution_prices",
)

GAP_AUTHORITY_RULES_V1: Final = (
    "coverage_is_declared_half_open_capture_windows",
    "gaps_are_explicit_and_never_bridged",
    "reconnect_emits_a_snapshot_state_reset",
    "sequence_evidence_only_where_it_proves_loss_or_disorder",
    "timestamp_proximity_is_not_continuity",
    "a_canonical_dataset_may_not_span_a_capture_gap",
)

#: The capture semantics version this authority pins. Bumping it is a new
#: authority, not a re-reading of this one.
CAPTURE_SEMANTIC_VERSION_V1: Final = "tardis-bybit-v5-public-websocket-capture-1.0.0"

CAPTURED_SOURCE_PROVIDER_TERMS_VERSION_V1: Final = (
    "operator-declared:tardis-historical-bybit-v5-public-websocket-capture:v1"
)

CAPTURED_SOURCE_AUTHORIZATION_REFERENCE_V1: Final = (
    "operator-approved Phase 3D.9S.2B captured-source contract: Tardis historical "
    "recordings of the public wss://stream.bybit.com/v5/public feed for "
    "CRYPTO:BYBIT:BTCUSDT:PERP, channels tickers and publicTrade only, "
    "vendor-generated records refused. Proven by the Phase 3D.9S.2A zero-cost "
    "engineering pilot (TARDIS_CAPTURE_ENGINEERING_PROVEN). Authorizes a source "
    "contract only: no paid subscription, no stored credential, no historical "
    "acquisition, no broker, account, order, execution or live-trading authority."
)

#: The persisted projection. ``PUBLIC_TRADE`` is deliberately absent: it is not
#: an ObservationKind, and inventing one to look complete would be a fiction.
CAPTURED_SOURCE_PERSISTED_PROVIDER_V1: Final = CAPTURE_PROVIDER_TARDIS
CAPTURED_SOURCE_PERSISTED_DATASET_NAME_V1: Final = (
    "tardis_historical_bybit_v5_public_websocket_capture"
)
CAPTURED_SOURCE_IDENTIFIER_NAMESPACE_V1: Final = "bybit_v5_symbol"
CAPTURED_SOURCE_ASSET_SCOPE_V1: Final = "CRYPTO"
CAPTURED_SOURCE_PUBLISHED_OBSERVATION_KINDS_V1: Final = ("INDEX_PRICE", "MARK_PRICE")


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CanonicalCapturedSourceContractV1:
    """The exact captured-source semantics one authority stands for.

    Immutable and issuable only by this module, so no fixture can mint an
    authority by filling in the same field names.
    """

    schema_version: str
    capture_provider: str
    originating_exchange: str
    capture_provider_exchange_identity: str
    origin_transport: str
    instrument_scope: str
    authorized_channels: tuple[str, ...]
    capture_methodology: str
    source_availability_clock: str
    exchange_timestamp_semantics: str
    generated_records_permitted: bool
    provider_captured_observations: tuple[str, ...]
    platform_derived_artifacts: tuple[str, ...]
    ticker_availability_rules: tuple[str, ...]
    bar_temporal_rules: tuple[str, ...]
    gap_authority_rules: tuple[str, ...]
    parser_semantic_version: str
    capture_semantic_version: str
    bar_builder_semantic_version: str
    provider_terms_version: str
    authorization_reference: str
    persisted_provider: str
    persisted_dataset_name: str
    provider_identifier_namespace: str
    asset_scope: str
    published_observation_kinds: tuple[str, ...]
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise CanonicalCapturedSourceAuthorityError(
                "captured_source_contract_is_issued_only_by_its_authority"
            )

    def identity_payload(self) -> dict[str, Any]:
        """The fields that decide *which actor and semantics*, not how it reads."""
        return {
            "schema_version": self.schema_version,
            "capture_provider": self.capture_provider,
            "originating_exchange": self.originating_exchange,
            "capture_provider_exchange_identity": self.capture_provider_exchange_identity,
            "origin_transport": self.origin_transport,
            "instrument_scope": self.instrument_scope,
            "authorized_channels": list(self.authorized_channels),
            "capture_methodology": self.capture_methodology,
            "provider_terms_version": self.provider_terms_version,
            "authorization_reference": self.authorization_reference,
        }

    def contract_payload(self) -> dict[str, Any]:
        payload = self.identity_payload()
        payload.update(
            {
                "source_availability_clock": self.source_availability_clock,
                "exchange_timestamp_semantics": self.exchange_timestamp_semantics,
                "generated_records_permitted": self.generated_records_permitted,
                "provider_captured_observations": list(self.provider_captured_observations),
                "platform_derived_artifacts": list(self.platform_derived_artifacts),
                "ticker_availability_rules": list(self.ticker_availability_rules),
                "bar_temporal_rules": list(self.bar_temporal_rules),
                "gap_authority_rules": list(self.gap_authority_rules),
                "parser_semantic_version": self.parser_semantic_version,
                "capture_semantic_version": self.capture_semantic_version,
                "bar_builder_semantic_version": self.bar_builder_semantic_version,
                "persisted_provider": self.persisted_provider,
                "persisted_dataset_name": self.persisted_dataset_name,
                "provider_identifier_namespace": self.provider_identifier_namespace,
                "asset_scope": self.asset_scope,
                "published_observation_kinds": list(self.published_observation_kinds),
            }
        )
        return payload

    def content_hash(self) -> str:
        return _sha256(self.contract_payload())

    @property
    def source_id(self) -> UUID:
        """Deterministic identity, derived from who and what -- not from wording."""
        return uuid5(_NAMESPACE, f"captured-source:{_sha256(self.identity_payload())}")

    def persisted_source_projection(self) -> dict[str, Any]:
        """The eight fields a ``historical_data_sources`` row can actually hold.

        Returned as a mapping rather than a :class:`CanonicalSourceContractV1` so
        this module stays independent of the provenance authority that consumes
        it, and so the existing V1 hash payload is not touched from here.
        """
        return {
            "source_id": self.source_id,
            "provider": self.persisted_provider,
            "dataset_name": self.persisted_dataset_name,
            "provider_identifier_namespace": self.provider_identifier_namespace,
            "provider_terms_version": self.provider_terms_version,
            "authorization_reference": self.authorization_reference,
            "asset_scope": self.asset_scope,
            "observation_kinds": tuple(self.published_observation_kinds),
        }


def canonical_tardis_captured_bybit_source_contract_v1() -> CanonicalCapturedSourceContractV1:
    """The one authorized Tardis-captured Bybit V5 public WebSocket contract."""
    return CanonicalCapturedSourceContractV1(
        schema_version=CAPTURED_SOURCE_CONTRACT_SCHEMA_VERSION,
        capture_provider=CAPTURE_PROVIDER_TARDIS,
        originating_exchange=ORIGINATING_EXCHANGE_BYBIT,
        capture_provider_exchange_identity=TARDIS_EXCHANGE_ID_BYBIT,
        origin_transport=OriginTransportV1.BYBIT_V5_PUBLIC_WEBSOCKET.value,
        instrument_scope=CAPTURED_INSTRUMENT_SCOPE_V1,
        authorized_channels=tuple(
            sorted((TardisChannelV1.TICKERS.value, TardisChannelV1.PUBLIC_TRADE.value))
        ),
        capture_methodology=CaptureMethodologyV1.HISTORICALLY_RECORDED_REALTIME_WEBSOCKET.value,
        source_availability_clock=SourceAvailabilityClockV1.TARDIS_RECORDER_LOCAL_TIMESTAMP.value,
        exchange_timestamp_semantics=(
            ExchangeTimestampSemanticsV1.BYBIT_MESSAGE_AND_TRADE_TIMESTAMPS.value
        ),
        generated_records_permitted=False,
        provider_captured_observations=PROVIDER_CAPTURED_OBSERVATIONS_V1,
        platform_derived_artifacts=PLATFORM_DERIVED_ARTIFACTS_V1,
        ticker_availability_rules=TICKER_AVAILABILITY_RULES_V1,
        bar_temporal_rules=BAR_TEMPORAL_RULES_V1,
        gap_authority_rules=GAP_AUTHORITY_RULES_V1,
        parser_semantic_version=TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
        capture_semantic_version=CAPTURE_SEMANTIC_VERSION_V1,
        bar_builder_semantic_version=BAR_BUILDER_SEMANTIC_VERSION_V1,
        provider_terms_version=CAPTURED_SOURCE_PROVIDER_TERMS_VERSION_V1,
        authorization_reference=CAPTURED_SOURCE_AUTHORIZATION_REFERENCE_V1,
        persisted_provider=CAPTURED_SOURCE_PERSISTED_PROVIDER_V1,
        persisted_dataset_name=CAPTURED_SOURCE_PERSISTED_DATASET_NAME_V1,
        provider_identifier_namespace=CAPTURED_SOURCE_IDENTIFIER_NAMESPACE_V1,
        asset_scope=CAPTURED_SOURCE_ASSET_SCOPE_V1,
        published_observation_kinds=CAPTURED_SOURCE_PUBLISHED_OBSERVATION_KINDS_V1,
        _issuer=_ISSUER,
    )


@dataclass(frozen=True, slots=True)
class CapturedSourceEvidenceFactsV1:
    """What a persisted/observed capture actually declares about itself.

    Caller-supplied *facts*, never verdicts, and never authority: every string
    here is untrusted input that must equal the contract to prove anything.
    """

    dataset_version_id: UUID
    capture_provider: str
    originating_exchange: str
    capture_provider_exchange_identity: str
    origin_transport: str
    instrument_scope: str
    channels: tuple[str, ...]
    capture_methodology: str
    source_availability_clock: str
    exchange_timestamp_semantics: str
    parser_semantic_version: str
    capture_semantic_version: str
    provider_terms_version: str
    authorization_reference: str
    capture_manifest_hash: str
    contains_generated_records: bool
    earliest_source_available_at_nanos: int | None
    latest_source_available_at_nanos: int | None
    declared_coverage_window_count: int
    spans_unproven_gap: bool


@dataclass(frozen=True, slots=True)
class CapturedSourceAuthorityVerdictV1:
    """Derived, content-hashed captured-source verdict. Issued here only."""

    schema_version: str
    status: str
    reasons: tuple[str, ...]
    dataset_version_id: UUID
    source_id: UUID
    contract_content_hash: str
    capture_manifest_hash: str | None
    content_hash: str
    evidence_id: UUID
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise CanonicalCapturedSourceAuthorityError(
                "captured_source_authority_is_issued_only_by_its_authority"
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reasons": list(self.reasons),
            "dataset_version_id": str(self.dataset_version_id),
            "source_id": str(self.source_id),
            "contract_content_hash": self.contract_content_hash,
            "capture_manifest_hash": self.capture_manifest_hash,
        }

    def integrity_verified(self) -> bool:
        return (
            self.content_hash == _sha256(self.identity_payload())
            and self.evidence_id == uuid5(_NAMESPACE, f"captured-authority:{self.content_hash}")
        )

    def is_authorized(self) -> bool:
        """True only for an intact, positively authorized captured source."""
        return (
            self.integrity_verified()
            and self.schema_version == CAPTURED_SOURCE_AUTHORITY_SCHEMA_VERSION
            and self.status == STATUS_CAPTURED_SOURCE_AUTHORIZED
            and not self.reasons
            and self.capture_manifest_hash is not None
        )


def _issue_verdict(**values: Any) -> CapturedSourceAuthorityVerdictV1:
    draft = CapturedSourceAuthorityVerdictV1(
        **values, content_hash="", evidence_id=_NAMESPACE, _issuer=_ISSUER
    )
    content_hash = _sha256(draft.identity_payload())
    return CapturedSourceAuthorityVerdictV1(
        **values,
        content_hash=content_hash,
        evidence_id=uuid5(_NAMESPACE, f"captured-authority:{content_hash}"),
        _issuer=_ISSUER,
    )


#: Facts field -> contract field. Every one must match exactly; a capture
#: provider alone proves nothing if the venue, transport or channels differ.
_SEMANTIC_FIELDS: Final = (
    ("capture_provider", "capture_provider"),
    ("originating_exchange", "originating_exchange"),
    ("capture_provider_exchange_identity", "capture_provider_exchange_identity"),
    ("origin_transport", "origin_transport"),
    ("instrument_scope", "instrument_scope"),
    ("capture_methodology", "capture_methodology"),
    ("source_availability_clock", "source_availability_clock"),
    ("exchange_timestamp_semantics", "exchange_timestamp_semantics"),
    ("parser_semantic_version", "parser_semantic_version"),
    ("capture_semantic_version", "capture_semantic_version"),
    ("provider_terms_version", "provider_terms_version"),
    ("authorization_reference", "authorization_reference"),
)


def evaluate_captured_source_authority_v1(
    facts: CapturedSourceEvidenceFactsV1,
    contract: CanonicalCapturedSourceContractV1 | None = None,
) -> CapturedSourceAuthorityVerdictV1:
    """Pure, fail-closed captured-source verdict. See the module docstring."""
    resolved = canonical_tardis_captured_bybit_source_contract_v1() if contract is None else contract
    reasons: list[str] = []

    for fact_name, contract_name in _SEMANTIC_FIELDS:
        if getattr(facts, fact_name) != getattr(resolved, contract_name):
            reasons.append(f"captured_source_semantics_mismatch:{fact_name}")
    if tuple(sorted(facts.channels)) != tuple(resolved.authorized_channels):
        reasons.append("captured_source_semantics_mismatch:channels")

    if facts.contains_generated_records and not resolved.generated_records_permitted:
        # A Tardis "generated" row is a vendor reconstruction, not an observation.
        reasons.append("captured_source_contains_generated_records")

    earliest = facts.earliest_source_available_at_nanos
    latest = facts.latest_source_available_at_nanos
    if earliest is None or latest is None:
        reasons.append("captured_source_recorder_timestamp_missing")
    elif latest < earliest:
        reasons.append("captured_source_availability_moved_earlier")

    if not _HEX64.fullmatch(facts.capture_manifest_hash.strip()):
        reasons.append("captured_source_capture_manifest_hash_malformed")

    if facts.declared_coverage_window_count < 1:
        reasons.append("captured_source_no_declared_coverage_window")
    if facts.spans_unproven_gap:
        reasons.append("captured_source_spans_unproven_capture_gap")

    authorized = not reasons
    return _issue_verdict(
        schema_version=CAPTURED_SOURCE_AUTHORITY_SCHEMA_VERSION,
        status=(
            STATUS_CAPTURED_SOURCE_AUTHORIZED
            if authorized
            else STATUS_CAPTURED_SOURCE_UNAUTHORIZED
        ),
        reasons=tuple(reasons),
        dataset_version_id=facts.dataset_version_id,
        source_id=resolved.source_id,
        contract_content_hash=resolved.content_hash(),
        capture_manifest_hash=facts.capture_manifest_hash.strip() if authorized else None,
    )


def basis_availability_reasons_v1(observation: PitBasisObservationV1) -> tuple[str, ...]:
    """Check one reconstructed basis against the authorized ticker rules."""
    reasons: list[str] = []
    expected = max(
        observation.mark_local_timestamp_nanos, observation.index_local_timestamp_nanos
    )
    if observation.research_available_at_nanos != expected:
        reasons.append("basis_source_available_at_is_not_max_of_component_arrivals")
    if not observation.mark_record_content_hash or not observation.index_record_content_hash:
        reasons.append("basis_component_evidence_identity_not_bound")
    return tuple(reasons)


def bar_temporal_reasons_v1(bar: ReconstructedTradeBarV1) -> tuple[str, ...]:
    """Check one reconstructed bar against the authorized temporal rules."""
    reasons: list[str] = []
    if bar.bar_complete_available_at_nanos < bar_close_nanos(bar):
        reasons.append("bar_complete_available_at_precedes_minute_close")
    if bar.bar_complete_available_at_nanos < bar.open_available_at_nanos:
        reasons.append("bar_complete_available_at_precedes_open_availability")
    return tuple(reasons)
