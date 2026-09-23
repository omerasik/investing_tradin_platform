"""Phase 3D.9S.2A -- causal Bybit ``tickers`` state reconstruction and PIT basis observation.

``RESEARCH_ENGINEERING_EVIDENCE_ONLY``. Reconstructs mark and index price from
the captured Bybit V5 ``tickers`` snapshot/delta stream
(:mod:`trade_platform.tardis_capture_evidence_v1`) and emits a basis observation
whose availability instant is *measured*, not assumed. This module is the direct
answer to 3D.9S.1's blocker: the canonical REST path assumes a value became
knowable at a computed bar close, whereas a capture records the instant the
recorder actually received it.

It is not the canonical basis feature. It writes no table, touches no sealed
dataset, produces no ``FeatureMaterializationV2``, and its output is explicitly
**not** ``REAL_DATA_RESEARCH_EVIDENCE``.

**Mark and index are two independent observations that happen to share a
channel.** Bybit sends ``markPrice`` and ``indexPrice`` asynchronously; over the
free sample most messages carry one, not both. Requiring them in a single
message would discard nearly all evidence, so state is kept per component and
each component retains *its own* arrival timestamp.

**A delta changes only the fields it carries.** An absent ``markPrice`` means
"unchanged", never "null" and never "zero". A ``snapshot`` is different in kind:
it is the venue re-declaring authoritative state, so it *replaces* state -- a
component the snapshot does not carry is reset to unobserved rather than
surviving from before the reconnect it implies.

**Nothing exists before both components have been observed.** Delta traffic
preceding the first usable snapshot produces no basis at all. There is no seed
value, no forward fill from a neighbouring day, and no default.

**Availability is the later of the two component arrivals.**
``research_available_at = max(mark_local_timestamp, index_local_timestamp)``,
compared in exact integer nanoseconds because the recorder clock is finer than
:class:`datetime`. A component is only ever the one already observed at or
before the emitting record, so no future observation can fill an earlier state;
:func:`reconstruct_bybit_ticker_basis` re-asserts that invariant per emission
rather than trusting the loop that maintains it.

**Fail closed on anything ambiguous.** A non-numeric, empty, non-finite,
non-positive index or non-positive mark is a malformed observation, not a
datapoint to skip quietly: it aborts the reconstruction.

The formula is unchanged from the canonical feature -- ``(mark - index) /
index`` -- computed in :class:`~decimal.Decimal` and quantized to a declared
scale *before* hashing, so a hash and a value can never diverge.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .tardis_capture_evidence_v1 import (
    CAPTURE_LIFECYCLE_V1,
    TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
    TardisChannelV1,
    TardisMessageTypeV1,
    TardisRawCaptureRecordV1,
    canonical_hash,
    nanos_to_datetime,
)

#: Same formula and name as the canonical ``crypto_mark_index_basis`` feature.
#: The *formula* is shared; the availability semantics deliberately are not, so
#: the version is this module's own and never claims the canonical one.
BASIS_FORMULA_V1: Final = "(mark_price - index_price) / index_price"
BASIS_FORMULA_SEMANTIC_VERSION_V1: Final = "tardis-capture-basis-1.0.0"

#: Quantization applied before hashing or comparing. A basis is a dimensionless
#: ratio around 1e-4, so 18 decimal places is far beyond the venue's own price
#: resolution while keeping every value exactly representable and reproducible.
BASIS_QUANTUM_V1: Final = Decimal("1E-18")

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.bybit_ticker_state_reconstruction_v1")


class BybitTickerStateReconstructionError(ValueError):
    """Raised for malformed ticker values or a violated causality invariant."""


class ReferencePriceComponentV1(StrEnum):
    MARK = "MARK"
    INDEX = "INDEX"


#: The exchange-native field each component is carried in.
_COMPONENT_FIELDS: Final[dict[ReferencePriceComponentV1, str]] = {
    ReferencePriceComponentV1.MARK: "markPrice",
    ReferencePriceComponentV1.INDEX: "indexPrice",
}


@dataclass(frozen=True, slots=True)
class ReferencePriceComponentObservationV1:
    """One observed mark or index value bound to the exact record that carried it."""

    component: ReferencePriceComponentV1
    value: Decimal
    exchange_timestamp: datetime | None
    local_timestamp_nanos: int
    record_content_hash: str
    record_id: UUID
    message_type: TardisMessageTypeV1

    @property
    def local_timestamp(self) -> datetime:
        return nanos_to_datetime(self.local_timestamp_nanos)


@dataclass(frozen=True, slots=True)
class BybitTickerStateV1:
    """Reconstructed causal state: at most one current observation per component."""

    mark: ReferencePriceComponentObservationV1 | None = None
    index: ReferencePriceComponentObservationV1 | None = None

    @property
    def is_complete(self) -> bool:
        return self.mark is not None and self.index is not None


@dataclass(frozen=True, slots=True)
class PitBasisObservationV1:
    """One point-in-time basis observation with per-component arrival evidence.

    ``research_available_at`` is the measured instant at which *both* components
    backing this value had arrived at the recorder. It is the earliest instant a
    researcher could have held this number, and it is never earlier than either
    component's own arrival.
    """

    symbol: str
    basis_value: Decimal
    mark_value: Decimal
    index_value: Decimal
    mark_record_id: UUID
    mark_record_content_hash: str
    index_record_id: UUID
    index_record_content_hash: str
    mark_exchange_timestamp: datetime | None
    index_exchange_timestamp: datetime | None
    mark_local_timestamp_nanos: int
    index_local_timestamp_nanos: int
    research_available_at_nanos: int
    emitting_record_id: UUID
    emitting_record_content_hash: str
    formula: str
    formula_semantic_version: str
    basis_quantum: str
    content_hash: str

    @property
    def research_available_at(self) -> datetime:
        return nanos_to_datetime(self.research_available_at_nanos)

    @property
    def mark_local_timestamp(self) -> datetime:
        return nanos_to_datetime(self.mark_local_timestamp_nanos)

    @property
    def index_local_timestamp(self) -> datetime:
        return nanos_to_datetime(self.index_local_timestamp_nanos)

    @property
    def observation_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"tardis-pit-basis-observation-v1:{self.content_hash}")


@dataclass(frozen=True, slots=True)
class BybitTickerReconstructionV1:
    """Deterministic, content-hashed result of replaying one ticker capture.

    The counters are descriptive capture statistics, not a research claim: they
    exist so a reviewer can see how asynchronous the real feed is without this
    module asserting anything economic about it.
    """

    symbol: str
    observations: tuple[PitBasisObservationV1, ...]
    final_state: BybitTickerStateV1
    record_count: int
    snapshot_count: int
    delta_count: int
    mark_update_count: int
    index_update_count: int
    both_component_message_count: int
    records_before_state_complete: int
    state_reset_count: int
    content_hash: str

    @property
    def distinct_availability_instants(self) -> tuple[int, ...]:
        return tuple(sorted({o.research_available_at_nanos for o in self.observations}))


def _component_value(raw: Any, component: ReferencePriceComponentV1) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, str | int | float):
        raise BybitTickerStateReconstructionError(f"bybit_ticker_{component.value.lower()}_malformed")
    if isinstance(raw, str) and not raw.strip():
        raise BybitTickerStateReconstructionError(f"bybit_ticker_{component.value.lower()}_empty")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise BybitTickerStateReconstructionError(
            f"bybit_ticker_{component.value.lower()}_malformed"
        ) from exc
    if not value.is_finite():
        raise BybitTickerStateReconstructionError(f"bybit_ticker_{component.value.lower()}_not_finite")
    if value <= 0:
        raise BybitTickerStateReconstructionError(f"bybit_ticker_{component.value.lower()}_not_positive")
    return value


def compute_basis_v1(*, mark_value: Decimal, index_value: Decimal) -> Decimal:
    """``(mark - index) / index``, quantized before any hash or comparison."""
    if not mark_value.is_finite() or not index_value.is_finite():
        raise BybitTickerStateReconstructionError("bybit_ticker_basis_inputs_not_finite")
    if index_value <= 0:
        raise BybitTickerStateReconstructionError("bybit_ticker_basis_index_not_positive")
    return ((mark_value - index_value) / index_value).quantize(BASIS_QUANTUM_V1, rounding=ROUND_HALF_EVEN)


def _observation_content_hash(
    *,
    symbol: str,
    basis_value: Decimal,
    mark: ReferencePriceComponentObservationV1,
    index: ReferencePriceComponentObservationV1,
    research_available_at_nanos: int,
    emitting_record: TardisRawCaptureRecordV1,
) -> str:
    return canonical_hash(
        {
            "lifecycle": CAPTURE_LIFECYCLE_V1,
            "parser_semantic_version": TARDIS_CAPTURE_PARSER_SEMANTIC_VERSION,
            "formula": BASIS_FORMULA_V1,
            "formula_semantic_version": BASIS_FORMULA_SEMANTIC_VERSION_V1,
            "basis_quantum": str(BASIS_QUANTUM_V1),
            "symbol": symbol,
            "basis_value": str(basis_value),
            "mark_value": str(mark.value),
            "index_value": str(index.value),
            "mark_record_content_hash": mark.record_content_hash,
            "index_record_content_hash": index.record_content_hash,
            "mark_exchange_timestamp": (
                None if mark.exchange_timestamp is None else mark.exchange_timestamp.isoformat()
            ),
            "index_exchange_timestamp": (
                None if index.exchange_timestamp is None else index.exchange_timestamp.isoformat()
            ),
            "mark_local_timestamp_nanos": mark.local_timestamp_nanos,
            "index_local_timestamp_nanos": index.local_timestamp_nanos,
            "research_available_at_nanos": research_available_at_nanos,
            "emitting_record_content_hash": emitting_record.content_hash,
        }
    )


def reconstruct_bybit_ticker_basis(
    records: Sequence[TardisRawCaptureRecordV1],
    *,
    symbol: str,
) -> BybitTickerReconstructionV1:
    """Replay a captured ``tickers`` stream into PIT basis observations.

    One observation is emitted per record that updates at least one component
    while both components are known. Records before the state is complete emit
    nothing at all.
    """
    if not symbol.strip():
        raise BybitTickerStateReconstructionError("bybit_ticker_symbol_required")

    state = BybitTickerStateV1()
    observations: list[PitBasisObservationV1] = []
    snapshot_count = delta_count = 0
    mark_update_count = index_update_count = both_component_message_count = 0
    records_before_state_complete = 0
    state_reset_count = 0
    previous_nanos: int | None = None

    for record in records:
        if record.channel is not TardisChannelV1.TICKERS:
            raise BybitTickerStateReconstructionError("bybit_ticker_wrong_channel")
        if record.symbol != symbol:
            raise BybitTickerStateReconstructionError("bybit_ticker_symbol_mismatch")
        if previous_nanos is not None and record.local_timestamp_nanos < previous_nanos:
            raise BybitTickerStateReconstructionError("bybit_ticker_arrival_order_regression")
        previous_nanos = record.local_timestamp_nanos

        data = record.payload.get("data")
        if not isinstance(data, dict):
            raise BybitTickerStateReconstructionError("bybit_ticker_data_not_an_object")

        observed: dict[ReferencePriceComponentV1, ReferencePriceComponentObservationV1] = {}
        for component, field_name in _COMPONENT_FIELDS.items():
            if field_name not in data:
                continue
            observed[component] = ReferencePriceComponentObservationV1(
                component=component,
                value=_component_value(data[field_name], component),
                exchange_timestamp=record.exchange_timestamp,
                local_timestamp_nanos=record.local_timestamp_nanos,
                record_content_hash=record.content_hash,
                record_id=record.record_id,
                message_type=record.message_type,
            )

        if record.message_type is TardisMessageTypeV1.SNAPSHOT:
            snapshot_count += 1
            # A snapshot re-declares authoritative state. A component it does not
            # carry is reset to unobserved: surviving state across a reconnect
            # would be an assumption the venue did not make.
            if state.mark is not None or state.index is not None:
                state_reset_count += 1
            state = BybitTickerStateV1(
                mark=observed.get(ReferencePriceComponentV1.MARK),
                index=observed.get(ReferencePriceComponentV1.INDEX),
            )
        else:
            delta_count += 1
            # A delta changes only the fields actually present.
            state = BybitTickerStateV1(
                mark=observed.get(ReferencePriceComponentV1.MARK, state.mark),
                index=observed.get(ReferencePriceComponentV1.INDEX, state.index),
            )

        if ReferencePriceComponentV1.MARK in observed:
            mark_update_count += 1
        if ReferencePriceComponentV1.INDEX in observed:
            index_update_count += 1
        if len(observed) == 2:
            both_component_message_count += 1

        if not observed:
            continue
        if not state.is_complete:
            records_before_state_complete += 1
            continue

        mark = state.mark
        index = state.index
        if mark is None or index is None:
            # Unreachable once is_complete holds. Raised rather than asserted so
            # the guard survives an optimised interpreter, where `assert` does not.
            raise BybitTickerStateReconstructionError("bybit_ticker_state_incomplete")
        research_available_at_nanos = max(mark.local_timestamp_nanos, index.local_timestamp_nanos)
        # Causality, re-asserted rather than assumed: neither component may come
        # from the future of the record that triggered this emission, and the
        # availability instant is exactly the later component arrival.
        if research_available_at_nanos > record.local_timestamp_nanos:
            raise BybitTickerStateReconstructionError("bybit_ticker_future_component_leak")
        if research_available_at_nanos < min(mark.local_timestamp_nanos, index.local_timestamp_nanos):
            raise BybitTickerStateReconstructionError("bybit_ticker_availability_precedes_component")

        basis_value = compute_basis_v1(mark_value=mark.value, index_value=index.value)
        content_hash = _observation_content_hash(
            symbol=symbol,
            basis_value=basis_value,
            mark=mark,
            index=index,
            research_available_at_nanos=research_available_at_nanos,
            emitting_record=record,
        )
        observations.append(
            PitBasisObservationV1(
                symbol=symbol,
                basis_value=basis_value,
                mark_value=mark.value,
                index_value=index.value,
                mark_record_id=mark.record_id,
                mark_record_content_hash=mark.record_content_hash,
                index_record_id=index.record_id,
                index_record_content_hash=index.record_content_hash,
                mark_exchange_timestamp=mark.exchange_timestamp,
                index_exchange_timestamp=index.exchange_timestamp,
                mark_local_timestamp_nanos=mark.local_timestamp_nanos,
                index_local_timestamp_nanos=index.local_timestamp_nanos,
                research_available_at_nanos=research_available_at_nanos,
                emitting_record_id=record.record_id,
                emitting_record_content_hash=record.content_hash,
                formula=BASIS_FORMULA_V1,
                formula_semantic_version=BASIS_FORMULA_SEMANTIC_VERSION_V1,
                basis_quantum=str(BASIS_QUANTUM_V1),
                content_hash=content_hash,
            )
        )

    content_hash = canonical_hash(
        {
            "lifecycle": CAPTURE_LIFECYCLE_V1,
            "formula_semantic_version": BASIS_FORMULA_SEMANTIC_VERSION_V1,
            "symbol": symbol,
            "record_count": len(records),
            "observation_content_hashes": [o.content_hash for o in observations],
        }
    )
    return BybitTickerReconstructionV1(
        symbol=symbol,
        observations=tuple(observations),
        final_state=state,
        record_count=len(records),
        snapshot_count=snapshot_count,
        delta_count=delta_count,
        mark_update_count=mark_update_count,
        index_update_count=index_update_count,
        both_component_message_count=both_component_message_count,
        records_before_state_complete=records_before_state_complete,
        state_reset_count=state_reset_count,
        content_hash=content_hash,
    )
