"""Phase 3Z.2 -- the first-party capture source and timing authority.

``RESEARCH_ONLY``. This platform is the *recorder*. Bybit is the *originating
exchange*. Those stay two fields with two vocabularies, exactly as Phase
3D.9S.2B insisted for the third-party case, because collapsing them would let
one actor's reputation stand in for another's evidence.

Why this is a separate contract, not a reuse
--------------------------------------------
:mod:`trade_platform.canonical_captured_source_authority_v1` authorizes
*Tardis-recorded* Bybit V5 semantics. Its ``capture_provider`` is a vendor, its
availability clock is that vendor's recorder, and its identity hash is pinned.
Recording the same exchange feed ourselves is a different actor with a different
clock, so it gets its own contract and its own deterministic ``source_id``.
Nothing here reads, widens or touches the Tardis authority or the Bybit REST
authority; both keep their ids and hashes byte for byte, and the Tardis contract
stays dormant and is not a dependency of this module.

The proven semantics are carried over, not re-derived
-----------------------------------------------------
The availability rules Phase 3D.9S.2A proved are restated here as this
contract's own constants rather than imported, so a dormant vendor module is
never a runtime dependency of the first-party path. They are asserted equal to
the 3D.9S.2B tuples in tests, so the two cannot drift apart silently.

The recorder clock, stated honestly
-----------------------------------
:data:`CLOCK_SEMANTICS_V1` says what the arrival timestamp actually is: the
platform's own wall clock read at the instant the message was handed to the
recorder by the socket, at the operating system's real resolution, paired with a
monotonic process counter used for ordering and for detecting wall-clock steps.
It is **not** a claim of nanosecond physical accuracy: Python exposes integer
nanoseconds, the underlying clock does not deliver them, and the archive records
the measured resolution rather than implying one. It is also not the exchange's
event time, not ``effective_at``, and not ``ingested_at``.

The T4 boundary this phase does not cross
-----------------------------------------
:func:`first_party_bybit_timing_contract_v1` registers this source with the
Phase 3Z.1 evidence-tier authority as
:class:`~trade_platform.evidence_tier_authority_v1.TimingAuthorityV1.PLATFORM_RECORDER_ARRIVAL_TIMESTAMP`,
which maps to ``T4_FIRST_PARTY_CAPTURE``. That registration alone issues no
verdict. ``evaluate_evidence_tier_v1`` still requires a
:class:`~trade_platform.real_market_data_provenance_v1.RealMarketDataProvenanceV1`
verdict proven over a *sealed* dataset, and no such dataset exists for captured
evidence yet -- creating one is Phase 3Z.3's normalization and sealing work.
Until then a first-party evidence-tier evaluation fails closed with
``evidence_tier_real_market_data_provenance_not_proven``. That is the correct
answer, and this phase deliberately fabricates no dataset to dodge it.

No caller flag can reach any of this. There is no ``first_party=true``: the tier
is resolved from a deterministic ``source_id`` derived from the contract's own
identity payload, and a recorded partition proves it belongs to this source only
by carrying capture semantics that equal this contract field for field.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

FIRST_PARTY_CAPTURE_CONTRACT_SCHEMA_VERSION: Final = "first-party-capture-contract-v1"

#: The recorder is this platform. Never an exchange, never a vendor.
CAPTURE_PROVIDER_FIRST_PARTY: Final = "trade_platform"

#: The originating exchange, in the platform's own vocabulary.
ORIGINATING_EXCHANGE_BYBIT_V1: Final = "BYBIT"

#: The official public endpoint. No credential, no account, no paid tier.
BYBIT_PUBLIC_LINEAR_ENDPOINT_V1: Final = "wss://stream.bybit.com/v5/public/linear"

CAPTURED_INSTRUMENT_V1: Final = "CRYPTO:BYBIT:BTCUSDT:PERP"
CAPTURED_EXCHANGE_SYMBOL_V1: Final = "BTCUSDT"

#: Bumping this is a new authority, not a re-reading of this one.
FIRST_PARTY_CAPTURE_SEMANTIC_VERSION_V1: Final = (
    "trade-platform-bybit-v5-public-websocket-first-party-capture-1.0.0"
)

FIRST_PARTY_RECORD_SCHEMA_VERSION_V1: Final = "first-party-capture-record-v1"


class FirstPartyCaptureAuthorityError(ValueError):
    """Raised when a contract is fabricated outside this authority."""


class BybitPublicChannelV1(StrEnum):
    """The two public topics this phase subscribes to, and no others.

    The wire values match Bybit's own topic names. They are declared here rather
    than imported from the Tardis evidence module so the first-party path owns
    its vocabulary and carries no dependency on a dormant vendor contract.
    """

    TICKERS = "tickers"
    PUBLIC_TRADE = "publicTrade"


class BybitMessageTypeV1(StrEnum):
    """Bybit's own ``type`` discriminator on a public stream message."""

    SNAPSHOT = "snapshot"
    DELTA = "delta"


