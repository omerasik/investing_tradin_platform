"""Rights-gated social/narrative research evidence with no trading authority."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from .news_event_intelligence import sanitize_untrusted_text
from .persistence import PostgresDatabase


class SocialNarrativeError(ValueError):
    pass


class SocialRightsStatus(StrEnum):
    APPROVED = "APPROVED"
    NOT_APPROVED = "NOT_APPROVED"


class DiscussionClass(StrEnum):
    RETAIL_SENTIMENT = "RETAIL_SENTIMENT"
    PROFESSIONAL_COMMENTARY = "PROFESSIONAL_COMMENTARY"
    NEWS_AMPLIFICATION = "NEWS_AMPLIFICATION"
    AUTOMATED_SPAM = "AUTOMATED_SPAM"
    PROMOTIONAL_ACTIVITY = "PROMOTIONAL_ACTIVITY"
    ORGANIC_DISCUSSION = "ORGANIC_DISCUSSION"


class NarrativeEvidenceStatus(StrEnum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


EVIDENCE_LABEL = "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"
LIMITATIONS = (
    EVIDENCE_LABEL,
    "RESEARCH_ONLY_SOCIAL_NARRATIVE_EVIDENCE",
    "NO_PROVIDER_ACTIVATION_OR_CONTENT_ACQUISITION",
    "NO_STANDALONE_SOCIAL_SENTIMENT_TRIGGER",
    "NO_SIGNAL_ORDER_OMS_BROKER_OR_EXECUTION_AUTHORITY",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class _Cursor(Protocol):
    def execute(self, query: str, params: tuple[object, ...]) -> object: ...
    def fetchone(self) -> tuple[object, ...] | None: ...
    def fetchall(self) -> list[tuple[object, ...]]: ...


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SocialNarrativeError(f"{name}_must_be_timezone_aware")


def _finite(value: Decimal, name: str) -> Decimal:
    if not value.is_finite():
        raise SocialNarrativeError(f"{name}_must_be_finite")
    return value


def _unit(value: Decimal, name: str) -> Decimal:
    value = _finite(value, name)
    if not Decimal("0") <= value <= Decimal("1"):
        raise SocialNarrativeError(f"{name}_outside_unit_interval")
    return value


def _signed_unit(value: Decimal, name: str) -> Decimal:
    value = _finite(value, name)
    if not Decimal("-1") <= value <= Decimal("1"):
        raise SocialNarrativeError(f"{name}_outside_signed_unit_interval")
    return value


def _decimal(value: Decimal) -> str:
    normalized = _finite(value, "social_narrative_value").normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SocialSourcePolicyV2:
    source_id: UUID
    provider: str
    version: str
    terms_version: str
    authorization_reference: str
    rights_status: SocialRightsStatus
    raw_storage_allowed: bool
    derived_use_allowed: bool
    permitted_classes: tuple[DiscussionClass, ...]
    permitted_languages: tuple[str, ...]
    geographic_processing_allowed: bool
    created_at: datetime
    status: str = "CONFIGURATION_ONLY"
    provider_activated: bool = False
    source_policy_version_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "social_source_policy_created_at")
        languages = tuple(value.casefold() for value in self.permitted_languages)
        if (
            not all(
                value.strip()
                for value in (
                    self.provider,
                    self.version,
                    self.terms_version,
                    self.authorization_reference,
                )
            )
            or not self.permitted_classes
            or len(set(self.permitted_classes)) != len(self.permitted_classes)
            or not languages
            or any(not value.strip() for value in languages)
            or len(set(languages)) != len(languages)
            or (
                self.rights_status is SocialRightsStatus.NOT_APPROVED
                and (self.raw_storage_allowed or self.derived_use_allowed)
            )
            or self.status != "CONFIGURATION_ONLY"
            or self.provider_activated
        ):
            raise SocialNarrativeError("invalid_social_source_policy")
        object.__setattr__(self, "permitted_languages", languages)
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "source_policy_version_id",
            uuid5(NAMESPACE_URL, f"social-source-policy:{content_hash}"),
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
            "permitted_classes": tuple(value.value for value in self.permitted_classes),
            "permitted_languages": self.permitted_languages,
            "geographic_processing_allowed": self.geographic_processing_allowed,
            "created_at": self.created_at,
            "status": self.status,
            "provider_activated": self.provider_activated,
        }


@dataclass(frozen=True, slots=True)
class SocialObservationV2:
    source_policy_version_id: UUID
    source_item_id: str
    instrument_id: str
    discussion_class: DiscussionClass
    author_identifier_hash: str
    language: str
    topic_key: str
    topic_label: str
    text_excerpt: str | None
    published_at: datetime
    ingested_at: datetime
    sentiment: Decimal
    emotional_intensity: Decimal
    bot_likelihood: Decimal
    spam_likelihood: Decimal
    coordinated_promotion_likelihood: Decimal
    influencer_score: Decimal
    geography_bucket: str | None
    content_fingerprint: str
    raw_payload_sha256: str
    evidence_label: str = EVIDENCE_LABEL
    observation_id: UUID = field(init=False)
    sanitization_flags: tuple[str, ...] = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.published_at, "social_published_at")
        _aware(self.ingested_at, "social_ingested_at")
        clean_label, label_flags = sanitize_untrusted_text(self.topic_label)
        clean_excerpt: str | None = None
        excerpt_flags: tuple[str, ...] = ()
        if self.text_excerpt is not None:
            clean_excerpt, excerpt_flags = sanitize_untrusted_text(self.text_excerpt)
        flags = tuple(sorted(set(label_flags).union(excerpt_flags)))
        if (
            not all(
                value.strip()
                for value in (
                    self.source_item_id,
                    self.instrument_id,
                    self.author_identifier_hash,
                    self.language,
                    clean_label,
                )
            )
            or not _HEX64.fullmatch(self.author_identifier_hash)
            or not _HEX64.fullmatch(self.topic_key)
            or not _HEX64.fullmatch(self.content_fingerprint)
            or not _HEX64.fullmatch(self.raw_payload_sha256)
            or self.ingested_at < self.published_at
            or (self.text_excerpt is not None and not clean_excerpt)
            or (self.geography_bucket is not None and not self.geography_bucket.strip())
            or self.evidence_label != EVIDENCE_LABEL
        ):
            raise SocialNarrativeError("invalid_social_observation")
        _signed_unit(self.sentiment, "sentiment")
        for value, name in (
            (self.emotional_intensity, "emotional_intensity"),
            (self.bot_likelihood, "bot_likelihood"),
            (self.spam_likelihood, "spam_likelihood"),
            (
                self.coordinated_promotion_likelihood,
                "coordinated_promotion_likelihood",
            ),
            (self.influencer_score, "influencer_score"),
        ):
            _unit(value, name)
        object.__setattr__(self, "language", self.language.casefold())
        object.__setattr__(self, "topic_label", clean_label)
        object.__setattr__(self, "text_excerpt", clean_excerpt)
        object.__setattr__(self, "sanitization_flags", flags)
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "observation_id",
            uuid5(NAMESPACE_URL, f"social-observation:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "source_policy_version_id": str(self.source_policy_version_id),
            "source_item_id": self.source_item_id,
            "instrument_id": self.instrument_id,
            "discussion_class": self.discussion_class.value,
            "author_identifier_hash": self.author_identifier_hash,
            "language": self.language.casefold(),
            "topic_key": self.topic_key,
            "topic_label": self.topic_label,
            "text_excerpt": self.text_excerpt,
            "published_at": self.published_at,
            "ingested_at": self.ingested_at,
            "sentiment": _decimal(self.sentiment),
            "emotional_intensity": _decimal(self.emotional_intensity),
            "bot_likelihood": _decimal(self.bot_likelihood),
            "spam_likelihood": _decimal(self.spam_likelihood),
            "coordinated_promotion_likelihood": _decimal(self.coordinated_promotion_likelihood),
            "influencer_score": _decimal(self.influencer_score),
            "geography_bucket": self.geography_bucket,
            "content_fingerprint": self.content_fingerprint,
            "raw_payload_sha256": self.raw_payload_sha256,
            "evidence_label": self.evidence_label,
            "sanitization_flags": self.sanitization_flags,
        }


@dataclass(frozen=True, slots=True)
class SocialNarrativeClusterV2:
    source_policy_version_id: UUID
    instrument_id: str
    model_version: str
    topic_key: str
    topic_label: str
    observation_ids: tuple[UUID, ...]
    created_at: datetime
    cluster_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _aware(self.created_at, "social_cluster_created_at")
        label, flags = sanitize_untrusted_text(self.topic_label)
        ordered = tuple(sorted(set(self.observation_ids), key=str))
        if (
            not self.instrument_id.strip()
            or not self.model_version.strip()
            or not _HEX64.fullmatch(self.topic_key)
            or not label
            or flags
            or not ordered
            or len(ordered) != len(self.observation_ids)
        ):
            raise SocialNarrativeError("invalid_social_narrative_cluster")
        object.__setattr__(self, "topic_label", label)
        object.__setattr__(self, "observation_ids", ordered)
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "cluster_id",
            uuid5(NAMESPACE_URL, f"social-narrative-cluster:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "source_policy_version_id": str(self.source_policy_version_id),
            "instrument_id": self.instrument_id,
            "model_version": self.model_version,
            "topic_key": self.topic_key,
            "topic_label": self.topic_label,
            "observation_ids": tuple(str(value) for value in self.observation_ids),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class SocialNarrativeWindowV2:
    source_policy_version_id: UUID
    instrument_id: str
    cluster_id: UUID
    window_start: datetime
    window_end: datetime
    evaluated_at: datetime
    mention_volume: int
    mention_acceleration: Decimal
    unique_author_count: int
    unique_author_growth: Decimal
    bot_likelihood: Decimal
    sentiment: Decimal
    sentiment_change: Decimal
    disagreement: Decimal
    emotional_intensity: Decimal
    influencer_concentration: Decimal
    crowding_score: Decimal
    spam_concentration: Decimal
    coordinated_promotion_likelihood: Decimal
    pump_and_dump_risk: Decimal
    narrative_persistence: Decimal
    price_sentiment_divergence: Decimal
    category_distribution: tuple[tuple[str, int], ...]
    language_distribution: tuple[tuple[str, int], ...]
    geography_distribution: tuple[tuple[str, int], ...]
    emerging_topic: bool
    status: NarrativeEvidenceStatus
    reasons: tuple[str, ...]
    data_health_assessment_ids: tuple[UUID, ...]
    research_only: bool = True
    standalone_trade_trigger: bool = False
    automatic_authority: bool = False
    evidence_label: str = EVIDENCE_LABEL
    window_id: UUID = field(init=False)
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for timestamp, name in (
            (self.window_start, "social_window_start"),
            (self.window_end, "social_window_end"),
            (self.evaluated_at, "social_window_evaluated_at"),
        ):
            _aware(timestamp, name)
        if (
            not self.instrument_id.strip()
            or self.window_end <= self.window_start
            or self.evaluated_at < self.window_end
            or self.mention_volume <= 0
            or self.unique_author_count <= 0
            or not self.data_health_assessment_ids
            or len(set(self.data_health_assessment_ids)) != len(self.data_health_assessment_ids)
            or not self.research_only
            or self.standalone_trade_trigger
            or self.automatic_authority
            or self.evidence_label != EVIDENCE_LABEL
        ):
            raise SocialNarrativeError("invalid_social_narrative_window")
        for value, name in (
            (self.bot_likelihood, "bot_likelihood"),
            (self.disagreement, "disagreement"),
            (self.emotional_intensity, "emotional_intensity"),
            (self.influencer_concentration, "influencer_concentration"),
            (self.crowding_score, "crowding_score"),
            (self.spam_concentration, "spam_concentration"),
            (
                self.coordinated_promotion_likelihood,
                "coordinated_promotion_likelihood",
            ),
            (self.pump_and_dump_risk, "pump_and_dump_risk"),
            (self.narrative_persistence, "narrative_persistence"),
            (self.price_sentiment_divergence, "price_sentiment_divergence"),
        ):
            _unit(value, name)
        _signed_unit(self.sentiment, "sentiment")
        _signed_unit(self.sentiment_change, "sentiment_change")
        _finite(self.mention_acceleration, "mention_acceleration")
        _finite(self.unique_author_growth, "unique_author_growth")
        expected_classes = tuple(value.value for value in DiscussionClass)
        if tuple(key for key, _ in self.category_distribution) != expected_classes:
            raise SocialNarrativeError("social_category_distribution_incomplete")
        if any(count < 0 for _, count in self.category_distribution):
            raise SocialNarrativeError("invalid_social_category_distribution")
        if sum(count for _, count in self.category_distribution) != self.mention_volume:
            raise SocialNarrativeError("social_category_distribution_mismatch")
        for distribution in (self.language_distribution, self.geography_distribution):
            if len({key for key, _ in distribution}) != len(distribution):
                raise SocialNarrativeError("duplicate_social_distribution_key")
            if any(not key or count <= 0 for key, count in distribution):
                raise SocialNarrativeError("invalid_social_distribution")
        content_hash = _digest(self.payload())
        object.__setattr__(self, "content_hash", content_hash)
        object.__setattr__(
            self,
            "window_id",
            uuid5(NAMESPACE_URL, f"social-narrative-window:{content_hash}"),
        )

    def payload(self) -> dict[str, object]:
        return {
            "source_policy_version_id": str(self.source_policy_version_id),
            "instrument_id": self.instrument_id,
            "cluster_id": str(self.cluster_id),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "evaluated_at": self.evaluated_at,
            "mention_volume": self.mention_volume,
            "mention_acceleration": _decimal(self.mention_acceleration),
            "unique_author_count": self.unique_author_count,
            "unique_author_growth": _decimal(self.unique_author_growth),
            "bot_likelihood": _decimal(self.bot_likelihood),
            "sentiment": _decimal(self.sentiment),
            "sentiment_change": _decimal(self.sentiment_change),
            "disagreement": _decimal(self.disagreement),
            "emotional_intensity": _decimal(self.emotional_intensity),
            "influencer_concentration": _decimal(self.influencer_concentration),
            "crowding_score": _decimal(self.crowding_score),
            "spam_concentration": _decimal(self.spam_concentration),
            "coordinated_promotion_likelihood": _decimal(self.coordinated_promotion_likelihood),
            "pump_and_dump_risk": _decimal(self.pump_and_dump_risk),
            "narrative_persistence": _decimal(self.narrative_persistence),
            "price_sentiment_divergence": _decimal(self.price_sentiment_divergence),
            "category_distribution": self.category_distribution,
            "language_distribution": self.language_distribution,
            "geography_distribution": self.geography_distribution,
            "emerging_topic": self.emerging_topic,
            "status": self.status.value,
            "reasons": self.reasons,
            "data_health_assessment_ids": tuple(
                str(value) for value in self.data_health_assessment_ids
            ),
            "research_only": self.research_only,
            "standalone_trade_trigger": self.standalone_trade_trigger,
            "automatic_authority": self.automatic_authority,
            "evidence_label": self.evidence_label,
            "limitations": LIMITATIONS,
        }


def cluster_social_observations(
    observations: tuple[SocialObservationV2, ...],
    *,
    model_version: str,
    created_at: datetime,
) -> tuple[SocialNarrativeClusterV2, ...]:
    """Cluster only explicit provider/model topic keys; never infer hidden semantics."""
    _aware(created_at, "social_cluster_created_at")
    if not observations or not model_version.strip():
        raise SocialNarrativeError("empty_social_observation_batch")
    if created_at < max(observation.ingested_at for observation in observations):
        raise SocialNarrativeError("social_cluster_precedes_observations")
    groups: dict[tuple[UUID, str, str], list[SocialObservationV2]] = {}
    for observation in observations:
        key = (
            observation.source_policy_version_id,
            observation.instrument_id,
            observation.topic_key,
        )
        groups.setdefault(key, []).append(observation)
    result: list[SocialNarrativeClusterV2] = []
    for (policy_id, instrument_id, topic_key), members in sorted(
        groups.items(), key=lambda item: tuple(str(value) for value in item[0])
    ):
        labels = {member.topic_label.casefold() for member in members}
        if len(labels) != 1:
            raise SocialNarrativeError("social_topic_label_conflict")
        result.append(
            SocialNarrativeClusterV2(
                policy_id,
                instrument_id,
                model_version,
                topic_key,
                members[0].topic_label,
                tuple(member.observation_id for member in members),
                created_at,
            )
        )
    return tuple(result)


def _average(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _distribution(values: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    return tuple((value, values.count(value)) for value in sorted(set(values)))


def build_narrative_window(
    source: SocialSourcePolicyV2,
    cluster: SocialNarrativeClusterV2,
    observations: tuple[SocialObservationV2, ...],
    *,
    window_start: datetime,
    window_end: datetime,
    evaluated_at: datetime,
    data_health_status: str,
    data_health_assessment_ids: tuple[UUID, ...],
    price_return: Decimal,
    previous_window: SocialNarrativeWindowV2 | None = None,
    minimum_observations: int = 3,
) -> SocialNarrativeWindowV2:
    """Calculate bounded research metrics after rights and privacy checks."""
    for value, name in (
        (window_start, "social_window_start"),
        (window_end, "social_window_end"),
        (evaluated_at, "social_window_evaluated_at"),
    ):
        _aware(value, name)
    if source.rights_status is not SocialRightsStatus.APPROVED:
        raise SocialNarrativeError("social_source_rights_not_approved")
    if not source.derived_use_allowed:
        raise SocialNarrativeError("social_derived_use_not_allowed")
    if not observations or minimum_observations <= 0:
        raise SocialNarrativeError("invalid_social_window_batch")
    if window_end <= window_start or evaluated_at < window_end:
        raise SocialNarrativeError("invalid_social_window_time")
    if cluster.source_policy_version_id != source.source_policy_version_id:
        raise SocialNarrativeError("social_cluster_policy_mismatch")
    observation_ids = {value.observation_id for value in observations}
    if not observation_ids.issubset(set(cluster.observation_ids)):
        raise SocialNarrativeError("social_cluster_membership_mismatch")
    for observation in observations:
        if (
            observation.source_policy_version_id != source.source_policy_version_id
            or observation.instrument_id != cluster.instrument_id
            or observation.topic_key != cluster.topic_key
            or observation.discussion_class not in source.permitted_classes
            or observation.language not in source.permitted_languages
            or not window_start <= observation.published_at < window_end
            or observation.ingested_at > evaluated_at
        ):
            raise SocialNarrativeError("social_observation_not_permitted_or_pit")
        if observation.text_excerpt is not None and not source.raw_storage_allowed:
            raise SocialNarrativeError("social_raw_storage_not_allowed")
        if observation.geography_bucket and not source.geographic_processing_allowed:
            raise SocialNarrativeError("social_geography_not_allowed")
    if not data_health_assessment_ids:
        raise SocialNarrativeError("social_data_health_evidence_required")
    if previous_window is not None and (
        previous_window.source_policy_version_id != source.source_policy_version_id
        or previous_window.instrument_id != cluster.instrument_id
        or previous_window.cluster_id != cluster.cluster_id
        or previous_window.window_end > window_start
    ):
        raise SocialNarrativeError("invalid_social_previous_window")

    count = len(observations)
    author_count = len({value.author_identifier_hash for value in observations})
    previous_count = 0 if previous_window is None else previous_window.mention_volume
    previous_authors = 0 if previous_window is None else previous_window.unique_author_count
    mention_acceleration = Decimal(count - previous_count) / Decimal(max(previous_count, 1))
    author_growth = Decimal(author_count - previous_authors) / Decimal(max(previous_authors, 1))
    sentiment = _average(tuple(value.sentiment for value in observations))
    previous_sentiment = Decimal("0") if previous_window is None else previous_window.sentiment
    sentiment_change = max(Decimal("-1"), min(Decimal("1"), sentiment - previous_sentiment))
    disagreement = _average(tuple(abs(value.sentiment - sentiment) for value in observations))
    bot = _average(tuple(value.bot_likelihood for value in observations))
    emotion = _average(tuple(value.emotional_intensity for value in observations))
    spam = _average(tuple(value.spam_likelihood for value in observations))
    coordination = _average(tuple(value.coordinated_promotion_likelihood for value in observations))
    influence = tuple(value.influencer_score for value in observations)
    influence_total = sum(influence, Decimal("0"))
    influencer_concentration = (
        Decimal("0") if influence_total == 0 else max(influence) / influence_total
    )
    promotional_count = sum(
        value.discussion_class
        in {DiscussionClass.PROMOTIONAL_ACTIVITY, DiscussionClass.AUTOMATED_SPAM}
        for value in observations
    )
    promotional_share = Decimal(promotional_count) / Decimal(count)
    crowding = min(
        Decimal("1"),
        (influencer_concentration + coordination + promotional_share) / Decimal("3"),
    )
    normalized_price_return = max(Decimal("-1"), min(Decimal("1"), price_return))
    divergence = min(Decimal("1"), abs(sentiment - normalized_price_return))
    pump_risk = min(
        Decimal("1"),
        (bot + spam + coordination + promotional_share + divergence) / Decimal("5"),
    )
    persistence = Decimal("0") if previous_window is None else Decimal("1")
    reasons: list[str] = []
    if data_health_status != "HEALTHY":
        reasons.append("DATA_HEALTH_BLOCKED")
    if count < minimum_observations:
        reasons.append("INSUFFICIENT_OBSERVATIONS")
    if previous_window is None:
        reasons.append("NO_PRIOR_BASELINE")
    if any(value.sanitization_flags for value in observations):
        reasons.append("UNTRUSTED_CONTENT_SANITIZED")
    if spam >= Decimal("0.6"):
        reasons.append("SPAM_CONCENTRATION_HIGH")
    if coordination >= Decimal("0.6"):
        reasons.append("COORDINATED_PROMOTION_LIKELY")
    if pump_risk >= Decimal("0.6"):
        reasons.append("PUMP_AND_DUMP_RISK_HIGH")
    blocking_reasons = {
        "DATA_HEALTH_BLOCKED",
        "INSUFFICIENT_OBSERVATIONS",
        "PUMP_AND_DUMP_RISK_HIGH",
    }
    status = (
        NarrativeEvidenceStatus.BLOCKED
        if blocking_reasons.intersection(reasons)
        else NarrativeEvidenceStatus.REVIEW_REQUIRED
    )
    category_counts = tuple(
        (
            value.value,
            sum(item.discussion_class is value for item in observations),
        )
        for value in DiscussionClass
    )
    geography = tuple(
        value.geography_bucket for value in observations if value.geography_bucket is not None
    )
    return SocialNarrativeWindowV2(
        source.source_policy_version_id,
        cluster.instrument_id,
        cluster.cluster_id,
        window_start,
        window_end,
        evaluated_at,
        count,
        mention_acceleration,
        author_count,
        author_growth,
        bot,
        sentiment,
        sentiment_change,
        disagreement,
        emotion,
        influencer_concentration,
        crowding,
        spam,
        coordination,
        pump_risk,
        persistence,
        divergence,
        category_counts,
        _distribution(tuple(value.language for value in observations)),
        _distribution(geography) if geography else (),
        previous_window is None and count >= minimum_observations,
        status,
        tuple(sorted(set(reasons))),
        data_health_assessment_ids,
    )


class PostgresSocialNarrativeStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    @staticmethod
    def _ensure_hash(cursor: _Cursor, table: str, identity: str, value: UUID, digest: str) -> None:
        cursor.execute(
            f"SELECT content_hash FROM {table} WHERE {identity}=%s",  # nosec B608: fixed internal identifiers
            (value,),
        )
        row = cursor.fetchone()
        if row is None or str(row[0]) != digest:
            raise SocialNarrativeError(f"social_content_conflict:{table}")

    def publish_source(self, source: SocialSourcePolicyV2) -> UUID:
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO social_source_policy_versions "
                    "(source_policy_version_id,source_id,provider,version,terms_version,"
                    "authorization_reference,rights_status,raw_storage_allowed,"
                    "derived_use_allowed,permitted_classes,permitted_languages,"
                    "geographic_processing_allowed,status,provider_activated,created_at,"
                    "content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,"
                    "%s,%s,FALSE,%s,%s) ON CONFLICT (source_policy_version_id) DO NOTHING",
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
                        _canonical(tuple(value.value for value in source.permitted_classes)),
                        _canonical(source.permitted_languages),
                        source.geographic_processing_allowed,
                        source.status,
                        source.created_at,
                        source.content_hash,
                    ),
                )
                self._ensure_hash(
                    cursor,
                    "social_source_policy_versions",
                    "source_policy_version_id",
                    source.source_policy_version_id,
                    source.content_hash,
                )
        except SocialNarrativeError:
            raise
        except Exception as error:
            raise SocialNarrativeError("social_source_publish_failed") from error
        return source.source_policy_version_id

    def publish_evidence(
        self,
        source: SocialSourcePolicyV2,
        observations: tuple[SocialObservationV2, ...],
        clusters: tuple[SocialNarrativeClusterV2, ...],
        windows: tuple[SocialNarrativeWindowV2, ...],
    ) -> tuple[UUID, ...]:
        if (
            source.rights_status is not SocialRightsStatus.APPROVED
            or not source.derived_use_allowed
            or not observations
            or not clusters
            or not windows
        ):
            raise SocialNarrativeError("social_evidence_not_permitted_or_incomplete")
        try:
            with self._database.transaction() as connection, connection.cursor() as cursor:
                self._ensure_hash(
                    cursor,
                    "social_source_policy_versions",
                    "source_policy_version_id",
                    source.source_policy_version_id,
                    source.content_hash,
                )
                for observation in observations:
                    if observation.source_policy_version_id != source.source_policy_version_id:
                        raise SocialNarrativeError("social_source_policy_mismatch")
                    if observation.text_excerpt is not None and not source.raw_storage_allowed:
                        raise SocialNarrativeError("social_raw_storage_not_allowed")
                    cursor.execute(
                        "INSERT INTO social_content_observations "
                        "(observation_id,source_policy_version_id,source_item_id,instrument_id,"
                        "discussion_class,author_identifier_hash,language,topic_key,topic_label,"
                        "text_excerpt,published_at,ingested_at,sentiment,emotional_intensity,"
                        "bot_likelihood,spam_likelihood,coordinated_promotion_likelihood,"
                        "influencer_score,geography_bucket,content_fingerprint,raw_payload_sha256,"
                        "sanitization_flags,evidence_label,content_hash) VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                        "%s::jsonb,%s,%s) ON CONFLICT (observation_id) DO NOTHING",
                        (
                            observation.observation_id,
                            observation.source_policy_version_id,
                            observation.source_item_id,
                            observation.instrument_id,
                            observation.discussion_class.value,
                            observation.author_identifier_hash,
                            observation.language,
                            observation.topic_key,
                            observation.topic_label,
                            observation.text_excerpt,
                            observation.published_at,
                            observation.ingested_at,
                            observation.sentiment,
                            observation.emotional_intensity,
                            observation.bot_likelihood,
                            observation.spam_likelihood,
                            observation.coordinated_promotion_likelihood,
                            observation.influencer_score,
                            observation.geography_bucket,
                            observation.content_fingerprint,
                            observation.raw_payload_sha256,
                            _canonical(observation.sanitization_flags),
                            observation.evidence_label,
                            observation.content_hash,
                        ),
                    )
                    self._ensure_hash(
                        cursor,
                        "social_content_observations",
                        "observation_id",
                        observation.observation_id,
                        observation.content_hash,
                    )
                for cluster in clusters:
                    cursor.execute(
                        "INSERT INTO social_narrative_clusters "
                        "(cluster_id,source_policy_version_id,instrument_id,model_version,topic_key,"
                        "topic_label,created_at,content_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (cluster_id) DO NOTHING",
                        (
                            cluster.cluster_id,
                            cluster.source_policy_version_id,
                            cluster.instrument_id,
                            cluster.model_version,
                            cluster.topic_key,
                            cluster.topic_label,
                            cluster.created_at,
                            cluster.content_hash,
                        ),
                    )
                    self._ensure_hash(
                        cursor,
                        "social_narrative_clusters",
                        "cluster_id",
                        cluster.cluster_id,
                        cluster.content_hash,
                    )
                    for observation_id in cluster.observation_ids:
                        cursor.execute(
                            "INSERT INTO social_narrative_cluster_members VALUES (%s,%s) "
                            "ON CONFLICT DO NOTHING",
                            (cluster.cluster_id, observation_id),
                        )
                for window in windows:
                    cursor.execute(
                        "INSERT INTO social_narrative_metric_windows "
                        "(window_id,source_policy_version_id,instrument_id,cluster_id,window_start,"
                        "window_end,evaluated_at,mention_volume,mention_acceleration,"
                        "unique_author_count,unique_author_growth,bot_likelihood,sentiment,"
                        "sentiment_change,disagreement,emotional_intensity,"
                        "influencer_concentration,crowding_score,spam_concentration,"
                        "coordinated_promotion_likelihood,pump_and_dump_risk,"
                        "narrative_persistence,price_sentiment_divergence,category_distribution,"
                        "language_distribution,geography_distribution,emerging_topic,status,"
                        "reasons,research_only,standalone_trade_trigger,automatic_authority,"
                        "evidence_label,content_hash) VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                        "%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,TRUE,FALSE,FALSE,%s,%s) "
                        "ON CONFLICT (window_id) DO NOTHING",
                        (
                            window.window_id,
                            window.source_policy_version_id,
                            window.instrument_id,
                            window.cluster_id,
                            window.window_start,
                            window.window_end,
                            window.evaluated_at,
                            window.mention_volume,
                            window.mention_acceleration,
                            window.unique_author_count,
                            window.unique_author_growth,
                            window.bot_likelihood,
                            window.sentiment,
                            window.sentiment_change,
                            window.disagreement,
                            window.emotional_intensity,
                            window.influencer_concentration,
                            window.crowding_score,
                            window.spam_concentration,
                            window.coordinated_promotion_likelihood,
                            window.pump_and_dump_risk,
                            window.narrative_persistence,
                            window.price_sentiment_divergence,
                            _canonical(dict(window.category_distribution)),
                            _canonical(dict(window.language_distribution)),
                            _canonical(dict(window.geography_distribution)),
                            window.emerging_topic,
                            window.status.value,
                            _canonical(window.reasons),
                            window.evidence_label,
                            window.content_hash,
                        ),
                    )
                    self._ensure_hash(
                        cursor,
                        "social_narrative_metric_windows",
                        "window_id",
                        window.window_id,
                        window.content_hash,
                    )
                    for assessment_id in window.data_health_assessment_ids:
                        cursor.execute(
                            "INSERT INTO social_narrative_window_data_health VALUES (%s,%s) "
                            "ON CONFLICT DO NOTHING",
                            (window.window_id, assessment_id),
                        )
        except SocialNarrativeError:
            raise
        except Exception as error:
            raise SocialNarrativeError("social_evidence_publish_failed") from error
        return tuple(window.window_id for window in windows)

    def windows_for_instrument_as_of(
        self, instrument_id: str, as_of: datetime
    ) -> tuple[tuple[str, Decimal, Decimal, Decimal, bool, datetime], ...]:
        _aware(as_of, "social_as_of")
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT status,sentiment,pump_and_dump_risk,"
                "price_sentiment_divergence,emerging_topic,evaluated_at "
                "FROM social_narrative_metric_windows WHERE instrument_id=%s "
                "AND window_end<=%s AND evaluated_at<=%s ORDER BY evaluated_at,window_id",
                (instrument_id, as_of, as_of),
            )
            return tuple(
                (
                    str(row[0]),
                    Decimal(str(row[1])),
                    Decimal(str(row[2])),
                    Decimal(str(row[3])),
                    bool(row[4]),
                    row[5],
                )
                for row in cursor.fetchall()
            )
