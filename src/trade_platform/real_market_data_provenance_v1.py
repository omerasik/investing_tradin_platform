"""Phase 3D.8A canonical real-market-data provenance authority.

**Positive proof only.** A historical dataset is ``REAL_DATA_RESEARCH_EVIDENCE``
only when persisted canonical lineage proves it, never because a provider
*name* looks right. ``historical_data_sources.provider`` (and the older
``datasets.provider``) is free text that any fixture can set to ``"bybit"``;
this authority never reads a provider string as authority. Real status
requires ALL of:

* the ``historical_dataset_versions`` row exists and is ``SEALED``;
* its ``source_id`` equals the deterministic canonical Bybit source identity
  (``uuid5`` derived by :mod:`trade_platform.bybit_instrument_onboarding`);
* the persisted ``historical_data_sources`` row and its
  ``historical_source_capabilities`` equal -- field by field -- the exact
  operator-authorized source contract that onboarding writes (provider,
  dataset, provider-identifier namespace, terms version, authorization
  reference, ``CRYPTO`` asset scope, the four public market-data kinds);
* complete member lineage: the dataset has members, and every member's
  normalized observation is ``VALIDATED`` under the dataset's normalization
  version and its raw observation belongs to that same canonical source.

The member check re-reads the invariant sealing already enforced
(``PostgresHistoricalMarketDataPipeline.seal_dataset`` refuses a member from
another source) in one aggregate pass -- no member provenance is copied into a
second registry. A composite built by
:mod:`trade_platform.historical_dataset_composition_v1` qualifies through the
same proof: it is sealed under the canonical source and every member is a
canonical-source observation of its sealed parents.

**Fail closed.** A source whose persisted provider, dataset name or
authorization reference carries a synthetic/demo/fixture marker is
``SYNTHETIC_ENGINEERING_EVIDENCE_ONLY``. Everything else that is not
positively proven -- unknown dataset, unsealed dataset, a different source id
(including a free-text ``"bybit"`` source), a source-contract or capability
mismatch, missing or foreign members -- is ``UNAVAILABLE``, with explicit
reasons. The absence of a synthetic marker is never proof of real data.

**No caller-controlled flag.** :class:`RealMarketDataProvenanceV1` has no
``is_real`` field to set: its status is derived by
:func:`evaluate_real_market_data_provenance_v1`, bound into a content hash
over every provenance-significant field, and instances can only be issued by
this module. :meth:`RealMarketDataProvenanceV1.is_proven_real` re-verifies that
hash, so an edited copy fails closed.

**Scope.** Provenance of historical market *data* only. Real OHLCV/mark/index/
open-interest history proves nothing about fill realism, top of book, funding
accounting or execution authority -- callers must keep those limitations.
Read-only; no provider, broker, account or order call.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from .bybit_instrument_onboarding import (
    bybit_authorized_historical_source,
    captured_btcusdt_snapshot_v1,
)
from .persistence import PostgresDatabase

PROVENANCE_SCHEMA_VERSION = "real-market-data-provenance-v1"

STATUS_REAL_DATA = "REAL_DATA_RESEARCH_EVIDENCE"
STATUS_SYNTHETIC = "SYNTHETIC_ENGINEERING_EVIDENCE_ONLY"
STATUS_UNAVAILABLE = "UNAVAILABLE"

#: Same markers as the operator dashboard's synthetic classification.
_SYNTHETIC_MARKERS = ("demo", "synthetic", "fixture", "module1b")

_NAMESPACE = uuid5(NAMESPACE_URL, "trade_platform.real_market_data_provenance_v1")

#: Only this module may issue a :class:`RealMarketDataProvenanceV1`.
_ISSUER = object()


class RealMarketDataProvenanceError(ValueError):
    pass


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _utc_text(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class CanonicalSourceContractV1:
    """The exact persisted source contract real data must come from."""

    source_id: UUID
    provider: str
    dataset_name: str
    provider_identifier_namespace: str
    provider_terms_version: str
    authorization_reference: str
    asset_scope: str
    observation_kinds: tuple[str, ...]

    def content_hash(self) -> str:
        return _sha256(
            {
                "source_id": str(self.source_id),
                "provider": self.provider,
                "dataset_name": self.dataset_name,
                "provider_identifier_namespace": self.provider_identifier_namespace,
                "provider_terms_version": self.provider_terms_version,
                "authorization_reference": self.authorization_reference,
                "asset_scope": self.asset_scope,
                "observation_kinds": list(self.observation_kinds),
            }
        )


def canonical_bybit_source_contract_v1() -> CanonicalSourceContractV1:
    """The operator-authorized public Bybit V5 source contract, from onboarding itself.

    Derived from the same captured, hash-pinned instrument snapshot and the same
    :func:`bybit_authorized_historical_source` onboarding writes, so the
    contract can never drift from what the canonical onboarding persists.
    ``authorized_at``/``created_at`` describe *when* the platform learned it
    and are deliberately not part of the contract.
    """
    snapshot = captured_btcusdt_snapshot_v1()
    source = bybit_authorized_historical_source(snapshot, snapshot.retrieved_at)
    return CanonicalSourceContractV1(
        source_id=source.source_id,
        provider=source.provider,
        dataset_name=source.dataset_name,
        provider_identifier_namespace=source.provider_identifier_namespace,
        provider_terms_version=source.provider_terms_version,
        authorization_reference=source.authorization_reference,
        asset_scope=source.asset_scope,
        observation_kinds=tuple(sorted(kind.value for kind in source.resolved_capabilities())),
    )


@dataclass(frozen=True, slots=True)
class PersistedSourceFactsV1:
    source_id: UUID
    provider: str
    dataset_name: str
    provider_identifier_namespace: str
    provider_terms_version: str
    authorization_reference: str
    asset_scope: str
    observation_kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatasetLineageFactsV1:
    """What the persisted authorities say about one dataset -- facts, not verdicts."""

    dataset_version_id: UUID
    found: bool
    status: str | None = None
    version: str | None = None
    normalization_version: str | None = None
    content_hash: str | None = None
    source_id: UUID | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    created_at: datetime | None = None
    source: PersistedSourceFactsV1 | None = None
    member_count: int = 0
    #: Members whose normalized observation is VALIDATED under the dataset's
    #: normalization version AND whose raw observation is from the dataset's source.
    lineage_complete_member_count: int = 0
    instrument_ids: tuple[str, ...] = ()
    member_count_by_kind: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class RealMarketDataProvenanceV1:
    """Derived, content-hashed provenance verdict for one historical dataset."""

    schema_version: str
    status: str
    reasons: tuple[str, ...]
    dataset_version_id: UUID
    dataset_version: str | None
    dataset_content_hash: str | None
    normalization_version: str | None
    valid_from: datetime | None
    valid_until: datetime | None
    dataset_created_at: datetime | None
    source_id: UUID | None
    source_contract_content_hash: str | None
    member_count: int
    instrument_ids: tuple[str, ...]
    member_count_by_kind: tuple[tuple[str, int], ...]
    content_hash: str
    evidence_id: UUID
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise RealMarketDataProvenanceError(
                "real_market_data_provenance_is_issued_only_by_its_authority"
            )

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reasons": list(self.reasons),
            "dataset_version_id": str(self.dataset_version_id),
            "dataset_version": self.dataset_version,
            "dataset_content_hash": self.dataset_content_hash,
            "normalization_version": self.normalization_version,
            # UTC-normalized: the identity never depends on a session time zone.
            "valid_from": _utc_text(self.valid_from),
            "valid_until": _utc_text(self.valid_until),
            "dataset_created_at": _utc_text(self.dataset_created_at),
            "source_id": None if self.source_id is None else str(self.source_id),
            "source_contract_content_hash": self.source_contract_content_hash,
            "member_count": self.member_count,
            "instrument_ids": list(self.instrument_ids),
            "member_count_by_kind": [list(item) for item in self.member_count_by_kind],
        }

    def integrity_verified(self) -> bool:
        return (
            self.content_hash == _sha256(self.identity_payload())
            and self.evidence_id == uuid5(_NAMESPACE, f"provenance:{self.content_hash}")
        )

    def is_proven_real(self) -> bool:
        """True only for an intact, positively proven real-data verdict."""
        return (
            self.integrity_verified()
            and self.schema_version == PROVENANCE_SCHEMA_VERSION
            and self.status == STATUS_REAL_DATA
            and not self.reasons
            and self.source_contract_content_hash is not None
            and self.dataset_content_hash is not None
            and self.member_count > 0
        )


def _issue(**values: Any) -> RealMarketDataProvenanceV1:
    draft = RealMarketDataProvenanceV1(
        **values, content_hash="", evidence_id=_NAMESPACE, _issuer=_ISSUER
    )
    content_hash = _sha256(draft.identity_payload())
    return RealMarketDataProvenanceV1(
        **values,
        content_hash=content_hash,
        evidence_id=uuid5(_NAMESPACE, f"provenance:{content_hash}"),
        _issuer=_ISSUER,
    )


def _has_synthetic_marker(source: PersistedSourceFactsV1) -> bool:
    texts = (source.provider, source.dataset_name, source.authorization_reference)
    return any(marker in text.casefold() for text in texts for marker in _SYNTHETIC_MARKERS)


def evaluate_real_market_data_provenance_v1(
    facts: DatasetLineageFactsV1,
    contract: CanonicalSourceContractV1 | None = None,
) -> RealMarketDataProvenanceV1:
    """Pure, fail-closed verdict over persisted facts. See the module docstring."""
    resolved = canonical_bybit_source_contract_v1() if contract is None else contract
    reasons: list[str] = []
    status = STATUS_UNAVAILABLE
    source = facts.source
    if not facts.found:
        reasons.append("dataset_not_found")
    elif source is not None and _has_synthetic_marker(source):
        status = STATUS_SYNTHETIC
        reasons.append("synthetic_source_marker")
    else:
        if facts.status != "SEALED":
            reasons.append("dataset_not_sealed")
        if source is None or facts.source_id is None:
            reasons.append("source_lineage_unresolved")
        else:
            if facts.source_id != resolved.source_id or source.source_id != resolved.source_id:
                reasons.append("source_id_not_canonical")
            for name in (
                "provider", "dataset_name", "provider_identifier_namespace",
                "provider_terms_version", "authorization_reference", "asset_scope",
                "observation_kinds",
            ):
                if getattr(source, name) != getattr(resolved, name):
                    reasons.append(f"source_contract_mismatch:{name}")
        if facts.member_count <= 0:
            reasons.append("dataset_has_no_members")
        elif facts.lineage_complete_member_count != facts.member_count:
            reasons.append("member_lineage_incomplete")
        if not facts.content_hash:
            reasons.append("dataset_content_hash_missing")
        if not reasons:
            status = STATUS_REAL_DATA
    proven_contract = status == STATUS_REAL_DATA
    return _issue(
        schema_version=PROVENANCE_SCHEMA_VERSION,
        status=status,
        reasons=tuple(reasons),
        dataset_version_id=facts.dataset_version_id,
        dataset_version=facts.version,
        dataset_content_hash=facts.content_hash,
        normalization_version=facts.normalization_version,
        valid_from=facts.valid_from,
        valid_until=facts.valid_until,
        dataset_created_at=facts.created_at,
        source_id=facts.source_id,
        source_contract_content_hash=resolved.content_hash() if proven_contract else None,
        member_count=facts.member_count,
        instrument_ids=facts.instrument_ids,
        member_count_by_kind=facts.member_count_by_kind,
    )


class PostgresRealMarketDataProvenanceAuthorityV1:
    """Reads persisted lineage facts (read-only) and issues the verdict."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def facts(self, dataset_version_id: UUID) -> DatasetLineageFactsV1:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT status, version, normalization_version, content_hash, source_id, "
                "valid_from, valid_until, created_at FROM historical_dataset_versions "
                "WHERE dataset_version_id=%s",
                (dataset_version_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return DatasetLineageFactsV1(dataset_version_id=dataset_version_id, found=False)
            source_id = UUID(str(row[4]))
            normalization_version = str(row[2])
            cursor.execute(
                "SELECT provider, dataset_name, provider_identifier_namespace, "
                "provider_terms_version, authorization_reference, asset_scope "
                "FROM historical_data_sources WHERE source_id=%s",
                (source_id,),
            )
            source_row = cursor.fetchone()
            source: PersistedSourceFactsV1 | None = None
            if source_row is not None:
                cursor.execute(
                    "SELECT observation_kind FROM historical_source_capabilities "
                    "WHERE source_id=%s ORDER BY observation_kind",
                    (source_id,),
                )
                kinds = tuple(sorted(str(item[0]) for item in cursor.fetchall()))
                source = PersistedSourceFactsV1(
                    source_id, str(source_row[0]), str(source_row[1]), str(source_row[2]),
                    str(source_row[3]), str(source_row[4]), str(source_row[5]), kinds,
                )
            cursor.execute(
                "SELECT r.observation_kind, n.instrument_id, COUNT(*), "
                "COUNT(*) FILTER (WHERE r.source_id=%s AND n.quality_status='VALIDATED' "
                "AND n.normalization_version=%s) "
                "FROM historical_dataset_members m "
                "LEFT JOIN historical_normalized_observations n "
                "ON n.normalized_observation_id=m.normalized_observation_id "
                "LEFT JOIN historical_raw_observations r ON r.raw_observation_id=n.raw_observation_id "
                "WHERE m.dataset_version_id=%s GROUP BY r.observation_kind, n.instrument_id",
                (source_id, normalization_version, dataset_version_id),
            )
            groups = cursor.fetchall()
        by_kind: dict[str, int] = {}
        instruments: set[str] = set()
        member_count = 0
        complete = 0
        for kind, instrument_id, count, lineage_count in groups:
            member_count += int(count)
            complete += int(lineage_count)
            by_kind[str(kind)] = by_kind.get(str(kind), 0) + int(count)
            instruments.add(str(instrument_id))
        return DatasetLineageFactsV1(
            dataset_version_id=dataset_version_id,
            found=True,
            status=str(row[0]),
            version=str(row[1]),
            normalization_version=normalization_version,
            content_hash=str(row[3]).strip(),
            source_id=source_id,
            valid_from=row[5],
            valid_until=row[6],
            created_at=row[7],
            source=source,
            member_count=member_count,
            lineage_complete_member_count=complete,
            instrument_ids=tuple(sorted(instruments)),
            member_count_by_kind=tuple(sorted(by_kind.items())),
        )

    def prove(self, dataset_version_id: UUID) -> RealMarketDataProvenanceV1:
        return evaluate_real_market_data_provenance_v1(self.facts(dataset_version_id))
