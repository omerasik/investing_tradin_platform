"""Read-only sealed T4 trade references for paper gross unrealized returns.

No prices are persisted or fetched. A caller declares the observation minute,
source/session/instrument, capture coverage bound and maximum age. Raw-replayed
seals and their frame bytes are checked again, and capture acceptance is derived
from verified archive manifests. Missing evidence yields UNAVAILABLE. This is
public first-trade evidence, never a venue settlement mark or valuation approval.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, localcontext
from pathlib import Path
from typing import Any
from uuid import UUID

from .first_party_capture_authority_v1 import resolve_t4_registered_first_party_capture_contract_v1
from .first_party_capture_hourly_acceptance_v1 import (
    HOUR_NANOS,
    derive_hourly_acceptance_v1,
    proven_windows_between_v1,
)
from .first_party_t4_forward_bars_v1 import (
    T4PriceConventionV1,
    T4PriceReferenceV1,
    forward_bars_from_seal_v1,
    resolve_t4_price_reference_v1,
)
from .first_party_t4_seal_v1 import FirstPartyT4SealV1, iter_frame_rows_v1
from .paper_economics_v1 import ProvenMarkV1
from .research_data_plane_v1 import T4_TRADE_FRAME, ResearchFrameStoreV1
from .strategy_lab_study_v1 import identity_hash_v1

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _micros(value: datetime) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("instant_must_be_timezone_aware")
    return (value.astimezone(UTC) - _EPOCH) // timedelta(microseconds=1)


@dataclass(frozen=True, slots=True)
class PaperTradePriceResolutionV1:
    status: str
    reasons: tuple[str, ...]
    mark: ProvenMarkV1 | None = None
    reference: T4PriceReferenceV1 | None = None
    evidence: Mapping[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        return {"status": self.status, "reasons": list(self.reasons),
                "mark": None if self.mark is None else self.mark.payload(),
                "evidence": None if self.evidence is None else dict(self.evidence)}

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.payload())


def resolve_paper_trade_price_v1(
    seals: Sequence[FirstPartyT4SealV1], *, store: ResearchFrameStoreV1,
    capture_root: Path, source_id: UUID, session_id: UUID, instrument: str,
    symbol: str, instant: datetime, entry_bar_open_micros: int, as_of: datetime,
    max_age_micros: int, capture_max_unproven_nanos: int,
) -> PaperTradePriceResolutionV1:
    """Resolve exactly the declared minute; never substitute a nearby/latest price.

    ``seals`` must be issued by sealing or the raw-replaying catalogue reader.
    An unverified dataset/bar/reference object cannot stand in for a seal. Age is
    measured from the venue observation minute, not from later ingestion/sealing.
    Both the completed market knowledge and seal audit time must be <= as_of.
    The result's mark can be passed unchanged to ``paper_economics_v1(marks=...)``.
    """
    try:
        now, observed = _micros(as_of), _micros(instant)
        for value, name in ((entry_bar_open_micros, "entry_bar_open_micros"),
                            (max_age_micros, "max_age_micros"),
                            (capture_max_unproven_nanos, "capture_max_unproven_nanos")):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name}_must_be_an_integer")
        if max_age_micros < 0:
            raise ValueError("max_age_micros_must_be_nonnegative")
        if not 0 <= capture_max_unproven_nanos < HOUR_NANOS:
            raise ValueError("capture_bound_must_be_within_one_hour")
        if observed <= entry_bar_open_micros:
            raise ValueError("PROVEN_MARK_NOT_AFTER_THE_ENTRY_FILL")
        if observed > now:
            raise ValueError("PRICE_OBSERVATION_AFTER_AS_OF")
        if now - observed > max_age_micros:
            raise ValueError("PROVEN_T4_PRICE_STALE")
        contract = resolve_t4_registered_first_party_capture_contract_v1(source_id)
        if contract is None or contract.instrument_scope != instrument or contract.exchange_symbol != symbol:
            raise ValueError("PRICE_SOURCE_INSTRUMENT_SYMBOL_MISMATCH")
        if not seals:
            raise ValueError("NO_SEALED_T4_PRICE_DATASET")
        datasets = []
        for seal in seals:
            if not isinstance(seal, FirstPartyT4SealV1) or not seal.integrity_verified():
                raise ValueError("PRICE_SEAL_NOT_INTACT")
            if _micros(seal.sealed_at) > now:
                raise ValueError("PRICE_SEAL_NOT_KNOWN_BY_AS_OF")
            with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                dataset = forward_bars_from_seal_v1(seal, store=store)
            if (dataset.source_id, dataset.session_id, dataset.instrument, dataset.exchange_symbol) != (
                    source_id, session_id, instrument, symbol):
                raise ValueError("PRICE_DATASET_SOURCE_SESSION_INSTRUMENT_MISMATCH")
            datasets.append(dataset)
        if len({item.dataset_version_id for item in datasets}) != len(datasets):
            raise ValueError("DUPLICATE_PRICE_DATASET")
        bars = [bar for dataset in datasets for bar in dataset.bars if bar.bar_open_micros == observed]
        if len(bars) != 1:
            raise ValueError("NO_UNIQUE_PROVEN_T4_PRICE_BAR")
        bar = bars[0]
        start = bar.clock.first_arrival_utc_nanos // HOUR_NANOS * HOUR_NANOS
        end = (bar.clock.last_arrival_utc_nanos // HOUR_NANOS + 1) * HOUR_NANOS
        windows = proven_windows_between_v1(capture_root, contract,
                    start_utc_nanos=start, end_utc_nanos=end)
        # Later coverage cannot prove an earlier reading's capture acceptance.
        windows = tuple(window for window in windows if window.interval.end_utc_nanos <= now * 1000)
        acceptance = derive_hourly_acceptance_v1([(symbol, windows)],
                    max_unproven_nanos=capture_max_unproven_nanos, required_consecutive_hours=1)
        reference = resolve_t4_price_reference_v1(datasets, instant=instant,
                    convention=T4PriceConventionV1.FIRST_TRADE_OF_MINUTE_STARTING_AT_INSTANT,
                    acceptance=acceptance, known_by=as_of)
        selected = next(seal for seal in seals if seal.dataset_version_id == reference.dataset_version_id)
        names = T4_TRADE_FRAME.schema.names
        matches = [dict(zip(names, row, strict=True)) for row in
                   iter_frame_rows_v1(selected, T4_TRADE_FRAME.kind, store)
                   if row[names.index("observation_reference")] == bar.first_trade_reference]
        if len(matches) != 1:
            raise ValueError("PRICE_TRADE_OBSERVATION_NOT_UNIQUE")
        trade = matches[0]
        evidence = {"schema_version": "paper-proven-trade-price-v1",
                    "source_id": str(source_id), "reference": reference.payload(),
                    "bar": bar.payload(), "sealed_at_micros": _micros(selected.sealed_at),
                    "frame_manifests": dict(selected.frame_manifests),
                    "capture_coverage": [{"session_id": str(window.session_id),
                                          "interval": window.interval.to_payload()} for window in windows],
                    "venue_trade_ts_millis": int(trade["trade_ts_millis"]),
                    "host_arrival_utc_nanos": int(trade["arrival_utc_nanos"]),
                    "entry_bar_open_micros": entry_bar_open_micros, "as_of_micros": now,
                    "max_age_micros": max_age_micros,
                    "capture_max_unproven_nanos": capture_max_unproven_nanos,
                    "price_class": "T4_PUBLIC_TRADE_NOT_VENUE_SETTLEMENT_MARK"}
        mark = ProvenMarkV1(symbol, format(reference.price, "f"), observed,
                           max(bar.complete_market_knowledge_micros, _micros(selected.sealed_at)),
                           identity_hash_v1(evidence))
        return PaperTradePriceResolutionV1("AVAILABLE", (), mark, reference, evidence)
    except OSError:
        return PaperTradePriceResolutionV1("UNAVAILABLE", ("PRICE_SOURCE_IO_UNAVAILABLE",))
    except (ValueError, KeyError, TypeError) as error:
        return PaperTradePriceResolutionV1("UNAVAILABLE", (str(error),))
