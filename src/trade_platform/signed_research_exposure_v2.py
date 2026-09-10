"""Module 3J.2b.1 -- generic research-only signed exposure abstraction.

A research-only mathematical exposure abstraction: ``-cap &lt;= exposure &lt;=
+cap``, negative meaning short research exposure, zero meaning flat, and
positive meaning long research exposure. This module grants no ``OrderSide``,
leverage, margin, portfolio-allocation, risk-approval, order-authority or
execution-authority semantics -- it is a signed number with provenance, not a
trading primitive.

Deliberately structurally independent of ``trend_strategy_v2.py``:
``TrendSignalObservation`` keeps its existing ``0 &lt;= exposure &lt;=
maximum_exposure`` non-negative invariant unchanged, and this module neither
imports from nor is imported by that module. A signed short/flat/long research
signal is a distinct abstraction from Trend V2's long/flat signal, not a
retrofit of it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5


class SignedResearchExposureV2Error(ValueError):
    """Raised for any invalid signed research exposure observation or series."""


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SignedResearchExposureV2Error(f"{field_name}_must_be_timezone_aware")


def _finite(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise SignedResearchExposureV2Error(f"{field_name}_must_be_finite")


def _valid_cap(cap: Decimal) -> None:
    _finite(cap, "maximum_absolute_exposure")
    if not (Decimal("0") < cap <= Decimal("1")):
        raise SignedResearchExposureV2Error("maximum_absolute_exposure_out_of_bounds")


@dataclass(frozen=True, slots=True)
class SignedResearchSignalObservationV2:
    """One signed research exposure decision candidate for one instrument.

    ``evidence_content_hash`` is the deterministic content hash of whatever
    upstream research evidence (e.g. a future ``SubjectAwareTradableResearchEvidenceV2``)
    justified this exposure -- carried here only as an opaque identity string
    so a later consumer can trace the decision back to its evidence without
    this module depending on that evidence's concrete type.
    """

    instrument_id: str
    decision_at: datetime
    exposure: Decimal
    maximum_absolute_exposure: Decimal
    evidence_content_hash: str

    def validate(self) -> None:
        if not self.instrument_id.strip():
            raise SignedResearchExposureV2Error("signed_research_signal_instrument_missing")
        _aware(self.decision_at, "decision_at")
        _finite(self.exposure, "exposure")
        _valid_cap(self.maximum_absolute_exposure)
        if not (-self.maximum_absolute_exposure <= self.exposure <= self.maximum_absolute_exposure):
            raise SignedResearchExposureV2Error("signed_exposure_out_of_bounds")
        if len(self.evidence_content_hash) != 64:
            raise SignedResearchExposureV2Error("evidence_content_hash_missing")

    @property
    def direction(self) -> str:
        if self.exposure > 0:
            return "LONG"
        if self.exposure < 0:
            return "SHORT"
        return "FLAT"


def _canonical_series_payload(
    instrument_id: str,
    maximum_absolute_exposure: Decimal,
    observations: tuple[SignedResearchSignalObservationV2, ...],
) -> dict[str, object]:
    return {
        "instrument_id": instrument_id,
        "maximum_absolute_exposure": str(maximum_absolute_exposure),
        "observations": [
            {
                "decision_at": observation.decision_at.isoformat(),
                "exposure": str(observation.exposure),
                "maximum_absolute_exposure": str(observation.maximum_absolute_exposure),
                "evidence_content_hash": observation.evidence_content_hash,
            }
            for observation in observations
        ],
    }


@dataclass(frozen=True, slots=True)
class SignedResearchSignalSeriesV2:
    """A deterministic, content-hashed, chronological series for one instrument."""

    instrument_id: str
    maximum_absolute_exposure: Decimal
    observations: tuple[SignedResearchSignalObservationV2, ...]
    content_hash: str
    series_id: UUID

    @classmethod
    def create(
        cls,
        *,
        instrument_id: str,
        maximum_absolute_exposure: Decimal,
        observations: tuple[SignedResearchSignalObservationV2, ...],
    ) -> SignedResearchSignalSeriesV2:
        payload = _canonical_series_payload(instrument_id, maximum_absolute_exposure, observations)
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        series_id = uuid5(NAMESPACE_URL, f"signed-research-signal-series-v2:{content_hash}")
        series = cls(instrument_id, maximum_absolute_exposure, observations, content_hash, series_id)
        series.validate()
        return series

    def validate(self) -> None:
        if not self.instrument_id.strip():
            raise SignedResearchExposureV2Error("signed_research_series_instrument_missing")
        _valid_cap(self.maximum_absolute_exposure)
        if not self.observations:
            raise SignedResearchExposureV2Error("signed_research_series_requires_observations")
        seen_decisions: set[datetime] = set()
        previous: datetime | None = None
        for observation in self.observations:
            if observation.instrument_id != self.instrument_id:
                raise SignedResearchExposureV2Error("signed_research_series_mixed_instrument")
            if observation.maximum_absolute_exposure != self.maximum_absolute_exposure:
                raise SignedResearchExposureV2Error("signed_research_series_cap_mismatch")
            observation.validate()
            if observation.decision_at in seen_decisions:
                raise SignedResearchExposureV2Error("duplicate_signed_research_decision_identity")
            seen_decisions.add(observation.decision_at)
            if previous is not None and observation.decision_at <= previous:
                raise SignedResearchExposureV2Error("signed_research_series_not_chronological")
            previous = observation.decision_at
        expected_hash = hashlib.sha256(
            json.dumps(
                _canonical_series_payload(
                    self.instrument_id, self.maximum_absolute_exposure, self.observations
                ),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if self.content_hash != expected_hash:
            raise SignedResearchExposureV2Error("signed_research_series_content_hash_mismatch")