class OriginTransportV1(StrEnum):
    BYBIT_V5_PUBLIC_WEBSOCKET = "BYBIT_V5_PUBLIC_WEBSOCKET"


class CaptureMethodologyV1(StrEnum):
    """How the evidence was obtained. A REST rebuild is emphatically not this."""

    FIRST_PARTY_LIVE_WEBSOCKET_RECORDING = "FIRST_PARTY_LIVE_WEBSOCKET_RECORDING"


class SourceAvailabilityClockV1(StrEnum):
    PLATFORM_RECORDER_WALL_CLOCK = "PLATFORM_RECORDER_WALL_CLOCK"


#: What the exchange published and the recorder observed arriving.
PROVIDER_CAPTURED_OBSERVATIONS_V1: Final = (
    "BYBIT_INDEX_PRICE_UPDATE",
    "BYBIT_MARK_PRICE_UPDATE",
    "BYBIT_PUBLIC_TRADE",
)

#: What this platform computes from them. Never attributable to Bybit.
PLATFORM_DERIVED_ARTIFACTS_V1: Final = (
    "PLATFORM_PIT_MARK_INDEX_BASIS",
    "PLATFORM_RECONSTRUCTED_1M_OHLCV",
    "PLATFORM_STRATEGY_FEATURES",
)

#: Carried over verbatim from the semantics Phase 3D.9S.2A proved. Tests assert
#: these equal the 3D.9S.2B tuples so the two contracts cannot drift.
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

#: The clock rules this recorder is bound to, stated so no reader has to infer
#: an accuracy claim from an integer-nanosecond API.
CLOCK_SEMANTICS_V1: Final = (
    "arrival_utc_is_the_platform_wall_clock_read_when_the_socket_yielded_the_message",
    "arrival_utc_resolution_is_measured_and_recorded_never_assumed_nanosecond",
    "monotonic_counter_orders_records_and_detects_wall_clock_steps",
    "monotonic_counter_is_process_local_and_never_compared_across_sessions",
    "wall_clock_regression_within_a_session_fails_closed",
    "wall_versus_monotonic_divergence_beyond_tolerance_is_a_clock_discontinuity",
    "a_clock_discontinuity_closes_the_coverage_interval_and_opens_an_explicit_gap",
    "process_restart_and_host_reboot_are_new_sessions_and_never_continuous_coverage",
    "arrival_is_not_event_at_not_effective_at_and_not_ingested_at",
)

#: Refusals that define acceptance, mirrored from the proven capture path.
CAPTURE_REFUSAL_RULES_V1: Final = (
    "records_rebuilt_from_rest_are_not_capture_evidence",
    "synthetic_or_generated_records_are_refused",
    "a_record_without_a_recorder_arrival_timestamp_is_refused",
    "a_topic_or_symbol_mismatch_is_malformed_identity_not_a_relabelling",
    "a_payload_is_stored_verbatim_and_never_re_serialized",
)

FIRST_PARTY_TERMS_VERSION_V1: Final = (
    "operator-declared:trade-platform-first-party-bybit-v5-public-websocket-capture:v1"
)

FIRST_PARTY_AUTHORIZATION_REFERENCE_V1: Final = (
    "operator-approved Phase 3Z.2 first-party capture contract: this platform "
    "records the official public wss://stream.bybit.com/v5/public/linear feed "
    "for CRYPTO:BYBIT:BTCUSDT:PERP, channels tickers and publicTrade only, with "
    "no credential, no account and no paid tier. Public market data only: no "
    "broker, account, order, execution or live-trading authority is implied."
)

FIRST_PARTY_TIMING_AUTHORIZATION_REFERENCE_V1: Final = (
    "Phase 3Z.2 evidence-tier registration: this platform measures and retains "
    "its own recorder arrival time for every captured message, with monotonic "
    "ordering evidence and explicit clock-discontinuity gaps, so the source's "
    "timing authority is PLATFORM_RECORDER_ARRIVAL_TIMESTAMP. Registration "
    "grants no verdict: a T4 evaluation still requires proven real provenance "
    "over a sealed dataset, which Phase 3Z.2 deliberately does not create."
)

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.first_party_capture_authority_v1")

