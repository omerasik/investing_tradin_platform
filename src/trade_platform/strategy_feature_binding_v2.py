"""Module 3J.2a -- Subject-Aware Strategy Lab Feature Binding V2.

The research boundary between the generalized ``FeatureMaterializationV2``
Feature Authority (Module 3J.0/3J.1) and the existing Strategy Lab research
stack. This module is deliberately small: a deterministic, immutable
research-input abstraction over :class:`~trade_platform.feature_authority.
PostgresFeatureAuthority`, nothing more.

It is not a new Feature Authority, a new strategy engine, a signal engine, an
order/execution layer, or a durable duplicate feature store. It writes
nothing; every type here is read-only over the existing authority's
``definition()`` / ``latest_as_of_subject()`` boundary.

Unlike ``trend_strategy_v2``/``trend_research_v2`` (unchanged, instrument-
centric, ``FeatureMaterialization`` V1), everything in this module is
canonical-subject-aware (``subject_type``, ``subject_id``) and speaks only
``FeatureMaterializationV2``. The two families are never coerced into each
other.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .feature_authority import (
    FeatureDefinitionVersion,
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
)


class StrategyFeatureBindingV2Error(ValueError):
    pass


class ResearchQualityPolicyV2(StrEnum):
    """How a materialization's quality status gates its admission into a bundle.

    ``VALIDATED_ONLY`` is the only policy 3J.2a v1 supports: a ``DEGRADED`` or
    ``REJECTED`` row is silently excluded rather than admitted with a null
    value -- there is no forward-fill, interpolation or substitution anywhere
    in this module.
    """

    VALIDATED_ONLY = "VALIDATED_ONLY"


def _quality_admitted(status: FeatureQualityStatus, policy: ResearchQualityPolicyV2) -> bool:
    if policy is ResearchQualityPolicyV2.VALIDATED_ONLY:
        return status is FeatureQualityStatus.VALIDATED
    raise StrategyFeatureBindingV2Error("unsupported_research_quality_policy")


class FeatureAuthorityReaderV2(Protocol):
    """The only read boundary this module is permitted to use.

    ``PostgresFeatureAuthority`` already satisfies this structurally; no
    adapter or subclass is required. There is deliberately no fallback method
    here from ``FUTURES_SERIES`` to the legacy ``latest_as_of`` -- a subject
    that is not resolvable through this exact boundary is unavailable, never
    approximated through the V1 path.
    """

    def definition(self, feature_id: UUID) -> FeatureDefinitionVersion: ...

    def latest_as_of_subject(
        self,
        feature_id: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        dataset_version: str,
        decision_at: datetime,
    ) -> tuple[FeatureMaterializationV2, ...]: ...


@dataclass(frozen=True, slots=True)
class ResearchFeatureRequirementV2:
    """An immutable, portable research requirement.

    Deliberately carries no ``subject_id``: a requirement describes what
    *kind* of subject a feature applies to (``expected_subject_type``), not
    which concrete instrument or series. Binding a concrete subject is the
    research request's job (:class:`ResearchFeatureBundleRequestV2`), which is
    what lets one research hypothesis stay portable across every eligible
    instrument or series.
    """

    feature_id: UUID
    name: str
    semantic_version: str
    expected_subject_type: FeatureSubjectType

    @property
    def version_key(self) -> str:
        return f"{self.name}:{self.semantic_version}"

    def validate(self) -> None:
        if not self.name.strip() or not self.semantic_version.strip():
            raise StrategyFeatureBindingV2Error("invalid_research_feature_requirement")


@dataclass(frozen=True, slots=True)
class AuthoritativeFeatureSeriesV2:
    """One requirement's resolved, quality-filtered evidence for one subject.

    Every invariant is re-checked here regardless of what the reader already
    guarantees: this is the module's own fail-closed boundary, not a trust
    extension of whatever supplied ``materializations``.
    """

    requirement: ResearchFeatureRequirementV2
    subject_type: FeatureSubjectType
    subject_id: str
    dataset_version: str
    materializations: tuple[FeatureMaterializationV2, ...]

    def validate(self) -> None:
        self.requirement.validate()
        if self.subject_type is not self.requirement.expected_subject_type:
            raise StrategyFeatureBindingV2Error("feature_series_subject_type_mismatch")
        if not self.subject_id.strip() or not self.dataset_version.strip():
            raise StrategyFeatureBindingV2Error("feature_series_identity_missing")
        if not self.materializations:
            raise StrategyFeatureBindingV2Error("feature_series_unavailable")
        seen_events: set[datetime] = set()
        previous: datetime | None = None
        for item in self.materializations:
            item.validate()
            if item.feature_id != self.requirement.feature_id:
                raise StrategyFeatureBindingV2Error("feature_series_id_mismatch")
            if item.subject_type is not self.subject_type:
                raise StrategyFeatureBindingV2Error("feature_series_subject_type_mismatch")
            if item.subject_id != self.subject_id:
                raise StrategyFeatureBindingV2Error("feature_series_subject_id_mismatch")
            if item.dataset_version != self.dataset_version:
                raise StrategyFeatureBindingV2Error("feature_series_dataset_mismatch")
            if item.event_at in seen_events:
                raise StrategyFeatureBindingV2Error("feature_series_duplicate_event")
            seen_events.add(item.event_at)
            if previous is not None and item.event_at <= previous:
                raise StrategyFeatureBindingV2Error("feature_series_not_chronological")
            previous = item.event_at


@dataclass(frozen=True, slots=True)
class ResearchFeatureBundleRequestV2:
    """One research request: a subject, a sealed dataset, and its requirements.

    Enforces the 3J.2a v1 subject-homogeneity rule structurally -- there is
    exactly one ``subject_type``/``subject_id`` for the whole request, so a
    mixed ``INSTRUMENT``/``FUTURES_SERIES`` bundle, or a bundle spanning two
    different concrete subjects, cannot be expressed at all.
    """

    subject_type: FeatureSubjectType
    subject_id: str
    dataset_version_id: UUID
    decision_at: datetime
    requirements: tuple[ResearchFeatureRequirementV2, ...]
    quality_policy: ResearchQualityPolicyV2 = ResearchQualityPolicyV2.VALIDATED_ONLY

    def validate(self) -> None:
        if not self.subject_id.strip():
            raise StrategyFeatureBindingV2Error("research_bundle_subject_missing")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise StrategyFeatureBindingV2Error("research_bundle_decision_time_must_be_aware")
        if not self.requirements:
            raise StrategyFeatureBindingV2Error("research_bundle_requires_features")
        for requirement in self.requirements:
            requirement.validate()
            if requirement.expected_subject_type is not self.subject_type:
                raise StrategyFeatureBindingV2Error("research_bundle_wrong_subject_type")
        if len({item.feature_id for item in self.requirements}) != len(self.requirements):
            raise StrategyFeatureBindingV2Error("duplicate_research_feature_requirement")


@dataclass(frozen=True, slots=True)
class SubjectAwareResearchFeatureBundle:
    """One research request bound to exact Feature Authority evidence.

    Immutable and content-hashed: identical canonical evidence (dataset,
    subject, feature versions, materialization identities/content hashes,
    quality states, decision time) always produces the same
    ``content_hash``. No second copy of any financial value is persisted --
    the hash is computed over identity and hash fields, never over raw
    ``Decimal`` values, and the bundle itself lives only in application
    memory.
    """

    dataset_version_id: UUID
    subject_type: FeatureSubjectType
    subject_id: str
    decision_at: datetime
    quality_policy: ResearchQualityPolicyV2
    feature_series: tuple[AuthoritativeFeatureSeriesV2, ...]
    content_hash: str
    bundle_id: UUID

    def validate(self) -> None:
        if not self.subject_id.strip():
            raise StrategyFeatureBindingV2Error("research_bundle_subject_missing")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise StrategyFeatureBindingV2Error("research_bundle_decision_time_must_be_aware")
        if not self.feature_series:
            raise StrategyFeatureBindingV2Error("research_bundle_requires_features")
        seen_features: set[UUID] = set()
        for series in self.feature_series:
            series.validate()
            if series.subject_type is not self.subject_type or series.subject_id != self.subject_id:
                raise StrategyFeatureBindingV2Error("research_bundle_mixed_subject")
            if series.dataset_version != str(self.dataset_version_id):
                raise StrategyFeatureBindingV2Error("research_bundle_mixed_dataset")
            if series.requirement.feature_id in seen_features:
                raise StrategyFeatureBindingV2Error("duplicate_research_feature_requirement")
            seen_features.add(series.requirement.feature_id)
            for item in series.materializations:
                if (
                    item.event_at > self.decision_at
                    or item.effective_at > self.decision_at
                    or item.knowledge_at > self.decision_at
                    or item.computed_at > self.decision_at
                ):
                    raise StrategyFeatureBindingV2Error("future_feature_knowledge")

    @classmethod
    def create(
        cls,
        *,
        dataset_version_id: UUID,
        subject_type: FeatureSubjectType,
        subject_id: str,
        decision_at: datetime,
        quality_policy: ResearchQualityPolicyV2,
        feature_series: tuple[AuthoritativeFeatureSeriesV2, ...],
    ) -> SubjectAwareResearchFeatureBundle:
        ordered = tuple(sorted(feature_series, key=lambda series: str(series.requirement.feature_id)))
        payload = {
            "dataset_version_id": str(dataset_version_id),
            "subject_type": subject_type.value,
            "subject_id": subject_id,
            "decision_at": decision_at.isoformat(),
            "quality_policy": quality_policy.value,
            "features": [
                {
                    "feature_id": str(series.requirement.feature_id),
                    "name": series.requirement.name,
                    "semantic_version": series.requirement.semantic_version,
                    "materializations": [
                        {
                            "materialization_id": str(item.materialization_id),
                            "content_hash": item.content_hash,
                            "quality_status": item.quality_status.value,
                        }
                        for item in series.materializations
                    ],
                }
                for series in ordered
            ],
        }
        content_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        bundle_id = uuid5(NAMESPACE_URL, f"research-feature-bundle-v2:{content_hash}")
        bundle = cls(
            dataset_version_id, subject_type, subject_id, decision_at, quality_policy,
            ordered, content_hash, bundle_id,
        )
        bundle.validate()
        return bundle


class ResearchFeatureBundleStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ResearchFeatureBundleOutcome:
    status: ResearchFeatureBundleStatus
    reasons: tuple[str, ...]
    bundle: SubjectAwareResearchFeatureBundle | None


def build_research_feature_bundle(
    reader: FeatureAuthorityReaderV2, request: ResearchFeatureBundleRequestV2
) -> ResearchFeatureBundleOutcome:
    """Resolve one request into an available bundle or an unavailable outcome.

    A genuinely absent feature (nothing materialized yet for this exact
    dataset/subject/decision time, including a subject nothing was ever
    authored against -- the existing Feature Authority's own write-side
    subject-existence trigger is what prevents any row from ever existing for
    an unknown subject) makes the whole bundle unavailable; it never falls
    back to another dataset or another subject. A structural contract
    violation -- wrong feature identity, wrong subject, cross-dataset
    evidence -- raises instead, since that is a caller/config defect rather
    than legitimately missing data.
    """
    request.validate()
    resolved: list[AuthoritativeFeatureSeriesV2] = []
    unavailable_reasons: list[str] = []
    dataset_version = str(request.dataset_version_id)
    for requirement in request.requirements:
        definition = reader.definition(requirement.feature_id)
        if definition.name != requirement.name or definition.semantic_version != requirement.semantic_version:
            raise StrategyFeatureBindingV2Error("research_feature_version_mismatch")
        raw = reader.latest_as_of_subject(
            requirement.feature_id, request.subject_type, request.subject_id,
            dataset_version, request.decision_at,
        )
        selected = tuple(item for item in raw if _quality_admitted(item.quality_status, request.quality_policy))
        if not selected:
            unavailable_reasons.append(f"{requirement.name}:required_feature_unavailable")
            continue
        resolved.append(
            AuthoritativeFeatureSeriesV2(
                requirement, request.subject_type, request.subject_id, dataset_version, selected,
            )
        )
    if unavailable_reasons:
        return ResearchFeatureBundleOutcome(
            ResearchFeatureBundleStatus.UNAVAILABLE, tuple(sorted(unavailable_reasons)), None
        )
    bundle = SubjectAwareResearchFeatureBundle.create(
        dataset_version_id=request.dataset_version_id,
        subject_type=request.subject_type,
        subject_id=request.subject_id,
        decision_at=request.decision_at,
        quality_policy=request.quality_policy,
        feature_series=tuple(resolved),
    )
    return ResearchFeatureBundleOutcome(ResearchFeatureBundleStatus.AVAILABLE, (), bundle)


@dataclass(frozen=True, slots=True)
class AlignedFeatureMatrixV2:
    """A deterministic exact-event alignment of a bundle against target timestamps.

    Only an exact ``event_at`` match is ever returned; an absent timestamp is
    ``None`` in that position, never forward-filled, backward-filled or
    nearest-matched. The feature series feeding the bundle may itself remain
    irregular/event-driven -- this primitive does not require, and never
    assumes, that a feature exists at every requested timestamp.
    """

    bundle_content_hash: str
    timestamps: tuple[datetime, ...]
    values: Mapping[str, tuple[Decimal | None, ...]]


def align_exact_event_feature_matrix(
    bundle: SubjectAwareResearchFeatureBundle, timestamps: tuple[datetime, ...]
) -> AlignedFeatureMatrixV2:
    if not timestamps:
        raise StrategyFeatureBindingV2Error("alignment_requires_timestamps")
    for timestamp in timestamps:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise StrategyFeatureBindingV2Error("alignment_timestamp_must_be_aware")
    values: dict[str, tuple[Decimal | None, ...]] = {}
    for series in bundle.feature_series:
        by_event = {item.event_at: item.value for item in series.materializations}
        values[series.requirement.name] = tuple(by_event.get(timestamp) for timestamp in timestamps)
    return AlignedFeatureMatrixV2(bundle.content_hash, timestamps, values)
