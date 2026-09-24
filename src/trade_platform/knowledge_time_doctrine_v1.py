"""Phase R2A.1 -- the three-clock knowledge-time doctrine.

``RESEARCH_ONLY``. No table, no migration, no engine, no provider call, no
economic assumption. This module answers one question about one decision:

    *At what instant could a market participant have known every input this
    decision used, and what claim can the result therefore carry?*

Why a doctrine at all
---------------------
Before R2A a feature's ``knowledge_at`` was ``max(normalized_at,
dataset.created_at)`` -- the platform's own ingestion/seal instant -- and the
canonical decision time also took ``computed_at``. That is a statement about
when *this platform* wrote a row, not about when the *market* could know the
value. On the 150-day T1 composite every one of 216,000 basis materializations
shares one such instant, so every decision time collapsed onto it
(``UNPROVEN_DISTINCT_FEATURE_DECISION_TIMES``). Recomputing the same feature
next week would move every historical decision time by a week. Both defects
come from one category error: an operational clock used as a market clock.

The clocks
----------
``event_at``
    Market/economic time: when the observed thing happened.
``market_knowledge_at``
    When a subscriber could first have known the value, expressed on the
    venue/UTC timescale the market events use. Set by the evidence tier and
    nothing else:

    * ``T4_FIRST_PARTY_CAPTURE`` -- recorder arrival plus a measured,
      evidence-referenced upper bound on how far the host clock ran *behind*
      the venue clock (which already includes the host clock's resolution).
      Arrivals are recorded on the host clock; a host running 9.47 s behind
      would otherwise label a message as known 9.47 s before it arrived, which
      is look-ahead against venue-timed bars. A host running *ahead* only makes
      the recorded arrival later, which is conservative, so a negative bound
      adds nothing. No bound, no knowledge time.
    * ``T3_PUBLICATION_TIME`` -- the publisher's own publication time. This
      doctrine takes that clock as UTC-aligned **because the T3 timing
      contract certifies it**: registering a T3 source in
      :func:`~trade_platform.evidence_tier_authority_v1.authorized_timing_contracts_v1`
      is an owner decision that must attest the publisher's clock discipline
      (none is registered today). A publisher whose clock cannot be attested
      is not T3.
    * ``T2_EVENT_TIME`` -- ``event_at`` plus the publication lag that is
      declared and bound into the dataset's evidence-tier verdict. Conditional
      only. This module never chooses a lag.
    * ``T1_RETROSPECTIVE`` and ``T0_SYNTHETIC`` -- **undefined**. Not early,
      not late, not "the seal time": undefined, and every consumer must fail
      closed on it.

``platform_recorded_at``
    When this platform normalized or sealed the row. Live audit only. It is
    carried for coherence checks and is **never** read by a historical
    decision time, nor part of any market identity: re-normalizing the same
    evidence later changes an object's audit hash, never its market identity
    or any decision-time identity.
``computed_at``
    When a feature value was calculated, on the host clock. Operational only.
    It enters a *live* decision time -- converted to the venue timescale with
    the same kind of measured host-clock bound as a T4 arrival -- and never a
    *historical* one.

The rules
---------
* A feature's ``market_knowledge_at`` is the maximum over its inputs. One
  input with an undefined knowledge time makes the feature's undefined.
* Historical replay decision time = ``market_knowledge_at`` + a *declared*
  compute latency. The latency has no default; it is a declared methodology
  input with a reference, bound into the decision's identity.
* Live decision time = ``max(market_knowledge_at, computed_at on the venue
  timescale)``. A computation that finished before its inputs were knowable
  is incoherent evidence and is refused, not clamped.
* A result's claim ceiling is the minimum claim over its inputs.

Claim, not tier, is what propagates
-----------------------------------
T3 and T4 are different clocks but the same *claim* (professional evidence
quality), so there is no meaningful "minimum" of T3 and T4. What propagates is
:class:`ClaimCeilingV1`, derived from the verdict's own eligibility flags --
never from the tier name alone -- and from whether a knowledge time was
actually derivable. A T4 verdict with missing clock evidence is real data with
no defensible knowledge time: it drops to ``DESCRIPTIVE``, exactly like T1.

OR-4 (role-based tier rule) is not decided here
-----------------------------------------------
Whether execution/marking inputs (e.g. the bars a trade is priced on) may sit
at a lower tier than decision inputs is owner decision OR-4. Until it is
approved the stricter rule applies: :func:`result_claim_ceiling_v1` takes the
minimum over *every* input, decision and execution/marking alike.

Only this module issues these objects
-------------------------------------
Every derived object is frozen, content-hashed and issuable only here (the same
issuer-token pattern as :mod:`trade_platform.evidence_tier_authority_v1`), so a
fixture cannot mint a knowledge time by filling in the same field names. The
evidence tier comes only from an intact :class:`EvidenceTierVerdictV1` bound to
the observation's own dataset identity and content hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .evidence_tier_authority_v1 import (
    EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION,
    EvidenceTierV1,
    EvidenceTierVerdictV1,
)

KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION: Final = "knowledge-time-doctrine-v1"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.knowledge_time_doctrine_v1")

#: Only this module may issue observation knowledge, feature knowledge,
#: decision times and claim ceilings.
_ISSUER: Final = object()

_NANOS_PER_MICRO: Final = 1000


class KnowledgeTimeDoctrineError(ValueError):
    """Raised on a fabricated object or a structurally malformed input."""


class ClockV1(StrEnum):
    """The four clocks. Named so a reason string can say which one is at fault."""

    EVENT_AT = "event_at"
    MARKET_KNOWLEDGE_AT = "market_knowledge_at"
    PLATFORM_RECORDED_AT = "platform_recorded_at"
    COMPUTED_AT = "computed_at"


class ClaimCeilingV1(IntEnum):
    """The strongest claim a body of evidence can carry. Ordered: min propagates."""

    NONE = 0
    DESCRIPTIVE = 1
    CONDITIONAL = 2
    PROFESSIONAL = 3


class DecisionModeV1(StrEnum):
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"
    LIVE = "LIVE"


class InputRoleV1(StrEnum):
    """What an input does in a result. Recorded now so OR-4 needs no re-plumbing."""

    DECISION = "DECISION"
    EXECUTION_MARKING = "EXECUTION_MARKING"


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise KnowledgeTimeDoctrineError(f"{name}_must_be_timezone_aware")


def _iso(value: datetime | None) -> str | None:
    """UTC ISO form, so one instant has one identity whatever offset it was written in."""
    return None if value is None else value.astimezone(UTC).isoformat()


def _is_integer_nanos(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _plus_nanos_rounded_up(instant: datetime, nanos: int) -> datetime:
    """``instant + nanos``, rounded *up* to the next microsecond, never down.

    ``datetime`` holds microseconds. Rounding a knowledge time down would make
    a value knowable earlier than the evidence allows; rounding up only ever
    makes it later, which is the conservative direction.
    """
    if not _is_integer_nanos(nanos) or nanos < 0:
        raise KnowledgeTimeDoctrineError("knowledge_offset_nanos_must_be_non_negative_integer")
    micros = -(-nanos // _NANOS_PER_MICRO)
    return instant + timedelta(microseconds=micros)


def _claim_from_verdict(verdict: EvidenceTierVerdictV1) -> ClaimCeilingV1:
    if verdict.is_professional_evidence():
        return ClaimCeilingV1.PROFESSIONAL
    if verdict.is_conditional_research_evidence():
        return ClaimCeilingV1.CONDITIONAL
    if (
        verdict.integrity_verified()
        and verdict.descriptive_research_eligible
        and not verdict.reasons
        and verdict.tier != EvidenceTierV1.T0_SYNTHETIC.value
    ):
        return ClaimCeilingV1.DESCRIPTIVE
    return ClaimCeilingV1.NONE


def _ordered_reasons(reasons: Iterable[str]) -> tuple[str, ...]:
    """Deduplicated and sorted, so identity never depends on input order."""
    return tuple(sorted(set(reasons)))


# ---------------------------------------------------------------------------
# Host-clock evidence (T4 arrivals, live computed_at)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HostClockBoundV1:
    """A measured upper bound on how far a host clock ran behind the venue clock.

    ``venue_minus_host_upper_bound_nanos`` is the worst case of
    ``venue_clock - host_clock`` over the window the reading belongs to,
    *including* the host clock's resolution -- for an RTT-bounded sample that
    is ``offset_estimate + half_round_trip + resolution``. Deriving it from a
    session's recorded samples (and deciding whether drift between samples is
    bounded) is the normalizer's job (R3A); this doctrine only consumes it and
    requires it to name its evidence.
    """

    venue_minus_host_upper_bound_nanos: int
    evidence_reference: str

    def validate(self) -> None:
        if not _is_integer_nanos(self.venue_minus_host_upper_bound_nanos):
            raise KnowledgeTimeDoctrineError("host_clock_bound_must_be_integer_nanos")
        if not self.evidence_reference.strip():
            raise KnowledgeTimeDoctrineError("host_clock_bound_requires_evidence_reference")

    def payload(self) -> dict[str, Any]:
        return {
            "venue_minus_host_upper_bound_nanos": self.venue_minus_host_upper_bound_nanos,
            "evidence_reference": self.evidence_reference.strip(),
        }

    def venue_upper_bound(self, host_reading: datetime) -> datetime:
        """The latest venue instant ``host_reading`` can correspond to."""
        return _plus_nanos_rounded_up(
            host_reading, max(0, self.venue_minus_host_upper_bound_nanos)
        )


#: The name the plan uses for T4; the same measured bound as any host reading.
ArrivalClockBoundV1 = HostClockBoundV1


# ---------------------------------------------------------------------------
# Issuance helper: every object has a market identity and an audit hash
# ---------------------------------------------------------------------------


def _hashes(market_payload: Mapping[str, Any], audit_extra: Mapping[str, Any]) -> tuple[str, str]:
    market_hash = _sha256(market_payload)
    audit_hash = _sha256({"market_content_hash": market_hash, **audit_extra})
    return market_hash, audit_hash


# ---------------------------------------------------------------------------
# Observation knowledge
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObservationKnowledgeV1:
    """One observation's clocks, derived from its dataset's evidence-tier verdict.

    ``market_knowledge_at`` is ``None`` whenever it is undefined, and
    ``reasons`` then says why. ``claim_ceiling`` is what this single input can
    support; it is never above what the verdict alone would allow.

    ``market_content_hash`` (and ``knowledge_id``) cover everything that can
    affect a knowledge or decision time, including the tier-specific clock
    inputs and their evidence references, and exclude ``platform_recorded_at``.
    ``content_hash`` additionally covers ``platform_recorded_at`` for audit.
    """

    schema_version: str
    observation_reference: str
    verdict_evidence_id: UUID
    verdict_content_hash: str
    dataset_version_id: UUID
    dataset_content_hash: str
    tier: str
    event_at: datetime
    publication_at: datetime | None
    arrival_at: datetime | None
    arrival_clock_bound: Mapping[str, Any] | None
    platform_recorded_at: datetime
    market_knowledge_at: datetime | None
    claim_ceiling: ClaimCeilingV1
    knowledge_basis: str
    publication_lag_nanos: int | None
    publication_lag_reference: str | None
    reasons: tuple[str, ...]
    market_content_hash: str
    content_hash: str
    knowledge_id: UUID
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise KnowledgeTimeDoctrineError(
                "observation_knowledge_is_issued_only_by_the_doctrine"
            )

    def market_identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observation_reference": self.observation_reference,
            "verdict_evidence_id": str(self.verdict_evidence_id),
            "verdict_content_hash": self.verdict_content_hash,
            "dataset_version_id": str(self.dataset_version_id),
            "dataset_content_hash": self.dataset_content_hash,
            "tier": self.tier,
            "event_at": _iso(self.event_at),
            "publication_at": _iso(self.publication_at),
            "arrival_at": _iso(self.arrival_at),
            "arrival_clock_bound": (
                None if self.arrival_clock_bound is None else dict(self.arrival_clock_bound)
            ),
            "market_knowledge_at": _iso(self.market_knowledge_at),
            "claim_ceiling": self.claim_ceiling.name,
            "knowledge_basis": self.knowledge_basis,
            "publication_lag_nanos": self.publication_lag_nanos,
            "publication_lag_reference": self.publication_lag_reference,
            "reasons": list(self.reasons),
        }

    def audit_payload(self) -> dict[str, Any]:
        return {"platform_recorded_at": _iso(self.platform_recorded_at)}

    def integrity_verified(self) -> bool:
        market_hash, audit_hash = _hashes(self.market_identity_payload(), self.audit_payload())
        return (
            self.schema_version == KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION
            and self.market_content_hash == market_hash
            and self.content_hash == audit_hash
            and self.knowledge_id
            == uuid5(_NAMESPACE, f"observation-knowledge:{self.market_content_hash}")
        )


def _issue_observation(**values: Any) -> ObservationKnowledgeV1:
    draft = ObservationKnowledgeV1(
        **values,
        market_content_hash="",
        content_hash="",
        knowledge_id=_NAMESPACE,
        _issuer=_ISSUER,
    )
    market_hash, audit_hash = _hashes(draft.market_identity_payload(), draft.audit_payload())
    return ObservationKnowledgeV1(
        **values,
        market_content_hash=market_hash,
        content_hash=audit_hash,
        knowledge_id=uuid5(_NAMESPACE, f"observation-knowledge:{market_hash}"),
        _issuer=_ISSUER,
    )


def derive_observation_knowledge_v1(
    verdict: EvidenceTierVerdictV1,
    *,
    dataset_version_id: UUID,
    dataset_content_hash: str,
    observation_reference: str,
    event_at: datetime,
    platform_recorded_at: datetime,
    publication_at: datetime | None = None,
    arrival_at: datetime | None = None,
    arrival_clock_bound: HostClockBoundV1 | None = None,
) -> ObservationKnowledgeV1:
    """Derive one observation's market knowledge time. Pure and fail-closed.

    ``verdict`` is the evidence-tier verdict of the sealed dataset the
    observation belongs to, and must be bound to that exact dataset identity
    and content hash -- a verdict for another dataset is a refusal, so a T1
    observation cannot borrow a T4 dataset's verdict. The tier is read from
    the verdict and from nothing else.

    The tier-specific clock (``publication_at`` for T3, ``arrival_at`` plus
    ``arrival_clock_bound`` for T4) must be supplied for exactly the tier that
    uses it: a clock supplied to the wrong tier is a refusal, never ignored,
    so a caller cannot smuggle a knowledge time into T1 evidence. For T4,
    ``platform_recorded_at`` must be read on the *same host clock* as
    ``arrival_at`` (the recorder host that normalized its own capture), which
    is what makes the recorded-before-arrival check meaningful.

    Structural defects (naive datetimes, a blank reference) raise. Evidential
    gaps return an issued object with ``market_knowledge_at=None`` and a named
    reason.
    """
    if not observation_reference.strip():
        raise KnowledgeTimeDoctrineError("observation_reference_required")
    if not dataset_content_hash.strip():
        raise KnowledgeTimeDoctrineError("dataset_content_hash_required")
    _require_aware(event_at, "event_at")
    _require_aware(platform_recorded_at, "platform_recorded_at")
    if publication_at is not None:
        _require_aware(publication_at, "publication_at")
    if arrival_at is not None:
        _require_aware(arrival_at, "arrival_at")
    if arrival_clock_bound is not None:
        arrival_clock_bound.validate()

    reasons: list[str] = []
    intact = (
        verdict.integrity_verified()
        and verdict.schema_version == EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION
    )
    if not intact:
        reasons.append("knowledge_time_evidence_tier_verdict_integrity_failed")
    if verdict.dataset_version_id != dataset_version_id:
        reasons.append("knowledge_time_verdict_dataset_mismatch")
    if verdict.dataset_content_hash != dataset_content_hash.strip():
        # An intact verdict with reasons carries no content hash; that is a
        # mismatch too, because nothing then binds it to this dataset's bytes.
        reasons.append("knowledge_time_verdict_content_hash_mismatch")
    tier = verdict.tier
    claim = _claim_from_verdict(verdict) if intact else ClaimCeilingV1.NONE

    uses_publication = tier == EvidenceTierV1.T3_PUBLICATION_TIME.value
    uses_arrival = tier == EvidenceTierV1.T4_FIRST_PARTY_CAPTURE.value
    if publication_at is not None and not uses_publication:
        reasons.append("knowledge_time_publication_at_supplied_for_a_non_publication_tier")
    if (arrival_at is not None or arrival_clock_bound is not None) and not uses_arrival:
        reasons.append("knowledge_time_arrival_clock_supplied_for_a_non_capture_tier")

    market_knowledge_at: datetime | None = None
    basis = "undefined"
    lag_nanos: int | None = None
    lag_reference: str | None = None

    if tier == EvidenceTierV1.T0_SYNTHETIC.value:
        reasons.append("knowledge_time_undefined_for_synthetic_evidence")
    elif tier == EvidenceTierV1.T1_RETROSPECTIVE.value:
        reasons.append("knowledge_time_undefined_for_retrospective_evidence")
    elif tier == EvidenceTierV1.T2_EVENT_TIME.value:
        declared_lag: object = verdict.declared_publication_lag_nanos
        if claim is not ClaimCeilingV1.CONDITIONAL:
            reasons.append("knowledge_time_t2_requires_a_bound_publication_lag_verdict")
        elif not _is_integer_nanos(declared_lag) or declared_lag < 0:  # type: ignore[operator]
            reasons.append("knowledge_time_t2_publication_lag_must_be_non_negative_integer")
        else:
            lag_nanos = int(declared_lag)  # type: ignore[call-overload]
            lag_reference = verdict.publication_lag_assumption_reference
            market_knowledge_at = _plus_nanos_rounded_up(event_at, lag_nanos)
            basis = "event_at_plus_declared_publication_lag"
    elif tier == EvidenceTierV1.T3_PUBLICATION_TIME.value:
        if claim is not ClaimCeilingV1.PROFESSIONAL:
            reasons.append("knowledge_time_t3_requires_a_professional_verdict")
        elif publication_at is None:
            reasons.append("knowledge_time_t3_requires_publisher_publication_time")
        else:
            # No platform_recorded_at >= publication_at check here: those are
            # two different clocks (host vs publisher) with an unknown offset,
            # so a comparison would refuse or admit on skew, not on evidence.
            market_knowledge_at = publication_at
            basis = "publisher_publication_time"
    elif tier == EvidenceTierV1.T4_FIRST_PARTY_CAPTURE.value:
        if claim is not ClaimCeilingV1.PROFESSIONAL:
            reasons.append("knowledge_time_t4_requires_a_professional_verdict")
        elif arrival_at is None:
            reasons.append("knowledge_time_t4_requires_recorder_arrival")
        elif arrival_clock_bound is None:
            reasons.append("knowledge_time_t4_arrival_clock_offset_unbounded")
        elif platform_recorded_at < arrival_at:
            # Same host clock by contract (see the docstring), so this is a
            # real ordering violation, not skew.
            reasons.append("knowledge_time_platform_recorded_before_arrival")
        else:
            market_knowledge_at = arrival_clock_bound.venue_upper_bound(arrival_at)
            basis = "recorder_arrival_plus_venue_clock_bound"
    else:
        reasons.append("knowledge_time_unknown_evidence_tier")

    if market_knowledge_at is not None and market_knowledge_at < event_at:
        # Knowing a value before it happened is incoherent evidence, not an
        # early signal. Refuse rather than clamp.
        reasons.append("knowledge_time_market_knowledge_precedes_event")

    if reasons:
        market_knowledge_at = None
        basis = "undefined"
        lag_nanos = None
        lag_reference = None
        # Real, intact data with no defensible knowledge time is descriptive at
        # best; a broken or mismatched verdict or synthetic evidence stays NONE.
        bound_to_dataset = not any(
            reason
            in (
                "knowledge_time_verdict_dataset_mismatch",
                "knowledge_time_verdict_content_hash_mismatch",
            )
            for reason in reasons
        )
        if not bound_to_dataset:
            claim = ClaimCeilingV1.NONE
        claim = min(claim, ClaimCeilingV1.DESCRIPTIVE)

    return _issue_observation(
        schema_version=KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION,
        observation_reference=observation_reference.strip(),
        verdict_evidence_id=verdict.evidence_id,
        verdict_content_hash=verdict.content_hash,
        dataset_version_id=dataset_version_id,
        dataset_content_hash=dataset_content_hash.strip(),
        tier=tier,
        event_at=event_at,
        publication_at=publication_at,
        arrival_at=arrival_at,
        arrival_clock_bound=None if arrival_clock_bound is None else arrival_clock_bound.payload(),
        platform_recorded_at=platform_recorded_at,
        market_knowledge_at=market_knowledge_at,
        claim_ceiling=claim,
        knowledge_basis=basis,
        publication_lag_nanos=lag_nanos,
        publication_lag_reference=lag_reference,
        reasons=_ordered_reasons(reasons),
    )


# ---------------------------------------------------------------------------
# Feature knowledge (propagation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublicationLagBindingV1:
    """One T2 lag assumption a result depends on. Carried so lag sensitivity is visible."""

    verdict_evidence_id: UUID
    publication_lag_nanos: int
    publication_lag_reference: str

    def payload(self) -> dict[str, Any]:
        return {
            "verdict_evidence_id": str(self.verdict_evidence_id),
            "publication_lag_nanos": self.publication_lag_nanos,
            "publication_lag_reference": self.publication_lag_reference,
        }


def _sorted_bindings(
    bindings: Iterable[PublicationLagBindingV1],
) -> tuple[PublicationLagBindingV1, ...]:
    return tuple(
        sorted(
            set(bindings),
            key=lambda binding: (
                str(binding.verdict_evidence_id),
                binding.publication_lag_nanos,
                binding.publication_lag_reference,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class FeatureKnowledgeV1:
    """The propagated clocks of one feature value over its inputs.

    ``input_knowledge_hashes`` are the inputs' *market* hashes, so the feature's
    market identity survives re-normalization; ``platform_recorded_at`` (the
    latest input's) is audit-only.
    """

    schema_version: str
    input_knowledge_hashes: tuple[str, ...]
    event_at: datetime
    market_knowledge_at: datetime | None
    platform_recorded_at: datetime
    claim_ceiling: ClaimCeilingV1
    publication_lag_bindings: tuple[PublicationLagBindingV1, ...]
    reasons: tuple[str, ...]
    market_content_hash: str
    content_hash: str
    knowledge_id: UUID
    _issuer: object = field(default=None, repr=False, compare=False)
    #: True only for an object rebuilt from persisted data by the integrity-only
    #: restore; decision and claim functions refuse it (never compared or hashed).
    _restored: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise KnowledgeTimeDoctrineError("feature_knowledge_is_issued_only_by_the_doctrine")

    def market_identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "input_knowledge_hashes": list(self.input_knowledge_hashes),
            "event_at": _iso(self.event_at),
            "market_knowledge_at": _iso(self.market_knowledge_at),
            "claim_ceiling": self.claim_ceiling.name,
            "publication_lag_bindings": [
                binding.payload() for binding in self.publication_lag_bindings
            ],
            "reasons": list(self.reasons),
        }

    def audit_payload(self) -> dict[str, Any]:
        return {"platform_recorded_at": _iso(self.platform_recorded_at)}

    def integrity_verified(self) -> bool:
        market_hash, audit_hash = _hashes(self.market_identity_payload(), self.audit_payload())
        return (
            self.schema_version == KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION
            and self.market_content_hash == market_hash
            and self.content_hash == audit_hash
            and self.knowledge_id
            == uuid5(_NAMESPACE, f"feature-knowledge:{self.market_content_hash}")
        )


def propagate_feature_knowledge_v1(
    inputs: Sequence[ObservationKnowledgeV1], *, event_at: datetime
) -> FeatureKnowledgeV1:
    """A feature's clocks from its inputs' clocks. Pure; order-independent.

    ``market_knowledge_at`` is the maximum over the inputs, and undefined if
    any input's is. ``claim_ceiling`` is the minimum. ``event_at`` is the
    feature's own economic time (the caller's feature definition decides it);
    it may not follow the feature's market knowledge time.
    """
    _require_aware(event_at, "event_at")
    if not inputs:
        raise KnowledgeTimeDoctrineError("feature_knowledge_requires_inputs")
    for item in inputs:
        if not isinstance(item, ObservationKnowledgeV1) or not item.integrity_verified():
            raise KnowledgeTimeDoctrineError("feature_knowledge_input_integrity_failed")
    reasons: list[str] = []
    hashes = sorted({item.market_content_hash for item in inputs})
    if len(hashes) != len(inputs):
        reasons.append("feature_knowledge_duplicate_input")

    claim = min(item.claim_ceiling for item in inputs)
    market_knowledge_at: datetime | None = None
    if any(item.market_knowledge_at is None for item in inputs):
        for item in inputs:
            if item.market_knowledge_at is None:
                reasons.extend(f"input:{reason}" for reason in item.reasons)
        reasons.append("feature_knowledge_input_market_knowledge_undefined")
    else:
        market_knowledge_at = max(
            item.market_knowledge_at for item in inputs if item.market_knowledge_at is not None
        )
        if market_knowledge_at < event_at:
            reasons.append("feature_knowledge_market_knowledge_precedes_feature_event")

    bindings = _sorted_bindings(
        PublicationLagBindingV1(
            item.verdict_evidence_id, item.publication_lag_nanos, item.publication_lag_reference
        )
        for item in inputs
        if item.publication_lag_nanos is not None and item.publication_lag_reference is not None
    )
    if reasons:
        market_knowledge_at = None
        claim = min(claim, ClaimCeilingV1.DESCRIPTIVE)

    values: dict[str, Any] = {
        "schema_version": KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION,
        "input_knowledge_hashes": tuple(hashes),
        "event_at": event_at,
        "market_knowledge_at": market_knowledge_at,
        "platform_recorded_at": max(item.platform_recorded_at for item in inputs),
        "claim_ceiling": claim,
        "publication_lag_bindings": bindings,
        "reasons": _ordered_reasons(reasons),
    }
    draft = FeatureKnowledgeV1(
        **values, market_content_hash="", content_hash="", knowledge_id=_NAMESPACE, _issuer=_ISSUER
    )
    market_hash, audit_hash = _hashes(draft.market_identity_payload(), draft.audit_payload())
    return FeatureKnowledgeV1(
        **values,
        market_content_hash=market_hash,
        content_hash=audit_hash,
        knowledge_id=uuid5(_NAMESPACE, f"feature-knowledge:{market_hash}"),
        _issuer=_ISSUER,
    )


def _parse_instant(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KnowledgeTimeDoctrineError(f"persisted_{name}_must_be_iso_text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise KnowledgeTimeDoctrineError(f"persisted_{name}_malformed") from error
    _require_aware(parsed, f"persisted_{name}")
    return parsed


def persisted_observation_knowledge_v1(observation: ObservationKnowledgeV1) -> dict[str, Any]:
    """What a durable row stores about one input: its market payload and audit clock."""
    if not isinstance(observation, ObservationKnowledgeV1) or not observation.integrity_verified():
        raise KnowledgeTimeDoctrineError("persisted_observation_knowledge_integrity_failed")
    return {
        "market": observation.market_identity_payload(),
        "platform_recorded_at": _iso(observation.platform_recorded_at),
    }


def persisted_observation_market_hash_v1(persisted: Mapping[str, Any]) -> str:
    """The observation market hash a persisted input claims to be."""
    market = persisted.get("market")
    if not isinstance(market, Mapping):
        raise KnowledgeTimeDoctrineError("persisted_observation_knowledge_malformed")
    return _sha256(market)


@dataclass(frozen=True, slots=True)
class SealedObservationClocksV1:
    """One observation's clock facts as the *sealed evidence* records them.

    Phase R2A.2. Supplied by a resolver that reads the sealed dataset (never
    the feature row being verified): ``event_at`` is the instant the value was
    complete, ``platform_recorded_at`` the platform's normalize/seal instant,
    and the tier-specific clocks are whatever the sealed evidence itself
    carries. A source whose sealed evidence carries no publication or arrival
    clock (every source today; T4 sealing is R3A) yields ``None`` there, so a
    T3/T4 input then has no knowledge time -- the fail-closed answer.
    """

    event_at: datetime
    platform_recorded_at: datetime
    publication_at: datetime | None = None
    arrival_at: datetime | None = None
    arrival_clock_bound: HostClockBoundV1 | None = None


#: ``(dataset_version_id, observation_references) -> {reference: sealed clocks}``.
#: A reference missing from the result is not sealed evidence and refuses.
SealedClockResolverV1 = Callable[[UUID, Sequence[str]], Mapping[str, SealedObservationClocksV1]]


def rederive_observation_knowledge_v1(
    verdict: EvidenceTierVerdictV1,
    persisted: Mapping[str, Any],
    *,
    sealed: SealedObservationClocksV1,
) -> ObservationKnowledgeV1:
    """Re-run the doctrine for a persisted input from sealed facts and its genuine verdict.

    Phase R2A.2 provenance check. The persisted payload contributes only its
    *identity* (dataset binding and observation reference); every clock --
    event, publication, arrival, clock bound, platform time -- comes from
    ``sealed``, which the caller resolved from the sealed dataset, and the
    tier and claim come from ``verdict``. The result must reproduce the stored
    market hash exactly. So neither a hand-built payload nor a correctly
    re-derived payload built on invented clock facts (an earlier arrival, a
    zero clock bound) survives: its hash cannot match the doctrine applied to
    what the evidence actually recorded.
    """
    market = persisted.get("market")
    if not isinstance(market, Mapping):
        raise KnowledgeTimeDoctrineError("persisted_observation_knowledge_malformed")
    try:
        if UUID(str(market["verdict_evidence_id"])) != verdict.evidence_id:
            raise KnowledgeTimeDoctrineError("persisted_observation_knowledge_verdict_mismatch")
        rederived = derive_observation_knowledge_v1(
            verdict,
            dataset_version_id=UUID(str(market["dataset_version_id"])),
            dataset_content_hash=str(market["dataset_content_hash"]),
            observation_reference=str(market["observation_reference"]),
            event_at=sealed.event_at,
            platform_recorded_at=sealed.platform_recorded_at,
            publication_at=sealed.publication_at,
            arrival_at=sealed.arrival_at,
            arrival_clock_bound=sealed.arrival_clock_bound,
        )
    except KnowledgeTimeDoctrineError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise KnowledgeTimeDoctrineError("persisted_observation_knowledge_malformed") from error
    if rederived.market_content_hash != _sha256(market):
        raise KnowledgeTimeDoctrineError("persisted_observation_knowledge_not_derivable_from_evidence")
    return rederived


def restore_persisted_feature_knowledge_v1(
    market_payload: Mapping[str, Any],
    *,
    platform_recorded_at: datetime,
    expected_market_content_hash: str,
) -> FeatureKnowledgeV1:
    """Re-issue a feature's clocks from the payload a durable row stored.

    Phase R2A.2. A V3 feature materialization persists
    :meth:`FeatureKnowledgeV1.market_identity_payload` so a later reader can
    compute decision times without recomputing the inputs. This function is
    the only way back from that payload to an issued object, and it trusts
    nothing it can check: the payload must re-hash to
    ``expected_market_content_hash`` (the hash bound into the row's own content
    hash), and its clocks and claim must be *coherent* -- a defined knowledge
    time only with no reasons and at least a conditional claim, an undefined
    one only with a reason and at most a descriptive claim, knowledge never
    before the event. A payload that fails any of this is refused, never
    repaired.

    This proves *integrity and coherence only* -- never provenance. A payload
    anyone assembled by hand can pass it, so the object it returns is marked
    restored and every decision-time and claim-ceiling function refuses it.
    Anything that grants decision authority must instead re-derive every input
    from sealed evidence and its genuine verdict with
    :func:`rederive_observation_knowledge_v1` and re-propagate.
    """
    _require_aware(platform_recorded_at, "platform_recorded_at")
    if market_payload.get("schema_version") != KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION:
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_schema_version_mismatch")
    try:
        hashes = tuple(str(item) for item in market_payload["input_knowledge_hashes"])
        event_at = _parse_instant(market_payload["event_at"], "event_at")
        market_knowledge_at = _parse_instant(
            market_payload["market_knowledge_at"], "market_knowledge_at"
        )
        claim = ClaimCeilingV1[str(market_payload["claim_ceiling"])]
        bindings = tuple(
            PublicationLagBindingV1(
                UUID(str(item["verdict_evidence_id"])),
                int(item["publication_lag_nanos"]),
                str(item["publication_lag_reference"]),
            )
            for item in market_payload["publication_lag_bindings"]
        )
        reasons = tuple(str(item) for item in market_payload["reasons"])
    except (KeyError, TypeError, ValueError) as error:
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_malformed") from error
    if event_at is None or not hashes:
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_malformed")
    if hashes != tuple(sorted(set(hashes))):
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_inputs_not_canonical")
    if reasons != _ordered_reasons(reasons) or bindings != _sorted_bindings(bindings):
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_not_canonical")
    if market_knowledge_at is None:
        if not reasons or claim > ClaimCeilingV1.DESCRIPTIVE:
            raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_incoherent")
    elif reasons or claim < ClaimCeilingV1.CONDITIONAL or market_knowledge_at < event_at:
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_incoherent")
    # Only a T2 input carries a lag binding and caps the claim at CONDITIONAL,
    # so a defined CONDITIONAL value has at least one and a PROFESSIONAL none.
    if claim is ClaimCeilingV1.CONDITIONAL and market_knowledge_at is not None and not bindings:
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_incoherent")
    if claim is ClaimCeilingV1.PROFESSIONAL and bindings:
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_incoherent")
    values: dict[str, Any] = {
        "schema_version": KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION,
        "input_knowledge_hashes": hashes,
        "event_at": event_at,
        "market_knowledge_at": market_knowledge_at,
        "platform_recorded_at": platform_recorded_at,
        "claim_ceiling": claim,
        "publication_lag_bindings": bindings,
        "reasons": reasons,
    }
    draft = FeatureKnowledgeV1(
        **values, market_content_hash="", content_hash="", knowledge_id=_NAMESPACE, _issuer=_ISSUER
    )
    market_hash, audit_hash = _hashes(draft.market_identity_payload(), draft.audit_payload())
    if market_hash != expected_market_content_hash:
        raise KnowledgeTimeDoctrineError("persisted_feature_knowledge_hash_mismatch")
    return FeatureKnowledgeV1(
        **values,
        market_content_hash=market_hash,
        content_hash=audit_hash,
        knowledge_id=uuid5(_NAMESPACE, f"feature-knowledge:{market_hash}"),
        _issuer=_ISSUER,
        _restored=True,
    )


# ---------------------------------------------------------------------------
# Decision time
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeclaredComputeLatencyV1:
    """The compute latency a historical replay assumes. Declared, never defaulted.

    It is a methodology input, like a preregistered horizon: whoever declares
    it names where it came from, and it is bound into every decision time that
    uses it.
    """

    latency_nanos: int
    reference: str

    def validate(self) -> None:
        if not _is_integer_nanos(self.latency_nanos):
            raise KnowledgeTimeDoctrineError("compute_latency_must_be_integer_nanos")
        if self.latency_nanos < 0:
            raise KnowledgeTimeDoctrineError("compute_latency_must_be_non_negative")
        if not self.reference.strip():
            raise KnowledgeTimeDoctrineError("compute_latency_requires_reference")

    def payload(self) -> dict[str, Any]:
        return {"latency_nanos": self.latency_nanos, "reference": self.reference.strip()}


@dataclass(frozen=True, slots=True)
class DecisionTimeV1:
    """One decision's time and claim ceiling, or a named refusal.

    Its identity reads only the features' *market* hashes, so re-normalizing
    the same evidence later yields the identical ``decision_time_id``.
    """

    schema_version: str
    mode: str
    feature_knowledge_hashes: tuple[str, ...]
    market_knowledge_at: datetime | None
    compute_latency: Mapping[str, Any] | None
    computed_at: datetime | None
    computed_at_clock_bound: Mapping[str, Any] | None
    decision_at: datetime | None
    claim_ceiling: ClaimCeilingV1
    publication_lag_bindings: tuple[PublicationLagBindingV1, ...]
    reasons: tuple[str, ...]
    content_hash: str
    decision_time_id: UUID
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise KnowledgeTimeDoctrineError("decision_time_is_issued_only_by_the_doctrine")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "feature_knowledge_hashes": list(self.feature_knowledge_hashes),
            "market_knowledge_at": _iso(self.market_knowledge_at),
            "compute_latency": None if self.compute_latency is None else dict(self.compute_latency),
            "computed_at": _iso(self.computed_at),
            "computed_at_clock_bound": (
                None
                if self.computed_at_clock_bound is None
                else dict(self.computed_at_clock_bound)
            ),
            "decision_at": _iso(self.decision_at),
            "claim_ceiling": self.claim_ceiling.name,
            "publication_lag_bindings": [
                binding.payload() for binding in self.publication_lag_bindings
            ],
            "reasons": list(self.reasons),
        }

    def integrity_verified(self) -> bool:
        return (
            self.schema_version == KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION
            and self.content_hash == _sha256(self.identity_payload())
            and self.decision_time_id == uuid5(_NAMESPACE, f"decision-time:{self.content_hash}")
        )

    def is_admissible(self) -> bool:
        """An intact decision with a defined time and at least a conditional claim."""
        return (
            self.integrity_verified()
            and self.decision_at is not None
            and not self.reasons
            and self.claim_ceiling >= ClaimCeilingV1.CONDITIONAL
        )


def _check_features(features: Sequence[FeatureKnowledgeV1]) -> None:
    if not features:
        raise KnowledgeTimeDoctrineError("decision_time_requires_feature_knowledge")
    for item in features:
        if not isinstance(item, FeatureKnowledgeV1) or not item.integrity_verified() or item._restored:
            raise KnowledgeTimeDoctrineError("decision_time_feature_knowledge_integrity_failed")


def _issue_decision(
    *,
    mode: DecisionModeV1,
    features: Sequence[FeatureKnowledgeV1],
    compute_latency: DeclaredComputeLatencyV1 | None,
    computed_at: datetime | None,
    computed_at_clock_bound: HostClockBoundV1 | None,
) -> DecisionTimeV1:
    reasons: list[str] = []
    hashes = sorted(item.market_content_hash for item in features)
    if len(set(hashes)) != len(hashes):
        reasons.append("decision_duplicate_feature_input")
    claim = min(item.claim_ceiling for item in features)
    market_knowledge_at: datetime | None = None
    if any(item.market_knowledge_at is None for item in features):
        for item in features:
            if item.market_knowledge_at is None:
                reasons.extend(item.reasons)
        # A principled refusal, not a collapse onto some operational instant.
        reasons.append("decision_input_has_no_market_knowledge_time")
    else:
        market_knowledge_at = max(
            item.market_knowledge_at for item in features if item.market_knowledge_at is not None
        )
    decision_at: datetime | None = None
    if market_knowledge_at is not None:
        if mode is DecisionModeV1.HISTORICAL_REPLAY and compute_latency is not None:
            decision_at = _plus_nanos_rounded_up(market_knowledge_at, compute_latency.latency_nanos)
        elif (
            mode is DecisionModeV1.LIVE
            and computed_at is not None
            and computed_at_clock_bound is not None
        ):
            computed_venue = computed_at_clock_bound.venue_upper_bound(computed_at)
            # The venue instant the computation finished is somewhere at or
            # before computed_venue. If even that upper bound precedes the
            # inputs' knowledge time, the computation cannot have used them.
            if computed_venue < market_knowledge_at:
                reasons.append("live_computed_before_market_knowledge")
            decision_at = max(market_knowledge_at, computed_venue)
        else:
            raise KnowledgeTimeDoctrineError("decision_time_mode_inputs_incoherent")
    if claim < ClaimCeilingV1.CONDITIONAL:
        reasons.append(f"decision_claim_ceiling_is_{claim.name.lower()}")
    if reasons:
        decision_at = None

    values: dict[str, Any] = {
        "schema_version": KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION,
        "mode": mode.value,
        "feature_knowledge_hashes": tuple(hashes),
        "market_knowledge_at": market_knowledge_at,
        "compute_latency": None if compute_latency is None else compute_latency.payload(),
        "computed_at": computed_at,
        "computed_at_clock_bound": (
            None if computed_at_clock_bound is None else computed_at_clock_bound.payload()
        ),
        "decision_at": decision_at,
        "claim_ceiling": claim,
        "publication_lag_bindings": _sorted_bindings(
            binding for item in features for binding in item.publication_lag_bindings
        ),
        "reasons": _ordered_reasons(reasons),
    }
    draft = DecisionTimeV1(**values, content_hash="", decision_time_id=_NAMESPACE, _issuer=_ISSUER)
    content_hash = _sha256(draft.identity_payload())
    return DecisionTimeV1(
        **values,
        content_hash=content_hash,
        decision_time_id=uuid5(_NAMESPACE, f"decision-time:{content_hash}"),
        _issuer=_ISSUER,
    )


def historical_decision_time_v1(
    features: Sequence[FeatureKnowledgeV1], *, compute_latency: DeclaredComputeLatencyV1
) -> DecisionTimeV1:
    """Historical replay: ``max(market_knowledge_at) + declared compute latency``.

    Reads no ``computed_at`` and no ``platform_recorded_at``: recomputing or
    re-normalizing the same features at any later wall time yields the
    identical decision time *and* the identical ``decision_time_id``.
    """
    _check_features(features)
    compute_latency.validate()
    return _issue_decision(
        mode=DecisionModeV1.HISTORICAL_REPLAY,
        features=features,
        compute_latency=compute_latency,
        computed_at=None,
        computed_at_clock_bound=None,
    )


def live_decision_time_v1(
    features: Sequence[FeatureKnowledgeV1],
    *,
    computed_at: datetime,
    computed_at_clock_bound: HostClockBoundV1,
) -> DecisionTimeV1:
    """Live: ``max(market_knowledge_at, computed_at on the venue timescale)``.

    ``computed_at`` is a host-clock reading; ``computed_at_clock_bound`` is the
    measured bound that converts it to the venue timescale, required for the
    same reason a T4 arrival needs one. Never used for historical replay.
    """
    _check_features(features)
    _require_aware(computed_at, "computed_at")
    computed_at_clock_bound.validate()
    return _issue_decision(
        mode=DecisionModeV1.LIVE,
        features=features,
        compute_latency=None,
        computed_at=computed_at,
        computed_at_clock_bound=computed_at_clock_bound,
    )


# ---------------------------------------------------------------------------
# Result claim ceiling
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResultClaimCeilingV1:
    """The strongest claim one research result may make, and the rule that set it."""

    schema_version: str
    rule: str
    input_claims: tuple[tuple[str, str, str], ...]
    claim_ceiling: ClaimCeilingV1
    publication_lag_bindings: tuple[PublicationLagBindingV1, ...]
    content_hash: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise KnowledgeTimeDoctrineError("result_claim_ceiling_is_issued_only_by_the_doctrine")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "rule": self.rule,
            "input_claims": [list(item) for item in self.input_claims],
            "claim_ceiling": self.claim_ceiling.name,
            "publication_lag_bindings": [
                binding.payload() for binding in self.publication_lag_bindings
            ],
        }

    def integrity_verified(self) -> bool:
        return (
            self.schema_version == KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION
            and self.content_hash == _sha256(self.identity_payload())
        )


#: The only rule implemented until the owner approves OR-4: every input counts.
STRICT_ALL_INPUTS_RULE_V1: Final = "strict_all_inputs_min_claim_v1"


def result_claim_ceiling_v1(
    *,
    decision_inputs: Sequence[FeatureKnowledgeV1],
    execution_marking_inputs: Sequence[FeatureKnowledgeV1] = (),
) -> ResultClaimCeilingV1:
    """The minimum claim over *every* input -- the stricter rule pending OR-4.

    Execution/marking inputs are accepted and recorded by role so the
    distinction survives into the result's identity, but under this rule they
    bind the ceiling exactly as decision inputs do. The same feature listed
    twice (in one role or across both) is a caller defect and raises.
    """
    if not decision_inputs:
        raise KnowledgeTimeDoctrineError("result_claim_ceiling_requires_decision_inputs")
    tagged = [(InputRoleV1.DECISION, item) for item in decision_inputs] + [
        (InputRoleV1.EXECUTION_MARKING, item) for item in execution_marking_inputs
    ]
    for _, item in tagged:
        if not isinstance(item, FeatureKnowledgeV1) or not item.integrity_verified() or item._restored:
            raise KnowledgeTimeDoctrineError("result_claim_ceiling_input_integrity_failed")
    if len({item.market_content_hash for _, item in tagged}) != len(tagged):
        raise KnowledgeTimeDoctrineError("result_claim_ceiling_duplicate_input")
    input_claims = tuple(
        sorted(
            (role.value, item.market_content_hash, item.claim_ceiling.name) for role, item in tagged
        )
    )
    values: dict[str, Any] = {
        "schema_version": KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION,
        "rule": STRICT_ALL_INPUTS_RULE_V1,
        "input_claims": input_claims,
        "claim_ceiling": min(item.claim_ceiling for _, item in tagged),
        "publication_lag_bindings": _sorted_bindings(
            binding for _, item in tagged for binding in item.publication_lag_bindings
        ),
    }
    draft = ResultClaimCeilingV1(**values, content_hash="", _issuer=_ISSUER)
    return ResultClaimCeilingV1(
        **values, content_hash=_sha256(draft.identity_payload()), _issuer=_ISSUER
    )


def require_admissible_decision_v1(
    decision: DecisionTimeV1, *, minimum_claim: ClaimCeilingV1
) -> datetime:
    """Return the decision time, or raise with every reason it is inadmissible."""
    if minimum_claim < ClaimCeilingV1.CONDITIONAL:
        raise KnowledgeTimeDoctrineError("admissible_decision_requires_at_least_conditional_claim")
    if not decision.is_admissible() or decision.decision_at is None:
        raise KnowledgeTimeDoctrineError(
            "decision_not_admissible:" + ",".join(decision.reasons or ("integrity",))
        )
    if decision.claim_ceiling < minimum_claim:
        raise KnowledgeTimeDoctrineError(
            f"decision_claim_ceiling_below_required:{decision.claim_ceiling.name}"
        )
    return decision.decision_at


__all__ = [
    "KNOWLEDGE_TIME_DOCTRINE_SCHEMA_VERSION",
    "STRICT_ALL_INPUTS_RULE_V1",
    "ArrivalClockBoundV1",
    "ClaimCeilingV1",
    "ClockV1",
    "DecisionModeV1",
    "DecisionTimeV1",
    "DeclaredComputeLatencyV1",
    "FeatureKnowledgeV1",
    "HostClockBoundV1",
    "InputRoleV1",
    "KnowledgeTimeDoctrineError",
    "ObservationKnowledgeV1",
    "PublicationLagBindingV1",
    "ResultClaimCeilingV1",
    "SealedClockResolverV1",
    "SealedObservationClocksV1",
    "derive_observation_knowledge_v1",
    "historical_decision_time_v1",
    "live_decision_time_v1",
    "persisted_observation_knowledge_v1",
    "persisted_observation_market_hash_v1",
    "propagate_feature_knowledge_v1",
    "rederive_observation_knowledge_v1",
    "require_admissible_decision_v1",
    "restore_persisted_feature_knowledge_v1",
    "result_claim_ceiling_v1",
]