#: Only this module may issue a first-party capture contract.
_ISSUER: Final = object()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FirstPartyCaptureContractV1:
    """The exact first-party capture semantics this authority stands for."""

    schema_version: str
    capture_provider: str
    originating_exchange: str
    origin_transport: str
    endpoint: str
    instrument_scope: str
    exchange_symbol: str
    authorized_channels: tuple[str, ...]
    capture_methodology: str
    source_availability_clock: str
    credential_required: bool
    generated_records_permitted: bool
    provider_captured_observations: tuple[str, ...]
    platform_derived_artifacts: tuple[str, ...]
    ticker_availability_rules: tuple[str, ...]
    bar_temporal_rules: tuple[str, ...]
    gap_authority_rules: tuple[str, ...]
    clock_semantics: tuple[str, ...]
    capture_refusal_rules: tuple[str, ...]
    record_schema_version: str
    capture_semantic_version: str
    provider_terms_version: str
    authorization_reference: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise FirstPartyCaptureAuthorityError(
                "first_party_capture_contract_is_issued_only_by_its_authority"
            )

    def identity_payload(self) -> dict[str, Any]:
        """Which actor, which feed, which instrument -- not how it is worded."""
        return {
            "schema_version": self.schema_version,
            "capture_provider": self.capture_provider,
            "originating_exchange": self.originating_exchange,
            "origin_transport": self.origin_transport,
            "endpoint": self.endpoint,
            "instrument_scope": self.instrument_scope,
            "exchange_symbol": self.exchange_symbol,
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
                "credential_required": self.credential_required,
                "generated_records_permitted": self.generated_records_permitted,
                "provider_captured_observations": list(self.provider_captured_observations),
                "platform_derived_artifacts": list(self.platform_derived_artifacts),
                "ticker_availability_rules": list(self.ticker_availability_rules),
                "bar_temporal_rules": list(self.bar_temporal_rules),
                "gap_authority_rules": list(self.gap_authority_rules),
                "clock_semantics": list(self.clock_semantics),
                "capture_refusal_rules": list(self.capture_refusal_rules),
                "record_schema_version": self.record_schema_version,
                "capture_semantic_version": self.capture_semantic_version,
            }
        )
        return payload

    def content_hash(self) -> str:
        return _sha256(self.contract_payload())

    @property
    def source_id(self) -> UUID:
        """Deterministic identity, derived from who and what -- never a flag."""
        return uuid5(_NAMESPACE, f"first-party-source:{_sha256(self.identity_payload())}")

    def topics(self) -> tuple[str, ...]:
        """The exact Bybit topic strings this contract subscribes to."""
        return tuple(f"{channel}.{self.exchange_symbol}" for channel in self.authorized_channels)


def first_party_bybit_capture_contract_v1() -> FirstPartyCaptureContractV1:
    """The one authorized first-party Bybit V5 public WebSocket capture contract."""
    return FirstPartyCaptureContractV1(
        schema_version=FIRST_PARTY_CAPTURE_CONTRACT_SCHEMA_VERSION,
        capture_provider=CAPTURE_PROVIDER_FIRST_PARTY,
        originating_exchange=ORIGINATING_EXCHANGE_BYBIT_V1,
        origin_transport=OriginTransportV1.BYBIT_V5_PUBLIC_WEBSOCKET.value,
        endpoint=BYBIT_PUBLIC_LINEAR_ENDPOINT_V1,
        instrument_scope=CAPTURED_INSTRUMENT_V1,
        exchange_symbol=CAPTURED_EXCHANGE_SYMBOL_V1,
        authorized_channels=tuple(
            sorted((BybitPublicChannelV1.TICKERS.value, BybitPublicChannelV1.PUBLIC_TRADE.value))
        ),
        capture_methodology=CaptureMethodologyV1.FIRST_PARTY_LIVE_WEBSOCKET_RECORDING.value,
        source_availability_clock=SourceAvailabilityClockV1.PLATFORM_RECORDER_WALL_CLOCK.value,
        credential_required=False,
        generated_records_permitted=False,
        provider_captured_observations=PROVIDER_CAPTURED_OBSERVATIONS_V1,
        platform_derived_artifacts=PLATFORM_DERIVED_ARTIFACTS_V1,
        ticker_availability_rules=TICKER_AVAILABILITY_RULES_V1,
        bar_temporal_rules=BAR_TEMPORAL_RULES_V1,
        gap_authority_rules=GAP_AUTHORITY_RULES_V1,
        clock_semantics=CLOCK_SEMANTICS_V1,
        capture_refusal_rules=CAPTURE_REFUSAL_RULES_V1,
        record_schema_version=FIRST_PARTY_RECORD_SCHEMA_VERSION_V1,
        capture_semantic_version=FIRST_PARTY_CAPTURE_SEMANTIC_VERSION_V1,
        provider_terms_version=FIRST_PARTY_TERMS_VERSION_V1,
        authorization_reference=FIRST_PARTY_AUTHORIZATION_REFERENCE_V1,
        _issuer=_ISSUER,
    )


def first_party_bybit_source_id_v1() -> UUID:
    """The deterministic first-party source identity, for registration and tests."""
    return first_party_bybit_capture_contract_v1().source_id
