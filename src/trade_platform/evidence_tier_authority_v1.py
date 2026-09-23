"""Phase 3Z.1 -- the evidence-tier authority.

``RESEARCH_ONLY``. No table, no migration, no engine, no acquisition, no
economic assumption, no execution path. This module answers exactly one
question about one dataset:

    *What historical timing claim can this exact evidence support?*

That is a different question from the one
:mod:`trade_platform.real_market_data_provenance_v1` answers (*is this
positively proven real source data with intact lineage?*), and the two are
deliberately kept orthogonal rather than collapsed into one status enum. A
dataset may be real-provenance and still only ``T1_RETROSPECTIVE``; that is the
normal case today, not a defect. Professional eligibility needs the right
*combination*, never one field.

The five tiers
--------------
``T0_SYNTHETIC``
    Fixtures and engineering evidence. Mechanics, unit and integration testing,
    deterministic pipeline validation. No economic claim of any kind.
``T1_RETROSPECTIVE``
    Real historical values with no defensible historical knowledge or
    dissemination time. Descriptive statistics, data-quality analysis,
    cross-source *value* checks, liquidity/cost-envelope research, hypothesis
    generation and engine correctness. Never a professional performance
    verdict, never a claim that a strategy historically "worked", never an
    authoritative parameter ranking.
``T2_EVENT_TIME``
    Per-observation venue/source *event* timestamps, but no proven subscriber
    knowledge time. Conditional research only, and only when a publication-lag
    assumption is explicitly declared and bound into this verdict's identity.
``T3_PUBLICATION_TIME``
    The publisher itself provides a defensible publication/knowledge time.
``T4_FIRST_PARTY_CAPTURE``
    This platform recorded an official public stream itself and retained the
    measured recorder arrival time with provenance.

Eligibility is not compatibility
--------------------------------
T3/T4 mean the evidence *quality* may carry a professional claim. They never
mean the evidence is usable by a particular strategy or validation engine. A
strategy may use professional-grade evidence only when the evidence is relevant
to that exact strategy, the strategy's own research-input contract accepts it,
and that strategy's methodology/preregistration requirements are satisfied.
This module therefore refuses to know anything about any strategy:
:func:`require_professional_evidence_tier_v1` binds a verdict to *one dataset
identity and content hash* and stops there. The strategy-specific gate lives
with the strategy -- for the crypto open-to-open path, in
:func:`trade_platform.open_to_open_preregistration_v1.require_authorized_for_holdout_with_evidence_tier_v1`.
There is deliberately no generic "this is T3, run anything" shortcut.

The tier comes from a closed contract set, never from a name
------------------------------------------------------------
:func:`authorized_timing_contracts_v1` is a closed, explicit set matched by
deterministic persisted ``source_id``. It is not a provider allowlist, and this
module never reads a provider string at all -- :class:`EvidenceTimingFactsV1`
has no provider field, so ``provider = "sec"`` cannot grant
``T3_PUBLICATION_TIME`` and ``provider = "bybit"`` cannot grant anything. The
tier is taken from the *contract*; a caller's declared
:class:`TimingAuthorityV1` is only checked for *agreement*, and a disagreement
is a refusal reason, never a promotion. An unregistered source id grants no
timing authority at all. Adding a contract is an owner decision that ships as
code, never as data.

The dormant Tardis captured-source contract is deliberately **absent** from the
set. No dataset exists under it and none is planned -- the standing owner
decision is that no paid market-data subscription will be purchased -- and its
third-party recorder clock is neither a publisher's publication time (T3) nor
this platform's own capture (T4). Registering it would have required inventing
a sixth tier or misclassifying it; doing neither leaves
:mod:`trade_platform.canonical_captured_source_authority_v1` byte-for-byte
unchanged and dormant.

Fail closed
-----------
The verdict is derived, content-hashed and issuable only here, so an edited
copy fails its own integrity check and no caller-set flag exists to raise a
tier. Every gap is an explicit reason. Absence of evidence is never evidence:
an unregistered source, a missing knowledge-time column, a collapsed decision
time, or a T2 dataset with no declared lag assumption all lose eligibility
rather than defaulting to it.

This phase chooses no publication-lag value. That is a methodology and economic
decision that must reach the owner with options and evidence; here a T2 dataset
without a declared lag is simply not conditional-research eligible.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .first_party_capture_authority_v1 import (
    FIRST_PARTY_TIMING_AUTHORIZATION_REFERENCE_V1,
    first_party_bybit_source_id_v1,
)
from .real_market_data_provenance_v1 import (
    STATUS_SYNTHETIC,
    RealMarketDataProvenanceV1,
    canonical_bybit_source_contract_v1,
)

EVIDENCE_TIMING_CONTRACT_SCHEMA_VERSION: Final = "evidence-timing-contract-v1"
EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION: Final = "evidence-tier-authority-v1"

#: The same minimum the open-to-open preregistration uses. One shared knowledge
#: instant across a whole dataset collapses every decision time onto it, which
#: makes any run vacuous. This is a mechanical precondition, not an economic
#: assumption, and it is re-checked here rather than imported so this authority
#: stays independent of any one strategy's packet.
_MINIMUM_DISTINCT_KNOWLEDGE_TIMES: Final = 2

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.evidence_tier_authority_v1")

#: Only this module may issue a timing contract or an evidence-tier verdict.
_ISSUER: Final = object()


class EvidenceTierAuthorityError(ValueError):
    """Raised when a contract or verdict is fabricated outside this authority."""


class EvidenceTierV1(StrEnum):
    """What historical timing claim one body of evidence can support."""

    T0_SYNTHETIC = "T0_SYNTHETIC"
    T1_RETROSPECTIVE = "T1_RETROSPECTIVE"
    T2_EVENT_TIME = "T2_EVENT_TIME"
    T3_PUBLICATION_TIME = "T3_PUBLICATION_TIME"
    T4_FIRST_PARTY_CAPTURE = "T4_FIRST_PARTY_CAPTURE"


class TimingAuthorityV1(StrEnum):
    """Which measured or published clock, if any, the source itself carries.

    ``NONE`` is a positive statement: the source publishes no availability or
    finality record, so any per-observation availability the platform holds is
    a computed assumption.
    """

    NONE = "NONE"
    VENUE_EVENT_TIMESTAMP = "VENUE_EVENT_TIMESTAMP"
    PUBLISHER_PUBLICATION_TIMESTAMP = "PUBLISHER_PUBLICATION_TIMESTAMP"
    PLATFORM_RECORDER_ARRIVAL_TIMESTAMP = "PLATFORM_RECORDER_ARRIVAL_TIMESTAMP"


#: The one tier each timing authority can support, and no other. A dataset never
#: rises above its source's timing authority, whatever it declares about itself.
_TIER_BY_TIMING_AUTHORITY: Final[Mapping[TimingAuthorityV1, EvidenceTierV1]] = {
    TimingAuthorityV1.NONE: EvidenceTierV1.T1_RETROSPECTIVE,
    TimingAuthorityV1.VENUE_EVENT_TIMESTAMP: EvidenceTierV1.T2_EVENT_TIME,
    TimingAuthorityV1.PUBLISHER_PUBLICATION_TIMESTAMP: EvidenceTierV1.T3_PUBLICATION_TIME,
    TimingAuthorityV1.PLATFORM_RECORDER_ARRIVAL_TIMESTAMP: EvidenceTierV1.T4_FIRST_PARTY_CAPTURE,
}

#: Tiers whose evidence *quality* may carry a professional claim. Membership
#: here is necessary and never sufficient -- see the module docstring.
PROFESSIONAL_EVIDENCE_TIERS_V1: Final = (
    EvidenceTierV1.T3_PUBLICATION_TIME,
    EvidenceTierV1.T4_FIRST_PARTY_CAPTURE,
)

#: The tiers that require a per-observation knowledge time to mean anything.
_KNOWLEDGE_TIME_TIERS_V1: Final = PROFESSIONAL_EVIDENCE_TIERS_V1

BYBIT_REST_TIER_CEILING_REASON_V1: Final = (
    "bybit_v5_rest_effective_at_is_a_computed_bar_close_assumption_not_a_published_availability"
)

BYBIT_REST_TIMING_AUTHORIZATION_REFERENCE_V1: Final = (
    "Phase 3Z.1 evidence-tier doctrine: the public Bybit V5 REST historical "
    "endpoints publish no finality, availability or revision record, so the "
    "platform's effective_at for this source is a computed assumption. Real "
    "values, no defensible historical knowledge time: T1_RETROSPECTIVE."
)


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceTimingContractV1:
    """The timing authority one exact persisted source is granted.

    Immutable and issuable only by this module, so no fixture can mint a timing
    authority by filling in the same field names.
    """

    schema_version: str
    source_id: UUID
    timing_authority: str
    granted_tier: str
    requires_declared_publication_lag: bool
    tier_ceiling_reason: str | None
    authorization_reference: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise EvidenceTierAuthorityError(
                "evidence_timing_contract_is_issued_only_by_its_authority"
            )

    def contract_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_id": str(self.source_id),
            "timing_authority": self.timing_authority,
            "granted_tier": self.granted_tier,
            "requires_declared_publication_lag": self.requires_declared_publication_lag,
            "tier_ceiling_reason": self.tier_ceiling_reason,
            "authorization_reference": self.authorization_reference,
        }

    def content_hash(self) -> str:
        return _sha256(self.contract_payload())


def _issue_contract(
    *,
    source_id: UUID,
    timing_authority: TimingAuthorityV1,
    tier_ceiling_reason: str | None,
    authorization_reference: str,
) -> EvidenceTimingContractV1:
    granted = _TIER_BY_TIMING_AUTHORITY[timing_authority]
    return EvidenceTimingContractV1(
        schema_version=EVIDENCE_TIMING_CONTRACT_SCHEMA_VERSION,
        source_id=source_id,
        timing_authority=timing_authority.value,
        granted_tier=granted.value,
        requires_declared_publication_lag=granted is EvidenceTierV1.T2_EVENT_TIME,
        tier_ceiling_reason=tier_ceiling_reason,
        authorization_reference=authorization_reference,
        _issuer=_ISSUER,
    )


def canonical_bybit_rest_timing_contract_v1() -> EvidenceTimingContractV1:
    """The Bybit V5 REST source's timing authority: ``NONE`` -> ``T1_RETROSPECTIVE``.

    The source id is read from the existing provenance authority rather than
    restated, so this contract can never drift from the canonical REST identity
    and nothing here touches that identity or its content hash.
    """
    return _issue_contract(
        source_id=canonical_bybit_source_contract_v1().source_id,
        timing_authority=TimingAuthorityV1.NONE,
        tier_ceiling_reason=BYBIT_REST_TIER_CEILING_REASON_V1,
        authorization_reference=BYBIT_REST_TIMING_AUTHORIZATION_REFERENCE_V1,
    )


def first_party_bybit_capture_timing_contract_v1() -> EvidenceTimingContractV1:
    """The Phase 3Z.2 first-party recorder: recorder arrival -> ``T4_FIRST_PARTY_CAPTURE``.

    The source id is derived from the capture contract's own identity payload,
    so nothing can enrol by asserting a provider name or a ``first_party`` flag.

    Registration grants no verdict. :func:`evaluate_evidence_tier_v1` still
    requires a proven :class:`RealMarketDataProvenanceV1` over a *sealed*
    dataset, and Phase 3Z.2 deliberately creates none -- normalization and
    sealing of captured evidence is Phase 3Z.3. Until then a first-party
    evaluation fails closed on
    ``evidence_tier_real_market_data_provenance_not_proven``, which is the
    correct answer rather than a defect.
    """
    return _issue_contract(
        source_id=first_party_bybit_source_id_v1(),
        timing_authority=TimingAuthorityV1.PLATFORM_RECORDER_ARRIVAL_TIMESTAMP,
        tier_ceiling_reason=None,
        authorization_reference=FIRST_PARTY_TIMING_AUTHORIZATION_REFERENCE_V1,
    )


def authorized_timing_contracts_v1() -> tuple[EvidenceTimingContractV1, ...]:
    """The closed, explicit set of sources granted any timing authority.

    Two entries. Adding one is an owner decision that ships as code: a persisted
    row can never enrol itself, whatever its ``provider`` text says.
    """
    return (
        canonical_bybit_rest_timing_contract_v1(),
        first_party_bybit_capture_timing_contract_v1(),
    )


def _resolve_timing_contract(source_id: UUID | None) -> EvidenceTimingContractV1 | None:
    if source_id is None:
        return None
    for contract in authorized_timing_contracts_v1():
        if source_id == contract.source_id:
            return contract
    return None


@dataclass(frozen=True, slots=True)
class EvidenceTimingFactsV1:
    """What one dataset declares about its own timing -- facts, never authority.

    There is deliberately no provider field: a name cannot reach the tier
    decision even by accident. ``declared_timing_authority`` is the caller's
    claim and is only ever checked for agreement with the registered contract.
    """

    dataset_version_id: UUID
    dataset_content_hash: str
    source_id: UUID | None
    declared_timing_authority: str
    #: Observations carrying a usable per-observation knowledge time.
    observations_with_knowledge_time: int
    #: Observations that should carry one and do not. Any is fail-closed.
    observations_missing_knowledge_time: int
    #: Distinct knowledge instants across the dataset. One instant collapses
    #: every decision time onto it and makes a run vacuous.
    distinct_knowledge_time_count: int
    #: T2 only, and never chosen by this module. Both must be present together.
    declared_publication_lag_nanos: int | None = None
    publication_lag_assumption_reference: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceTierVerdictV1:
    """Derived, content-hashed evidence-tier verdict. Issued here only."""

    schema_version: str
    tier: str
    reasons: tuple[str, ...]
    dataset_version_id: UUID
    dataset_content_hash: str | None
    source_id: UUID | None
    timing_authority: str
    timing_contract_content_hash: str | None
    provenance_evidence_id: UUID | None
    provenance_proven_real: bool
    professional_evidence_eligible: bool
    conditional_research_eligible: bool
    descriptive_research_eligible: bool
    declared_publication_lag_nanos: int | None
    publication_lag_assumption_reference: str | None
    content_hash: str
    evidence_id: UUID
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise EvidenceTierAuthorityError(
                "evidence_tier_verdict_is_issued_only_by_its_authority"
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tier": self.tier,
            "reasons": list(self.reasons),
            "dataset_version_id": str(self.dataset_version_id),
            "dataset_content_hash": self.dataset_content_hash,
            "source_id": None if self.source_id is None else str(self.source_id),
            "timing_authority": self.timing_authority,
            "timing_contract_content_hash": self.timing_contract_content_hash,
            "provenance_evidence_id": (
                None if self.provenance_evidence_id is None else str(self.provenance_evidence_id)
            ),
            "provenance_proven_real": self.provenance_proven_real,
            "professional_evidence_eligible": self.professional_evidence_eligible,
            "conditional_research_eligible": self.conditional_research_eligible,
            "descriptive_research_eligible": self.descriptive_research_eligible,
            "declared_publication_lag_nanos": self.declared_publication_lag_nanos,
            "publication_lag_assumption_reference": self.publication_lag_assumption_reference,
        }

    def integrity_verified(self) -> bool:
        return (
            self.content_hash == _sha256(self.identity_payload())
            and self.evidence_id == uuid5(_NAMESPACE, f"evidence-tier:{self.content_hash}")
        )

    def is_professional_evidence(self) -> bool:
        """True only for an intact verdict positively eligible as professional evidence.

        Eligibility of the *evidence*. It says nothing about whether any given
        strategy or engine may consume it.
        """
        return (
            self.integrity_verified()
            and self.schema_version == EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION
            and self.professional_evidence_eligible
            and not self.reasons
            and self.tier in {tier.value for tier in PROFESSIONAL_EVIDENCE_TIERS_V1}
            and self.provenance_proven_real
            and self.dataset_content_hash is not None
            and self.timing_contract_content_hash is not None
        )

    def is_conditional_research_evidence(self) -> bool:
        """True only for intact T2 evidence carrying a declared, bound lag assumption."""
        return (
            self.integrity_verified()
            and self.schema_version == EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION
            and self.conditional_research_eligible
            and not self.professional_evidence_eligible
            and self.tier == EvidenceTierV1.T2_EVENT_TIME.value
            and self.declared_publication_lag_nanos is not None
            and bool(self.publication_lag_assumption_reference)
        )


def _issue_verdict(**values: Any) -> EvidenceTierVerdictV1:
    draft = EvidenceTierVerdictV1(
        **values, content_hash="", evidence_id=_NAMESPACE, _issuer=_ISSUER
    )
    content_hash = _sha256(draft.identity_payload())
    return EvidenceTierVerdictV1(
        **values,
        content_hash=content_hash,
        evidence_id=uuid5(_NAMESPACE, f"evidence-tier:{content_hash}"),
        _issuer=_ISSUER,
    )


def evaluate_evidence_tier_v1(
    facts: EvidenceTimingFactsV1,
    provenance: RealMarketDataProvenanceV1 | None = None,
) -> EvidenceTierVerdictV1:
    """Pure, fail-closed evidence-tier verdict. See the module docstring.

    ``provenance`` is the orthogonal real-data verdict for the *same* dataset.
    It is required for any tier above ``T0_SYNTHETIC``: timing evidence over
    data whose lineage is unproven proves nothing.
    """
    reasons: list[str] = []

    proven_real = False
    positively_synthetic = False
    provenance_evidence_id: UUID | None = None
    if provenance is None:
        reasons.append("evidence_tier_requires_a_real_market_data_provenance_verdict")
    elif not provenance.integrity_verified():
        reasons.append("evidence_tier_provenance_integrity_failed")
    elif provenance.dataset_version_id != facts.dataset_version_id:
        reasons.append("evidence_tier_provenance_dataset_mismatch")
    else:
        provenance_evidence_id = provenance.evidence_id
        proven_real = provenance.is_proven_real()
        positively_synthetic = provenance.status == STATUS_SYNTHETIC
        if not proven_real and not positively_synthetic:
            reasons.append("evidence_tier_real_market_data_provenance_not_proven")
        if proven_real and provenance.source_id != facts.source_id:
            reasons.append("evidence_tier_provenance_source_mismatch")
        if (
            proven_real
            and provenance.dataset_content_hash != facts.dataset_content_hash.strip()
        ):
            reasons.append("evidence_tier_provenance_content_hash_mismatch")

    contract = _resolve_timing_contract(facts.source_id)

    # Positively synthetic evidence is T0 and stops there: no timing authority,
    # however genuine, turns a fixture into an economic observation.
    if positively_synthetic:
        return _issue_verdict(
            schema_version=EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION,
            tier=EvidenceTierV1.T0_SYNTHETIC.value,
            reasons=tuple(reasons),
            dataset_version_id=facts.dataset_version_id,
            dataset_content_hash=None,
            source_id=facts.source_id,
            timing_authority=TimingAuthorityV1.NONE.value,
            timing_contract_content_hash=None,
            provenance_evidence_id=provenance_evidence_id,
            provenance_proven_real=False,
            professional_evidence_eligible=False,
            conditional_research_eligible=False,
            descriptive_research_eligible=False,
            declared_publication_lag_nanos=None,
            publication_lag_assumption_reference=None,
        )

    if contract is None:
        # An unregistered source is granted no timing authority at all. It is
        # not synthetic either, so it is not T0: it is real-shaped evidence
        # whose timing nobody has authorized, which is exactly T1 at best.
        reasons.append("evidence_tier_source_has_no_authorized_timing_contract")
        tier = EvidenceTierV1.T1_RETROSPECTIVE
        timing_authority = TimingAuthorityV1.NONE
    else:
        tier = EvidenceTierV1(contract.granted_tier)
        timing_authority = TimingAuthorityV1(contract.timing_authority)
        if facts.declared_timing_authority != contract.timing_authority:
            # Never a promotion: the contract decides, the declaration is only
            # checked for agreement, and a disagreement is a refusal.
            reasons.append("evidence_tier_declared_timing_authority_not_granted_by_source_contract")

    if tier in _KNOWLEDGE_TIME_TIERS_V1:
        if facts.observations_missing_knowledge_time > 0:
            reasons.append("evidence_tier_observations_missing_knowledge_time")
        if facts.observations_with_knowledge_time < 1:
            reasons.append("evidence_tier_no_observation_carries_a_knowledge_time")
        if facts.distinct_knowledge_time_count < _MINIMUM_DISTINCT_KNOWLEDGE_TIMES:
            reasons.append("evidence_tier_knowledge_times_collapse_to_one_instant")

    lag_declared = (
        facts.declared_publication_lag_nanos is not None
        and facts.publication_lag_assumption_reference is not None
        and bool(facts.publication_lag_assumption_reference.strip())
    )
    if tier is EvidenceTierV1.T2_EVENT_TIME and not lag_declared:
        # This phase deliberately chooses no lag value; an undeclared one simply
        # loses conditional eligibility rather than defaulting to zero.
        reasons.append("evidence_tier_t2_requires_a_declared_bound_publication_lag_assumption")
    if tier is not EvidenceTierV1.T2_EVENT_TIME and lag_declared:
        reasons.append("evidence_tier_publication_lag_assumption_only_applies_to_event_time")

    intact = not reasons
    professional = intact and proven_real and tier in PROFESSIONAL_EVIDENCE_TIERS_V1
    conditional = intact and proven_real and tier is EvidenceTierV1.T2_EVENT_TIME
    descriptive = proven_real and not reasons

    return _issue_verdict(
        schema_version=EVIDENCE_TIER_AUTHORITY_SCHEMA_VERSION,
        tier=tier.value,
        reasons=tuple(reasons),
        dataset_version_id=facts.dataset_version_id,
        dataset_content_hash=facts.dataset_content_hash.strip() if intact else None,
        source_id=facts.source_id,
        timing_authority=timing_authority.value,
        timing_contract_content_hash=(
            contract.content_hash() if contract is not None and intact else None
        ),
        provenance_evidence_id=provenance_evidence_id,
        provenance_proven_real=proven_real,
        professional_evidence_eligible=professional,
        conditional_research_eligible=conditional,
        descriptive_research_eligible=descriptive,
        declared_publication_lag_nanos=(
            facts.declared_publication_lag_nanos if conditional else None
        ),
        publication_lag_assumption_reference=(
            facts.publication_lag_assumption_reference.strip()
            if conditional and facts.publication_lag_assumption_reference is not None
            else None
        ),
    )


def require_professional_evidence_tier_v1(
    verdict: EvidenceTierVerdictV1,
    *,
    dataset_version_id: UUID,
    dataset_content_hash: str,
) -> None:
    """Gate professional use of one dataset's evidence. Raises unless eligible.

    Deliberately narrow. It proves the *evidence quality* and that the verdict
    is bound to this exact dataset identity and content hash. It does not and
    must not decide that any particular strategy or engine may consume it --
    that stays with the strategy's own contract and preregistration.
    """
    if not verdict.is_professional_evidence():
        raise EvidenceTierAuthorityError(
            "professional_use_requires_professional_evidence_tier:"
            + ",".join(verdict.reasons or (verdict.tier,))
        )
    if verdict.dataset_version_id != dataset_version_id:
        raise EvidenceTierAuthorityError("evidence_tier_verdict_dataset_mismatch")
    if verdict.dataset_content_hash != dataset_content_hash.strip():
        raise EvidenceTierAuthorityError("evidence_tier_verdict_content_hash_mismatch")


def require_conditional_research_tier_v1(
    verdict: EvidenceTierVerdictV1,
    *,
    dataset_version_id: UUID,
    dataset_content_hash: str,
) -> None:
    """Gate the T2 conditional-research lane. Raises unless conditionally eligible.

    Passing this is never a professional result. A caller that clears it must
    label its output conditional and carry
    :attr:`EvidenceTierVerdictV1.declared_publication_lag_nanos` and its
    assumption reference with the result, because both are bound into the
    verdict's content hash and are what make the number interpretable.
    """
    if not verdict.is_conditional_research_evidence():
        raise EvidenceTierAuthorityError(
            "conditional_research_requires_event_time_evidence_with_a_declared_lag:"
            + ",".join(verdict.reasons or (verdict.tier,))
        )
    if verdict.dataset_version_id != dataset_version_id:
        raise EvidenceTierAuthorityError("evidence_tier_verdict_dataset_mismatch")
    if verdict.dataset_content_hash != dataset_content_hash.strip():
        raise EvidenceTierAuthorityError("evidence_tier_verdict_content_hash_mismatch")
