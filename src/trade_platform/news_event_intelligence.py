"""Correction-aware, rights-gated news/event evidence with no execution authority."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .persistence import PostgresDatabase


class NewsEventIntelligenceError(ValueError):
    pass


class RightsStatus(StrEnum):
    APPROVED = "APPROVED"
    NOT_APPROVED = "NOT_APPROVED"


class RevisionKind(StrEnum):
    INITIAL = "INITIAL"
    CORRECTION = "CORRECTION"
    RETRACTION = "RETRACTION"
    FOLLOW_UP = "FOLLOW_UP"


class Officiality(StrEnum):
    OFFICIAL = "OFFICIAL"
    REPORTED = "REPORTED"
    RUMOR = "RUMOR"


class LinkMethod(StrEnum):
    PROVIDER_IDENTIFIER = "PROVIDER_IDENTIFIER"
    IDENTIFIER_MAPPING = "IDENTIFIER_MAPPING"
    DETERMINISTIC_ALIAS = "DETERMINISTIC_ALIAS"
    REVIEWED = "REVIEWED"


class EventType(StrEnum):
    EARNINGS = "EARNINGS"
    GUIDANCE = "GUIDANCE"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    INDEX_CHANGE = "INDEX_CHANGE"
    MERGER_ACQUISITION = "MERGER_ACQUISITION"
    REGULATORY = "REGULATORY"
    MANAGEMENT = "MANAGEMENT"
    PRODUCT = "PRODUCT"
    LITIGATION = "LITIGATION"
    CREDIT = "CREDIT"
    MACRO_RELEASE = "MACRO_RELEASE"
    GEOPOLITICAL = "GEOPOLITICAL"
    OTHER = "OTHER"


class EventHorizon(StrEnum):
    INTRADAY = "INTRADAY"
    DAYS = "DAYS"
    WEEKS = "WEEKS"
    LONG_TERM = "LONG_TERM"


class EvidenceStatus(StrEnum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    WITHDRAWN = "WITHDRAWN"


class ConfidenceAction(StrEnum):
    MAINTAIN = "MAINTAIN"
    REDUCE = "REDUCE"
    WITHDRAW = "WITHDRAW"


LIMITATIONS = (
    "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY",
    "RESEARCH_ONLY_NEWS_EVENT_EVIDENCE",
    "NO_PROVIDER_ACTIVATION_OR_CONTENT_ACQUISITION",
    "NO_SIGNAL_ORDER_OMS_BROKER_OR_EXECUTION_AUTHORITY",
    "NO_AUTOMATIC_CONFIDENCE_OR_RISK_INCREASE",
)

_PROMPT_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?previous\s+instructions\b", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    re.compile(r"\bexecute\s+(?:this\s+)?(?:command|code)\b", re.IGNORECASE),
    re.compile(r"<\s*(?:script|iframe|object)\b", re.IGNORECASE),
)
_TAG_PATTERN = re.compile(r"<[^>]*>")
_SPACE_PATTERN = re.compile(r"\s+")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[object, ...]) -> object: ...
    def fetchone(self) -> tuple[object, ...] | None: ...


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NewsEventIntelligenceError(f"{name}_must_be_timezone_aware")


def _decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise NewsEventIntelligenceError("non_finite_news_event_value")
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def sanitize_untrusted_text(value: str) -> tuple[str, tuple[str, ...]]:
    """Return plain text and inert flags; external text is never treated as instructions."""
    flags: list[str] = []
    decoded = html.unescape(value)
    if _TAG_PATTERN.search(decoded):
        flags.append("MARKUP_REMOVED")
    plain = _TAG_PATTERN.sub(" ", decoded)
    if any(pattern.search(decoded) for pattern in _PROMPT_PATTERNS):
        flags.append("PROMPT_INJECTION_PATTERN")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in plain):
        flags.append("CONTROL_CHARACTERS_REMOVED")
    plain = "".join(
        character for character in plain if ord(character) >= 32 or character in "\t\n\r"
    )
    return _SPACE_PATTERN.sub(" ", plain).strip(), tuple(sorted(set(flags)))


def normalized_headline(value: str) -> str:
    plain, _ = sanitize_untrusted_text(value)
    return " ".join(re.findall(r"[\w]+", plain.casefold(), flags=re.UNICODE))


@dataclass(frozen=True, slots=True)
class NewsSourcePolicyV2:
    source_id: UUID
    provider: str
    version: str
    terms_version: str
    authorization_reference: str
    rights_status: RightsStatus
    raw_storage_allowed: bool
    derived_use_allowed: bool
    permitted_languages: tuple[str, ...]
    reliability: Decimal
    created_at: datetime
    status: str = "CONFIGURATION_ONLY"
    provider_activated: bool = False
    source_policy_version_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "news_source_policy_created_at")
        languages = tuple(item.casefold() for item in self.permitted_languages)
        if (
            not all(
                item.strip()
                for item in (
                    self.provider,
                    self.version,
                    self.terms_version,
                    self.authorization_reference,
                )
            )
            or not languages
            or any(not item.strip() for item in languages)
            or len(set(languages)) != len(languages)
            or not Decimal("0") <= self.reliability <= Decimal("1")
            or self.status != "CONFIGURATION_ONLY"
            or self.provider_activated
        ):
            raise NewsEventIntelligenceError("invalid_news_source_policy")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "source_policy_version_id",
            uuid5(NAMESPACE_URL, f"news-source-policy:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "source_id": str(self.source_id),
            "provider": self.provider,
            "version": self.version,
            "terms_version": self.terms_version,
            "authorization_reference": self.authorization_reference,
            "rights_status": self.rights_status.value,
            "raw_storage_allowed": self.raw_storage_allowed,
            "derived_use_allowed": self.derived_use_allowed,
            "permitted_languages": tuple(item.casefold() for item in self.permitted_languages),
            "reliability": _decimal(self.reliability),
            "created_at": self.created_at,
            "status": self.status,
            "provider_activated": self.provider_activated,
        }


@dataclass(frozen=True, slots=True)
class NewsDocumentRevisionV2:
    source_policy_version_id: UUID
    source_item_id: str
    root_source_item_id: str
    revision: int
    revision_kind: RevisionKind
    supersedes_document_revision_id: UUID | None
    source_url: str
    language: str
    headline: str
    published_at: datetime
    source_updated_at: datetime
    ingested_at: datetime
    content_fingerprint: str
    raw_payload_sha256: str
    officiality: Officiality
    sanitization_flags: tuple[str, ...]
    metadata: tuple[tuple[str, str], ...] = ()
    document_revision_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.published_at, "news_published_at"),
            (self.source_updated_at, "news_source_updated_at"),
            (self.ingested_at, "news_ingested_at"),
        ):
            _aware(value, name)
        clean, detected = sanitize_untrusted_text(self.headline)
        flags = tuple(sorted(set(self.sanitization_flags).union(detected)))
        initial_valid = (
            self.revision == 0
            and self.revision_kind is RevisionKind.INITIAL
            and self.supersedes_document_revision_id is None
        )
        successor_valid = (
            self.revision > 0
            and self.revision_kind is not RevisionKind.INITIAL
            and self.supersedes_document_revision_id is not None
        )
        if (
            not all(
                item.strip()
                for item in (
                    self.source_item_id,
                    self.root_source_item_id,
                    self.source_url,
                    self.language,
                    clean,
                )
            )
            or not _HEX64.fullmatch(self.content_fingerprint)
            or not _HEX64.fullmatch(self.raw_payload_sha256)
            or self.source_updated_at < self.published_at
            or self.ingested_at < self.source_updated_at
            or not (initial_valid or successor_valid)
            or len({key for key, _ in self.metadata}) != len(self.metadata)
        ):
            raise NewsEventIntelligenceError("invalid_news_document_revision")
        object.__setattr__(self, "headline", clean)
        object.__setattr__(self, "sanitization_flags", flags)
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "document_revision_id",
            uuid5(NAMESPACE_URL, f"news-document-revision:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "source_policy_version_id": str(self.source_policy_version_id),
            "source_item_id": self.source_item_id,
            "root_source_item_id": self.root_source_item_id,
            "revision": self.revision,
            "revision_kind": self.revision_kind.value,
            "supersedes_document_revision_id": (
                None
                if self.supersedes_document_revision_id is None
                else str(self.supersedes_document_revision_id)
            ),
            "source_url": self.source_url,
            "language": self.language.casefold(),
            "headline": self.headline,
            "published_at": self.published_at,
            "source_updated_at": self.source_updated_at,
            "ingested_at": self.ingested_at,
            "content_fingerprint": self.content_fingerprint,
            "raw_payload_sha256": self.raw_payload_sha256,
            "officiality": self.officiality.value,
            "sanitization_flags": self.sanitization_flags,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class NewsEntityLinkV2:
    document_revision_id: UUID
    instrument_id: str
    method: LinkMethod
    confidence: Decimal
    ambiguous: bool
    evidence: tuple[tuple[str, str], ...]
    entity_link_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            not self.instrument_id.strip()
            or not Decimal("0") <= self.confidence <= Decimal("1")
            or not self.evidence
            or len({key for key, _ in self.evidence}) != len(self.evidence)
        ):
            raise NewsEventIntelligenceError("invalid_news_entity_link")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self, "entity_link_id", uuid5(NAMESPACE_URL, f"news-entity-link:{content_hash}")
        )

    def payload(self) -> dict[str, object]:
        return {
            "document_revision_id": str(self.document_revision_id),
            "instrument_id": self.instrument_id,
            "method": self.method.value,
            "confidence": _decimal(self.confidence),
            "ambiguous": self.ambiguous,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class NewsEventExtractionV2:
    document_revision_id: UUID
    event_type: EventType
    taxonomy_version: str
    model_version: str
    novelty: Decimal
    urgency: Decimal
    uncertainty: Decimal
    horizon: EventHorizon
    extracted_at: datetime
    facts: tuple[str, ...]
    limitations: tuple[str, ...]
    extraction_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.extracted_at, "news_extracted_at")
        if (
            not self.taxonomy_version.strip()
            or not self.model_version.strip()
            or not self.facts
            or any(not item.strip() for item in self.facts + self.limitations)
            or any(
                not Decimal("0") <= item <= Decimal("1")
                for item in (self.novelty, self.urgency, self.uncertainty)
            )
        ):
            raise NewsEventIntelligenceError("invalid_news_event_extraction")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "extraction_id",
            uuid5(NAMESPACE_URL, f"news-event-extraction:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "document_revision_id": str(self.document_revision_id),
            "event_type": self.event_type.value,
            "taxonomy_version": self.taxonomy_version,
            "model_version": self.model_version,
            "novelty": _decimal(self.novelty),
            "urgency": _decimal(self.urgency),
            "uncertainty": _decimal(self.uncertainty),
            "horizon": self.horizon.value,
            "extracted_at": self.extracted_at,
            "facts": self.facts,
            "limitations": self.limitations,
        }


@dataclass(frozen=True, slots=True)
class NewsEventClusterV2:
    cluster_key: str
    canonical_document_revision_id: UUID
    members: tuple[tuple[UUID, str], ...]
    created_at: datetime
    cluster_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "news_cluster_created_at")
        member_ids = tuple(item[0] for item in self.members)
        if (
            not _HEX64.fullmatch(self.cluster_key)
            or not self.members
            or self.canonical_document_revision_id not in member_ids
            or len(set(member_ids)) != len(member_ids)
            or any(
                kind not in {"CANONICAL", "EXACT_CONTENT", "NORMALIZED_HEADLINE", "FOLLOW_UP"}
                for _, kind in self.members
            )
        ):
            raise NewsEventIntelligenceError("invalid_news_event_cluster")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(self, "cluster_id", uuid5(NAMESPACE_URL, f"news-cluster:{content_hash}"))

    def payload(self) -> dict[str, object]:
        return {
            "cluster_key": self.cluster_key,
            "canonical_document_revision_id": str(self.canonical_document_revision_id),
            "members": tuple((str(item), kind) for item, kind in self.members),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class NewsEventAssessmentV2:
    document_revision_id: UUID
    extraction_id: UUID
    entity_link_id: UUID | None
    assessed_at: datetime
    status: EvidenceStatus
    credibility: Decimal
    reasons: tuple[str, ...]
    data_health_assessment_ids: tuple[UUID, ...]
    report: tuple[tuple[str, str], ...]
    assessment_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.assessed_at, "news_assessed_at")
        if (
            not Decimal("0") <= self.credibility <= Decimal("1")
            or not self.data_health_assessment_ids
            or len(set(self.data_health_assessment_ids)) != len(self.data_health_assessment_ids)
            or len({key for key, _ in self.report}) != len(self.report)
            or (self.status is EvidenceStatus.REVIEW_REQUIRED and self.reasons)
            or (self.status is not EvidenceStatus.REVIEW_REQUIRED and not self.reasons)
        ):
            raise NewsEventIntelligenceError("invalid_news_event_assessment")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self, "assessment_id", uuid5(NAMESPACE_URL, f"news-assessment:{content_hash}")
        )

    def payload(self) -> dict[str, object]:
        return {
            "document_revision_id": str(self.document_revision_id),
            "extraction_id": str(self.extraction_id),
            "entity_link_id": None if self.entity_link_id is None else str(self.entity_link_id),
            "assessed_at": self.assessed_at,
            "status": self.status.value,
            "credibility": _decimal(self.credibility),
            "reasons": self.reasons,
            "data_health_assessment_ids": tuple(
                str(item) for item in self.data_health_assessment_ids
            ),
            "report": self.report,
        }


@dataclass(frozen=True, slots=True)
class NewsResearchCandidateV2:
    assessment_id: UUID
    instrument_id: str | None
    strategy_version_id: UUID | None
    thesis_id: UUID | None
    confidence_action: ConfidenceAction
    maximum_confidence: Decimal
    status: EvidenceStatus
    created_at: datetime
    research_only: bool = True
    automatic_authority: bool = False
    candidate_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "news_candidate_created_at")
        if (
            not Decimal("0") <= self.maximum_confidence <= Decimal("1")
            or not self.research_only
            or self.automatic_authority
            or self.confidence_action not in (
                ConfidenceAction.MAINTAIN,
                ConfidenceAction.REDUCE,
                ConfidenceAction.WITHDRAW,
            )
            or (
                self.status is not EvidenceStatus.REVIEW_REQUIRED
                and self.confidence_action is ConfidenceAction.MAINTAIN
            )
        ):
            raise NewsEventIntelligenceError("invalid_news_research_candidate")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self, "candidate_id", uuid5(NAMESPACE_URL, f"news-research-candidate:{content_hash}")
        )

    def payload(self) -> dict[str, object]:
        return {
            "assessment_id": str(self.assessment_id),
            "instrument_id": self.instrument_id,
            "strategy_version_id": (
                None if self.strategy_version_id is None else str(self.strategy_version_id)
            ),
            "thesis_id": None if self.thesis_id is None else str(self.thesis_id),
            "confidence_action": self.confidence_action.value,
            "maximum_confidence": _decimal(self.maximum_confidence),
            "status": self.status.value,
            "created_at": self.created_at,
            "research_only": self.research_only,
            "automatic_authority": self.automatic_authority,
        }


@dataclass(frozen=True, slots=True)
class NewsEventIntelligenceResultV2:
    clusters: tuple[NewsEventClusterV2, ...]
    assessments: tuple[NewsEventAssessmentV2, ...]
    candidates: tuple[NewsResearchCandidateV2, ...]


def cluster_documents(
    documents: tuple[NewsDocumentRevisionV2, ...], created_at: datetime
) -> tuple[NewsEventClusterV2, ...]:
    """Deterministically group exact-content and normalized-headline duplicates."""
    _aware(created_at, "news_cluster_created_at")
    if not documents:
        raise NewsEventIntelligenceError("empty_news_document_batch")
    groups: dict[str, list[NewsDocumentRevisionV2]] = {}
    for document in documents:
        key = _digest(
            {
                "content": document.content_fingerprint,
                "headline": normalized_headline(document.headline),
            }
        )
        groups.setdefault(key, []).append(document)
    result: list[NewsEventClusterV2] = []
    for key, members in sorted(groups.items()):
        ordered = sorted(
            members,
            key=lambda item: (item.published_at, item.ingested_at, str(item.document_revision_id)),
        )
        canonical = ordered[0]
        membership: list[tuple[UUID, str]] = []
        for item in ordered:
            if item.document_revision_id == canonical.document_revision_id:
                kind = "CANONICAL"
            elif item.revision_kind is RevisionKind.FOLLOW_UP:
                kind = "FOLLOW_UP"
            elif item.content_fingerprint == canonical.content_fingerprint:
                kind = "EXACT_CONTENT"
            else:
                kind = "NORMALIZED_HEADLINE"
            membership.append((item.document_revision_id, kind))
        result.append(
            NewsEventClusterV2(key, canonical.document_revision_id, tuple(membership), created_at)
        )
    return tuple(result)


def assess_news_event(
    source: NewsSourcePolicyV2,
    document: NewsDocumentRevisionV2,
    extraction: NewsEventExtractionV2,
    links: tuple[NewsEntityLinkV2, ...],
    *,
    assessed_at: datetime,
    data_health_status: str,
    data_health_assessment_ids: tuple[UUID, ...],
    maximum_ingestion_latency: timedelta = timedelta(hours=6),
) -> NewsEventAssessmentV2:
    """Build review evidence; any missing authority, ambiguity, or correction fails closed."""
    _aware(assessed_at, "news_assessed_at")
    if document.source_policy_version_id != source.source_policy_version_id:
        raise NewsEventIntelligenceError("news_source_policy_mismatch")
    if extraction.document_revision_id != document.document_revision_id:
        raise NewsEventIntelligenceError("news_extraction_document_mismatch")
    if any(link.document_revision_id != document.document_revision_id for link in links):
        raise NewsEventIntelligenceError("news_entity_link_document_mismatch")
    if extraction.extracted_at < document.ingested_at or assessed_at < extraction.extracted_at:
        raise NewsEventIntelligenceError("news_future_evidence")
    if not data_health_assessment_ids:
        raise NewsEventIntelligenceError("news_data_health_evidence_required")

    reasons: list[str] = []
    withdrawn = document.revision_kind is RevisionKind.RETRACTION
    if source.rights_status is not RightsStatus.APPROVED:
        reasons.append("SOURCE_RIGHTS_NOT_APPROVED")
    if not source.derived_use_allowed:
        reasons.append("DERIVED_USE_NOT_ALLOWED")
    if document.language.casefold() not in {
        item.casefold() for item in source.permitted_languages
    }:
        reasons.append("LANGUAGE_NOT_PERMITTED_OR_VALIDATED")
    if document.ingested_at - document.published_at > maximum_ingestion_latency:
        reasons.append("LATE_INGESTION")
    if document.sanitization_flags:
        reasons.append("UNTRUSTED_CONTENT_SANITIZED")
    if data_health_status != "HEALTHY":
        reasons.append("DATA_HEALTH_BLOCKED")
    valid_links = tuple(link for link in links if not link.ambiguous and link.confidence >= Decimal("0.8"))
    if len(valid_links) != 1 or len(links) != 1:
        reasons.append("AMBIGUOUS_OR_MISSING_ENTITY_LINK")
    if document.officiality is Officiality.RUMOR:
        reasons.append("RUMOR_REQUIRES_CORROBORATION")
    if withdrawn:
        reasons.append("SOURCE_RETRACTION")

    link_confidence = valid_links[0].confidence if len(valid_links) == 1 else Decimal("0")
    officiality_factor = {
        Officiality.OFFICIAL: Decimal("1"),
        Officiality.REPORTED: Decimal("0.8"),
        Officiality.RUMOR: Decimal("0.25"),
    }[document.officiality]
    credibility = (
        source.reliability
        * link_confidence
        * (Decimal("1") - extraction.uncertainty)
        * officiality_factor
    )
    credibility = max(Decimal("0"), min(Decimal("1"), credibility))
    status = (
        EvidenceStatus.WITHDRAWN
        if withdrawn
        else EvidenceStatus.BLOCKED
        if reasons
        else EvidenceStatus.REVIEW_REQUIRED
    )
    if withdrawn:
        credibility = Decimal("0")
    return NewsEventAssessmentV2(
        document.document_revision_id,
        extraction.extraction_id,
        valid_links[0].entity_link_id if len(valid_links) == 1 else None,
        assessed_at,
        status,
        credibility,
        tuple(sorted(set(reasons))),
        data_health_assessment_ids,
        (
            ("event_type", extraction.event_type.value),
            ("horizon", extraction.horizon.value),
            ("novelty", _decimal(extraction.novelty)),
            ("urgency", _decimal(extraction.urgency)),
            ("uncertainty", _decimal(extraction.uncertainty)),
            ("officiality", document.officiality.value),
            ("limitations", "|".join(LIMITATIONS)),
        ),
    )


def build_research_candidate(
    assessment: NewsEventAssessmentV2,
    *,
    instrument_id: str | None,
    created_at: datetime,
    strategy_version_id: UUID | None = None,
    thesis_id: UUID | None = None,
) -> NewsResearchCandidateV2:
    """Translate evidence to a review candidate that can only maintain or reduce confidence."""
    action = (
        ConfidenceAction.WITHDRAW
        if assessment.status is EvidenceStatus.WITHDRAWN
        else ConfidenceAction.REDUCE
        if assessment.status is EvidenceStatus.BLOCKED
        else ConfidenceAction.MAINTAIN
    )
    maximum = assessment.credibility if action is ConfidenceAction.MAINTAIN else Decimal("0")
    return NewsResearchCandidateV2(
        assessment.assessment_id,
        instrument_id,
        strategy_version_id,
        thesis_id,
        action,
        maximum,
        assessment.status,
        created_at,
    )


class PostgresNewsEventIntelligenceStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    @staticmethod
    def _ensure_hash(cursor: _Cursor, table: str, identity: str, value: UUID, digest: str) -> None:
        cursor.execute(f"SELECT content_hash FROM {table} WHERE {identity}=%s", (value,))  # nosec B608: internal fixed identifiers
        row = cursor.fetchone()
        if row is None or str(row[0]) != digest:
            raise NewsEventIntelligenceError(f"news_content_conflict:{table}")

    def publish_source(self, source: NewsSourcePolicyV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO news_source_policy_versions (source_policy_version_id,source_id,provider,version,terms_version,authorization_reference,rights_status,raw_storage_allowed,derived_use_allowed,permitted_languages,reliability,status,provider_activated,created_at,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,FALSE,%s,%s) ON CONFLICT (source_policy_version_id) DO NOTHING",
                    (
                        source.source_policy_version_id,
                        source.source_id,
                        source.provider,
                        source.version,
                        source.terms_version,
                        source.authorization_reference,
                        source.rights_status.value,
                        source.raw_storage_allowed,
                        source.derived_use_allowed,
                        _canonical(source.permitted_languages),
                        source.reliability,
                        source.status,
                        source.created_at,
                        source.content_hash,
                    ),
                )
                self._ensure_hash(
                    cursor,
                    "news_source_policy_versions",
                    "source_policy_version_id",
                    source.source_policy_version_id,
                    source.content_hash,
                )
        except NewsEventIntelligenceError:
            raise
        except Exception as error:
            raise NewsEventIntelligenceError("news_source_publish_failed") from error
        return source.source_policy_version_id

    def publish_evidence(
        self,
        source: NewsSourcePolicyV2,
        documents: tuple[NewsDocumentRevisionV2, ...],
        links: tuple[NewsEntityLinkV2, ...],
        extractions: tuple[NewsEventExtractionV2, ...],
        clusters: tuple[NewsEventClusterV2, ...],
        assessments: tuple[NewsEventAssessmentV2, ...],
        candidates: tuple[NewsResearchCandidateV2, ...],
    ) -> tuple[UUID, ...]:
        if not documents or not assessments or len(assessments) != len(candidates):
            raise NewsEventIntelligenceError("incomplete_news_evidence_batch")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                self._ensure_hash(
                    cursor,
                    "news_source_policy_versions",
                    "source_policy_version_id",
                    source.source_policy_version_id,
                    source.content_hash,
                )
                for document in sorted(documents, key=lambda item: item.revision):
                    if document.source_policy_version_id != source.source_policy_version_id:
                        raise NewsEventIntelligenceError("news_source_policy_mismatch")
                    if document.supersedes_document_revision_id is not None:
                        cursor.execute(
                            "SELECT root_source_item_id,revision FROM news_document_revisions WHERE document_revision_id=%s",
                            (document.supersedes_document_revision_id,),
                        )
                        predecessor = cursor.fetchone()
                        if (
                            predecessor is None
                            or str(predecessor[0]) != document.root_source_item_id
                            or int(str(predecessor[1])) >= document.revision
                        ):
                            raise NewsEventIntelligenceError("invalid_news_revision_lineage")
                    cursor.execute(
                        "INSERT INTO news_document_revisions (document_revision_id,source_policy_version_id,source_item_id,root_source_item_id,revision,revision_kind,supersedes_document_revision_id,source_url,language,headline,published_at,source_updated_at,ingested_at,content_fingerprint,raw_payload_sha256,officiality,sanitization_flags,payload,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s) ON CONFLICT (document_revision_id) DO NOTHING",
                        (
                            document.document_revision_id,
                            document.source_policy_version_id,
                            document.source_item_id,
                            document.root_source_item_id,
                            document.revision,
                            document.revision_kind.value,
                            document.supersedes_document_revision_id,
                            document.source_url,
                            document.language,
                            document.headline,
                            document.published_at,
                            document.source_updated_at,
                            document.ingested_at,
                            document.content_fingerprint,
                            document.raw_payload_sha256,
                            document.officiality.value,
                            _canonical(document.sanitization_flags),
                            _canonical(document.payload()),
                            document.content_hash,
                        ),
                    )
                    self._ensure_hash(cursor, "news_document_revisions", "document_revision_id", document.document_revision_id, document.content_hash)
                    if document.supersedes_document_revision_id is not None:
                        relation = {
                            RevisionKind.CORRECTION: "CORRECTS",
                            RevisionKind.RETRACTION: "RETRACTS",
                            RevisionKind.FOLLOW_UP: "FOLLOWS_UP",
                        }[document.revision_kind]
                        cursor.execute(
                            "INSERT INTO news_event_lineage VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                            (document.supersedes_document_revision_id, document.document_revision_id, relation),
                        )
                for link in links:
                    cursor.execute(
                        "INSERT INTO news_document_entity_links (entity_link_id,document_revision_id,instrument_id,link_method,confidence,ambiguous,evidence,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (entity_link_id) DO NOTHING",
                        (link.entity_link_id, link.document_revision_id, link.instrument_id, link.method.value, link.confidence, link.ambiguous, _canonical(dict(link.evidence)), link.content_hash),
                    )
                    self._ensure_hash(cursor, "news_document_entity_links", "entity_link_id", link.entity_link_id, link.content_hash)
                for extraction in extractions:
                    cursor.execute(
                        "INSERT INTO news_event_extractions (extraction_id,document_revision_id,event_type,taxonomy_version,model_version,novelty,urgency,uncertainty,horizon,extracted_at,facts,limitations,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s) ON CONFLICT (extraction_id) DO NOTHING",
                        (extraction.extraction_id, extraction.document_revision_id, extraction.event_type.value, extraction.taxonomy_version, extraction.model_version, extraction.novelty, extraction.urgency, extraction.uncertainty, extraction.horizon.value, extraction.extracted_at, _canonical(extraction.facts), _canonical(extraction.limitations), extraction.content_hash),
                    )
                    self._ensure_hash(cursor, "news_event_extractions", "extraction_id", extraction.extraction_id, extraction.content_hash)
                for cluster in clusters:
                    cursor.execute(
                        "INSERT INTO news_event_clusters VALUES (%s,%s,%s,%s,%s) ON CONFLICT (cluster_id) DO NOTHING",
                        (cluster.cluster_id, cluster.cluster_key, cluster.canonical_document_revision_id, cluster.created_at, cluster.content_hash),
                    )
                    self._ensure_hash(cursor, "news_event_clusters", "cluster_id", cluster.cluster_id, cluster.content_hash)
                    for document_id, kind in cluster.members:
                        cursor.execute(
                            "INSERT INTO news_event_cluster_members VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                            (cluster.cluster_id, document_id, kind),
                        )
                link_by_assessment = {item.assessment_id: item for item in assessments}
                for assessment in assessments:
                    cursor.execute(
                        "INSERT INTO news_event_assessments (assessment_id,document_revision_id,extraction_id,entity_link_id,assessed_at,status,credibility,reasons,report,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s) ON CONFLICT (assessment_id) DO NOTHING",
                        (assessment.assessment_id, assessment.document_revision_id, assessment.extraction_id, assessment.entity_link_id, assessment.assessed_at, assessment.status.value, assessment.credibility, _canonical(assessment.reasons), _canonical(dict(assessment.report)), assessment.content_hash),
                    )
                    self._ensure_hash(cursor, "news_event_assessments", "assessment_id", assessment.assessment_id, assessment.content_hash)
                    for health_id in assessment.data_health_assessment_ids:
                        cursor.execute(
                            "INSERT INTO news_event_assessment_data_health VALUES (%s,%s) ON CONFLICT DO NOTHING",
                            (assessment.assessment_id, health_id),
                        )
                for candidate in candidates:
                    if candidate.assessment_id not in link_by_assessment:
                        raise NewsEventIntelligenceError("candidate_assessment_not_in_batch")
                    cursor.execute(
                        "INSERT INTO news_event_research_candidates (candidate_id,assessment_id,instrument_id,strategy_version_id,thesis_id,confidence_action,maximum_confidence,status,research_only,automatic_authority,created_at,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,FALSE,%s,%s) ON CONFLICT (candidate_id) DO NOTHING",
                        (candidate.candidate_id, candidate.assessment_id, candidate.instrument_id, candidate.strategy_version_id, candidate.thesis_id, candidate.confidence_action.value, candidate.maximum_confidence, candidate.status.value, candidate.created_at, candidate.content_hash),
                    )
                    self._ensure_hash(cursor, "news_event_research_candidates", "candidate_id", candidate.candidate_id, candidate.content_hash)
        except NewsEventIntelligenceError:
            raise
        except Exception as error:
            raise NewsEventIntelligenceError("news_evidence_publish_failed") from error
        return tuple(item.candidate_id for item in candidates)

    def candidates_for_instrument_as_of(
        self, instrument_id: str, as_of: datetime
    ) -> tuple[tuple[str, str, Decimal, datetime], ...]:
        """Return only the latest then-known revision per source story root."""
        _aware(as_of, "news_as_of")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT status,confidence_action,maximum_confidence,ingested_at FROM (
                    SELECT c.status,c.confidence_action,c.maximum_confidence,d.ingested_at,
                           ROW_NUMBER() OVER (PARTITION BY d.source_policy_version_id,d.root_source_item_id
                                              ORDER BY d.revision DESC,d.ingested_at DESC) AS rank
                    FROM news_event_research_candidates c
                    JOIN news_event_assessments a ON a.assessment_id=c.assessment_id
                    JOIN news_document_revisions d ON d.document_revision_id=a.document_revision_id
                    WHERE c.instrument_id=%s AND d.published_at<=%s AND d.ingested_at<=%s
                ) value WHERE rank=1 ORDER BY ingested_at""",
                (instrument_id, as_of, as_of),
            )
            rows = cursor.fetchall()
        return tuple(
            (str(row[0]), str(row[1]), Decimal(str(row[2])), row[3]) for row in rows
        )
