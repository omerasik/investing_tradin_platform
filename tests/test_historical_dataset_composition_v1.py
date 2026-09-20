"""Unit tests for the Phase 3D.3 canonical dataset composition authority.

Fully in-memory: fake evidence and a fake sealing pipeline. No database, no
provider call. Real-PostgreSQL evidence is in
``test_historical_dataset_composition_v1_postgres``.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from trade_platform.bybit_crypto_provider import BYBIT_V5_SYMBOL_NAMESPACE
from trade_platform.bybit_instrument_onboarding import (
    BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    BYBIT_BTCUSDT_SYMBOL,
)
from trade_platform.historical_acquisition import (
    MaterializedFeatureCounts,
    SealedDatasetView,
    SourceProfile,
    expected_event_ats,
)
from trade_platform.historical_dataset_composition_v1 import (
    CompositionParent,
    DatasetCompositionError,
    DatasetCompositionRequest,
    HistoricalDatasetCompositionService,
    composed_dataset_version,
    composition_identity_hash,
)
from trade_platform.historical_market_data import (
    HistoricalDatasetVersion,
    HistoricalMarketDataError,
    ObservationKind,
)

KINDS = frozenset(
    {
        ObservationKind.OHLCV,
        ObservationKind.MARK_PRICE,
        ObservationKind.INDEX_PRICE,
        ObservationKind.OPEN_INTEREST,
    }
)
SOURCE_ID = UUID("00000000-0000-0000-0000-0000000000a1")
NORMALIZATION = "bybit-v5-md-test"
START = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)
NOW = datetime(2026, 9, 17, tzinfo=UTC)


def _view(
    dataset_version_id: UUID,
    content_hash: str,
    start: datetime,
    end: datetime,
    *,
    normalization: str = NORMALIZATION,
    instrument: str = BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
    symbol: str = BYBIT_BTCUSDT_SYMBOL,
    drop_one_ohlcv: bool = False,
    extra_revision: bool = False,
) -> SealedDatasetView:
    events = {kind: frozenset(expected_event_ats(kind, start, end)) for kind in KINDS}
    counts = {kind: len(ats) for kind, ats in events.items()}
    if drop_one_ohlcv:
        events[ObservationKind.OHLCV] = frozenset(sorted(events[ObservationKind.OHLCV])[1:])
        counts[ObservationKind.OHLCV] -= 1
    if extra_revision:
        counts[ObservationKind.OHLCV] += 1
    return SealedDatasetView(
        dataset_version_id=dataset_version_id,
        content_hash=content_hash,
        normalization_version=normalization,
        valid_from=start,
        valid_until=end,
        created_at=NOW - timedelta(hours=1),
        instrument_ids=frozenset({instrument}),
        provider_identifiers=frozenset({symbol}),
        provider_symbols=frozenset({symbol}),
        event_ats_by_kind=events,
        member_count_by_kind=counts,
    )


class FakeWorld:
    """In-memory evidence + pipeline; shares one dataset/member table."""

    def __init__(self) -> None:
        self.views: dict[str, SealedDatasetView] = {}
        self.members: dict[UUID, tuple[UUID, ...]] = {}
        self.seals: list[tuple[str, tuple[UUID, ...]]] = []
        self.profile = SourceProfile(
            provider="bybit",
            provider_identifier_namespace=BYBIT_V5_SYMBOL_NAMESPACE,
            asset_scope="CRYPTO",
            capabilities=KINDS,
        )
        self.resolved: tuple[str, ...] = (BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,)
        self.seal_error: Exception | None = None
        self.feature_counts_store: dict[UUID, int] = {}
        self.materialize_calls = 0

    # -- parent helpers
    def add_parent(self, index: int, **view_kwargs: object) -> CompositionParent:
        start = START + index * WINDOW
        end = start + WINDOW
        dataset_id = uuid4()
        version = f"parent-{index}"
        content_hash = f"hash-{index}"
        view = _view(dataset_id, content_hash, start, end, **view_kwargs)  # type: ignore[arg-type]
        self.views[version] = view
        self.members[dataset_id] = tuple(
            uuid4() for _ in range(sum(view.member_count_by_kind.values()))
        )
        return CompositionParent(dataset_id, version, content_hash, start, end)

    # -- CompositionEvidence
    def source_profile(self, source_id: UUID) -> SourceProfile | None:
        return self.profile if source_id == SOURCE_ID else None

    def resolve_instrument_ids(
        self, namespace: str, provider_symbol: str, known_at: datetime
    ) -> tuple[str, ...]:
        return self.resolved

    def crypto_specification(self, instrument_id: str, known_at: datetime) -> None:
        return None

    def existing_sealed_dataset(self, source_id: UUID, version: str) -> SealedDatasetView | None:
        return self.views.get(version) if source_id == SOURCE_ID else None

    def dataset_member_ids(self, dataset_version_id: UUID) -> tuple[UUID, ...]:
        return self.members[dataset_version_id]

    def feature_counts(
        self, dataset_version_id: UUID, feature_ids: tuple[UUID, ...]
    ) -> Mapping[UUID, int]:
        return {fid: self.feature_counts_store.get(fid, 0) for fid in feature_ids}

    def normalized_observation_for_raw(self, raw_observation_id: UUID) -> None:
        return None

    # -- pipeline
    def capture_raw(self, observations: list[object]) -> tuple[UUID, ...]:
        raise AssertionError("composition must never capture raw evidence")

    def normalize(self, *args: object) -> None:
        raise AssertionError("composition must never normalize")

    def seal_dataset(
        self,
        source_id: UUID,
        version: str,
        normalization_version: str,
        normalized_ids: tuple[UUID, ...],
        created_at: datetime,
    ) -> HistoricalDatasetVersion:
        if self.seal_error is not None:
            raise self.seal_error
        self.seals.append((version, normalized_ids))
        dataset_id = uuid4()
        # Reconstruct the target view from the parent views the ids came from.
        by_kind_events: dict[ObservationKind, set[datetime]] = {k: set() for k in KINDS}
        counts: dict[ObservationKind, int] = {k: 0 for k in KINDS}
        member_set = set(normalized_ids)
        for parent_id, parent_members in self.members.items():
            if member_set & set(parent_members):
                view = next(v for v in self.views.values() if v.dataset_version_id == parent_id)
                for kind in KINDS:
                    by_kind_events[kind] |= set(view.event_ats_by_kind[kind])
                    counts[kind] += view.member_count_by_kind[kind]
        starts = [v.valid_from for v in self.views.values()]
        self.views[version] = SealedDatasetView(
            dataset_version_id=dataset_id,
            content_hash="composed-hash",
            normalization_version=normalization_version,
            valid_from=min(starts),
            valid_until=max(v.valid_until for v in self.views.values()),
            created_at=created_at,
            instrument_ids=frozenset({BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID}),
            provider_identifiers=frozenset({BYBIT_BTCUSDT_SYMBOL}),
            provider_symbols=frozenset({BYBIT_BTCUSDT_SYMBOL}),
            event_ats_by_kind={k: frozenset(v) for k, v in by_kind_events.items()},
            member_count_by_kind=counts,
        )
        self.members[dataset_id] = tuple(normalized_ids)
        return HistoricalDatasetVersion(
            dataset_id, source_id, version, normalization_version, "composed-hash",
            self.views[version].valid_from, self.views[version].valid_until, created_at,
        )

    # -- feature materializer
    def resolve_basis_feature_id(self, at: datetime) -> UUID:
        return UUID(int=1)

    def resolve_open_interest_feature_id(self, at: datetime) -> UUID:
        return UUID(int=2)

    def materialize_features(self, **kwargs: object) -> MaterializedFeatureCounts:
        self.materialize_calls += 1
        basis = len(kwargs["basis_event_ats"])  # type: ignore[arg-type]
        oi = max(len(kwargs["open_interest_event_ats"]) - 1, 0)  # type: ignore[arg-type]
        self.feature_counts_store[UUID(int=1)] = basis
        self.feature_counts_store[UUID(int=2)] = oi
        return MaterializedFeatureCounts(basis, oi)


def _request(
    parents: tuple[CompositionParent, ...], *, features: bool = False
) -> DatasetCompositionRequest:
    return DatasetCompositionRequest(
        source_id=SOURCE_ID,
        instrument_id=BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,
        provider="bybit",
        provider_symbol=BYBIT_BTCUSDT_SYMBOL,
        normalization_version=NORMALIZATION,
        observation_kinds=KINDS,
        start=parents[0].start,
        end=parents[-1].end,
        parents=parents,
        materialize_features=features,
    )


def _service(world: FakeWorld) -> HistoricalDatasetCompositionService:
    return HistoricalDatasetCompositionService(
        evidence=world,  # type: ignore[arg-type]
        pipeline=world,  # type: ignore[arg-type]
        feature_materializer=world,  # type: ignore[arg-type]
        now=lambda: NOW,
    )


class CompositionIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = FakeWorld()
        self.parents = tuple(self.world.add_parent(i) for i in range(3))

    def test_target_version_is_deterministic_and_binds_window(self) -> None:
        request = _request(self.parents)
        version = composed_dataset_version(request)
        self.assertEqual(version, composed_dataset_version(_request(self.parents)))
        prefix, digest, start, end = version.split(":")
        self.assertEqual(prefix, "bybit-research-composite-v1")
        self.assertEqual(len(digest), 16)
        self.assertEqual(start, "20260916T000000Z")
        self.assertEqual(end, "20260916T003000Z")

    def test_features_flag_does_not_change_identity(self) -> None:
        self.assertEqual(
            composition_identity_hash(_request(self.parents)),
            composition_identity_hash(_request(self.parents, features=True)),
        )

    def test_every_bound_field_changes_identity(self) -> None:
        base = _request(self.parents)
        baseline = composition_identity_hash(base)
        changed_parent = replace(self.parents[1], content_hash="other")
        variants = [
            replace(base, normalization_version="other"),
            replace(base, observation_kinds=frozenset({ObservationKind.OHLCV})),
            replace(base, source_id=uuid4()),
            replace(base, parents=(self.parents[0], changed_parent, self.parents[2])),
            replace(base, parents=(self.parents[0], self.parents[1])),
        ]
        for variant in variants:
            self.assertNotEqual(baseline, composition_identity_hash(variant))


class CompositionComposeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = FakeWorld()
        self.parents = tuple(self.world.add_parent(i) for i in range(3))
        self.service = _service(self.world)

    def _refused(self, request: DatasetCompositionRequest, prefix: str) -> None:
        with self.assertRaises(DatasetCompositionError) as raised:
            self.service.compose(request)
        self.assertTrue(raised.exception.code.startswith(prefix), raised.exception.code)
        self.assertEqual(self.world.seals, [])

    def test_happy_path_seals_exactly_the_member_union_once(self) -> None:
        result = self.service.compose(_request(self.parents))
        self.assertFalse(result.already_completed)
        self.assertEqual(len(self.world.seals), 1)
        version, ids = self.world.seals[0]
        self.assertEqual(version, result.dataset_version)
        expected = {m for p in self.parents for m in self.world.members[p.dataset_version_id]}
        self.assertEqual(set(ids), expected)
        self.assertEqual(len(ids), len(expected))
        self.assertEqual(result.member_count_by_kind[ObservationKind.OHLCV], 30)
        self.assertEqual(result.member_count_by_kind[ObservationKind.OPEN_INTEREST], 6)
        self.assertEqual(
            result.parent_dataset_version_ids, tuple(p.dataset_version_id for p in self.parents)
        )

    def test_replay_returns_existing_without_resealing(self) -> None:
        first = self.service.compose(_request(self.parents))
        second = self.service.compose(_request(self.parents))
        self.assertTrue(second.already_completed)
        self.assertEqual(second.dataset_version_id, first.dataset_version_id)
        self.assertEqual(len(self.world.seals), 1)

    def test_target_version_with_different_semantics_is_a_conflict(self) -> None:
        request = _request(self.parents)
        target = composed_dataset_version(request)
        self.world.views[target] = _view(uuid4(), "squatter", START, START + WINDOW)
        self.world.members[self.world.views[target].dataset_version_id] = ()
        self._refused(request, "composition_version_conflict")

    def test_target_version_with_same_shape_but_other_members_is_a_conflict(self) -> None:
        request = _request(self.parents)
        self.service.compose(request)
        target = composed_dataset_version(request)
        stored = self.world.views[target]
        self.world.members[stored.dataset_version_id] = tuple(
            uuid4() for _ in self.world.members[stored.dataset_version_id]
        )
        with self.assertRaises(DatasetCompositionError) as raised:
            self.service.compose(request)
        self.assertTrue(raised.exception.code.startswith("composition_version_conflict"))

    def test_missing_parent(self) -> None:
        ghost = CompositionParent(
            uuid4(), "ghost", "h", self.parents[1].start, self.parents[1].end
        )
        self._refused(
            _request((self.parents[0], ghost, self.parents[2])), "parent_not_sealed_or_missing"
        )

    def test_wrong_parent_content_hash(self) -> None:
        bad = replace(self.parents[1], content_hash="tampered")
        self._refused(
            _request((self.parents[0], bad, self.parents[2])), "parent_content_hash_mismatch"
        )

    def test_wrong_parent_dataset_version_id(self) -> None:
        bad = replace(self.parents[1], dataset_version_id=uuid4())
        self._refused(
            _request((self.parents[0], bad, self.parents[2])), "parent_dataset_version_id_mismatch"
        )

    def test_wrong_normalization_parent(self) -> None:
        world = FakeWorld()
        parents = (world.add_parent(0), world.add_parent(1, normalization="other-norm"))
        with self.assertRaises(DatasetCompositionError) as raised:
            _service(world).compose(_request(parents))
        self.assertTrue(raised.exception.code.startswith("parent_identity_proof_failed"))

    def test_wrong_instrument_parent(self) -> None:
        world = FakeWorld()
        parents = (world.add_parent(0), world.add_parent(1, instrument="CRYPTO:BYBIT:ETHUSDT:PERP"))
        with self.assertRaises(DatasetCompositionError) as raised:
            _service(world).compose(_request(parents))
        self.assertTrue(raised.exception.code.startswith("parent_identity_proof_failed"))

    def test_wrong_provider_symbol_parent(self) -> None:
        world = FakeWorld()
        parents = (world.add_parent(0), world.add_parent(1, symbol="ETHUSDT"))
        with self.assertRaises(DatasetCompositionError) as raised:
            _service(world).compose(_request(parents))
        self.assertTrue(raised.exception.code.startswith("parent_identity_proof_failed"))

    def test_parent_event_set_mismatch(self) -> None:
        world = FakeWorld()
        parents = (world.add_parent(0), world.add_parent(1, drop_one_ohlcv=True))
        with self.assertRaises(DatasetCompositionError) as raised:
            _service(world).compose(_request(parents))
        self.assertTrue(raised.exception.code.startswith("parent_identity_proof_failed"))

    def test_extra_member_or_revision_in_parent(self) -> None:
        world = FakeWorld()
        parents = (world.add_parent(0), world.add_parent(1, extra_revision=True))
        with self.assertRaises(DatasetCompositionError) as raised:
            _service(world).compose(_request(parents))
        self.assertTrue(raised.exception.code.startswith("parent_identity_proof_failed"))

    def test_parent_gap(self) -> None:
        self._refused(
            replace(_request((self.parents[0], self.parents[2])), start=START), "parent_window_gap"
        )

    def test_parent_overlap(self) -> None:
        shifted = replace(self.parents[1], start=self.parents[1].start - timedelta(minutes=5))
        self._refused(
            _request((self.parents[0], shifted, self.parents[2])), "parent_window_overlap"
        )

    def test_unordered_parents(self) -> None:
        request = replace(
            _request(self.parents), parents=(self.parents[1], self.parents[0], self.parents[2])
        )
        self._refused(request, "parent_windows_unordered")

    def test_duplicate_parent(self) -> None:
        request = replace(
            _request(self.parents),
            parents=(self.parents[0], self.parents[1], self.parents[1], self.parents[2]),
        )
        self._refused(request, "duplicate_parent")

    def test_parents_must_tile_requested_window(self) -> None:
        request = replace(_request(self.parents), end=self.parents[-1].end + WINDOW)
        self._refused(request, "parents_do_not_cover_requested_window")

    def test_single_parent_refused(self) -> None:
        self._refused(_request((self.parents[0],)), "composition_requires_at_least_two_parents")

    def test_duplicate_normalized_member_across_parents(self) -> None:
        shared = self.world.members[self.parents[0].dataset_version_id][0]
        second = list(self.world.members[self.parents[1].dataset_version_id])
        second[0] = shared
        self.world.members[self.parents[1].dataset_version_id] = tuple(second)
        self._refused(_request(self.parents), "duplicate_member_across_parents")

    def test_member_count_inconsistent_with_view(self) -> None:
        pid = self.parents[1].dataset_version_id
        self.world.members[pid] = self.world.members[pid][:-1]
        self._refused(_request(self.parents), "parent_member_set_inconsistent")

    def test_unsupported_scope_is_refused(self) -> None:
        base = _request(self.parents)
        for variant, prefix in (
            (replace(base, provider="binance"), "unsupported_provider"),
            (replace(base, instrument_id="X"), "unsupported_instrument"),
            (replace(base, provider_symbol="X"), "unsupported_provider_symbol"),
            (
                replace(base, observation_kinds=frozenset({ObservationKind.FUNDING_RATE_REALIZED})),
                "unsupported_observation_kind",
            ),
            (replace(base, observation_kinds=frozenset()), "no_observation_kind_requested"),
        ):
            self._refused(variant, prefix)

    def test_source_and_symbol_identity_refused(self) -> None:
        self.world.resolved = ("CRYPTO:BYBIT:OTHER:PERP",)
        self._refused(_request(self.parents), "provider_symbol_instrument_mismatch")
        self.world.resolved = ()
        self._refused(_request(self.parents), "provider_symbol_unresolved")
        self.world.resolved = (BYBIT_BTCUSDT_PERPETUAL_INSTRUMENT_ID,)
        self.world.profile = replace(self.world.profile, provider="other")
        self._refused(_request(self.parents), "source_provider_mismatch")
        self.world.profile = replace(
            self.world.profile, provider="bybit", capabilities=frozenset({ObservationKind.OHLCV})
        )
        self._refused(_request(self.parents), "source_not_authorized_for_kind")
        self._refused(replace(_request(self.parents), source_id=uuid4()), "historical_source_not_found")

    def test_misaligned_open_interest_window_refused(self) -> None:
        parent = replace(self.parents[0], end=self.parents[0].end - timedelta(minutes=1))
        request = replace(_request((parent, self.parents[1])), end=self.parents[1].end)
        self._refused(request, "parent_span_not_open_interest_aligned")

    def test_clock_before_parent_seal_refused(self) -> None:
        early = HistoricalDatasetCompositionService(
            evidence=self.world,  # type: ignore[arg-type]
            pipeline=self.world,  # type: ignore[arg-type]
            feature_materializer=self.world,  # type: ignore[arg-type]
            now=lambda: NOW - timedelta(days=1),
        )
        with self.assertRaises(DatasetCompositionError) as raised:
            early.compose(_request(self.parents))
        self.assertEqual(raised.exception.code, "composition_clock_precedes_parent_seal")

    def test_seal_failure_is_reported_and_not_swallowed(self) -> None:
        self.world.seal_error = HistoricalMarketDataError("historical_dataset_seal_failed")
        self._refused(_request(self.parents), "composition_seal_failed")

    def test_post_seal_proof_failure_fails_closed(self) -> None:
        real_seal = self.world.seal_dataset

        def corrupt(*args: object) -> HistoricalDatasetVersion:
            result = real_seal(*args)  # type: ignore[arg-type]
            version = args[1]
            self.world.views[version] = replace(  # type: ignore[index]
                self.world.views[version],  # type: ignore[index]
                content_hash="x",
                member_count_by_kind={k: 0 for k in KINDS},
            )
            return result

        self.world.seal_dataset = corrupt  # type: ignore[method-assign]
        with self.assertRaises(DatasetCompositionError) as raised:
            self.service.compose(_request(self.parents))
        self.assertEqual(raised.exception.code, "post_seal_identity_proof_failed")

    def test_no_raw_capture_or_normalize_is_ever_called(self) -> None:
        # capture_raw / normalize on the fake raise AssertionError if touched.
        self.service.compose(_request(self.parents))

    def test_features_use_combined_window_expectation_and_replay_safely(self) -> None:
        first = self.service.compose(_request(self.parents, features=True))
        assert first.feature_counts is not None
        # 3 x 10 minutes: 30 basis events; 6 OI events -> 5 changes (not 3 x 1).
        self.assertEqual(first.feature_counts, MaterializedFeatureCounts(30, 5))
        self.assertEqual(self.world.materialize_calls, 1)
        again = self.service.compose(_request(self.parents, features=True))
        self.assertEqual(again.feature_counts, MaterializedFeatureCounts(30, 5))
        self.assertEqual(self.world.materialize_calls, 1)

    def test_feature_excess_fails_closed(self) -> None:
        self.service.compose(_request(self.parents))
        self.world.feature_counts_store[UUID(int=2)] = 99
        with self.assertRaises(DatasetCompositionError) as raised:
            self.service.compose(_request(self.parents, features=True))
        self.assertTrue(raised.exception.code.startswith("existing_feature_count_exceeds_expected"))


if __name__ == "__main__":
    unittest.main()
