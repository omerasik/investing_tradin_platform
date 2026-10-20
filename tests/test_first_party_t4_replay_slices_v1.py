"""Explicit replay selections rederive their complete canonical parent from raw evidence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from tests.test_first_party_t4_v1 import BASE, SECOND, _standard_samples, _TempRoots
from trade_platform.first_party_t4_dataset_v1 import CataloguedT4DatasetV1, t4_seal_from_catalog_v1
from trade_platform.first_party_t4_normalization_v1 import sha256_json_v1
from trade_platform.first_party_t4_seal_v1 import (
    FirstPartyT4SealError,
    discover_t4_segments_v1,
    plan_t4_replay_slice_v1,
    seal_t4_segment_v1,
    verify_t4_dataset_v1,
)

SEALED_AT = datetime(2026, 9, 21, tzinfo=UTC)


class ReplaySliceProofTests(_TempRoots):
    def setUp(self):
        super().setUp()
        self.archive.session(windows=[(BASE, BASE + 240 * SECOND)], samples=_standard_samples())
        self.parent_plan = discover_t4_segments_v1(self.capture).segments[0]
        self.parent = seal_t4_segment_v1(self.parent_plan, store=self.store(), sealed_at=SEALED_AT)

    def plan(self, **changes):
        arguments = {"start_arrival_nanos": BASE, "end_arrival_nanos_inclusive": BASE + 150 * SECOND}
        arguments.update(changes)
        return plan_t4_replay_slice_v1(self.parent.identity, capture_root=self.capture, **arguments)

    def seal(self):
        return seal_t4_segment_v1(self.plan(), store=self.store(), sealed_at=SEALED_AT)

    def verify(self, seal, *, identity=None, **changes):
        arguments = {"frame_manifests": seal.frame_manifests, "sealed_at": seal.sealed_at,
                     "store": self.store(), "capture_root": self.capture}
        arguments.update(changes)
        return verify_t4_dataset_v1(seal.identity if identity is None else identity, **arguments)

    def test_catalogue_roundtrip_rebuilds_parent_and_selected_minimal_clocks(self):
        slice_plan = self.plan()
        self.assertLess(len(slice_plan.clock.samples), len(self.parent_plan.clock.samples))
        seal = self.seal()
        row = CataloguedT4DatasetV1(seal.dataset_version_id, seal.content_hash, seal.identity,
                                    seal.frame_manifests, seal.sealed_at)
        restored = t4_seal_from_catalog_v1(row, store=self.store(), capture_root=self.capture)
        self.assertEqual(seal.content_hash, restored.content_hash)
        self.assertTrue(restored.raw_replayed)
        self.assertTrue(restored.integrity_verified())
        self.assertEqual(self.parent.content_hash, restored.identity["replay_slice_proof"]["parent_content_hash"])

    def test_ordinary_arbitrary_subrange_still_refuses(self):
        forged = seal_t4_segment_v1(replace(self.parent_plan, end_arrival_nanos=BASE + 150 * SECOND),
                                    store=self.store(), sealed_at=SEALED_AT)
        with self.assertRaises(FirstPartyT4SealError):
            self.verify(forged)

    def test_outside_full_and_reversed_ranges_refuse(self):
        for low, high in ((BASE - 1, BASE + 150 * SECOND), (BASE, BASE + 241 * SECOND),
                          (BASE, BASE + 240 * SECOND), (BASE + 200 * SECOND, BASE + 150 * SECOND)):
            with self.assertRaisesRegex(FirstPartyT4SealError, "strict_contained_parent_range"):
                self.plan(start_arrival_nanos=low, end_arrival_nanos_inclusive=high)

    def test_nested_slice_refuses(self):
        seal = self.seal()
        with self.assertRaisesRegex(FirstPartyT4SealError, "nested_replay_slices"):
            plan_t4_replay_slice_v1(seal.identity, start_arrival_nanos=BASE,
                                   end_arrival_nanos_inclusive=BASE + 100 * SECOND, capture_root=self.capture)

    def test_rehashed_parent_frame_counter_contract_or_clock_tampering_refuses(self):
        seal = self.seal()
        for field in ("frames", "counts", "contract_content_hash", "clock_evidence", "partitions"):
            with self.subTest(field=field):
                identity = deepcopy(seal.identity)
                parent = identity["replay_slice_proof"]["parent_identity"]
                if field == "frames":
                    next(iter(parent[field].values()))["logical_content_hash"] = "f" * 64
                elif field == "counts":
                    key = next(iter(parent[field]))
                    parent[field][key] += 1
                elif field == "clock_evidence":
                    parent[field]["sample_hashes"][0] = "f" * 64
                elif field == "partitions":
                    parent[field][0]["manifest_content_hash"] = "f" * 64
                else:
                    parent[field] = "f" * 64
                identity["replay_slice_proof"]["parent_content_hash"] = sha256_json_v1(parent)
                with self.assertRaises(FirstPartyT4SealError):
                    self.verify(seal, identity=identity)

    def test_slice_proof_range_clock_hash_and_available_time_tampering_refuse(self):
        seal = self.seal()
        variants = []
        for field, value in (("parent_content_hash", "f" * 64), ("parent_proof_available_at_nanos", 0),
                             ("schema_version", "unknown")):
            identity = deepcopy(seal.identity)
            identity["replay_slice_proof"][field] = value
            variants.append(identity)
        identity = deepcopy(seal.identity)
        identity["segment"]["end_arrival_nanos_inclusive"] = BASE + 241 * SECOND
        variants.append(identity)
        identity = deepcopy(seal.identity)
        identity["clock_evidence"]["sample_hashes"][0] = "f" * 64
        variants.append(identity)
        for identity in variants:
            with self.assertRaises(FirstPartyT4SealError):
                self.verify(seal, identity=identity)

    def test_slice_stored_frames_must_equal_exact_raw_rebuild(self):
        seal = self.seal()
        with self.assertRaisesRegex(FirstPartyT4SealError, "sealed_frame_disagrees_with_rebuild"):
            self.verify(seal, frame_manifests=self.parent.frame_manifests)

    def test_slice_cannot_be_sealed_before_parent_records_or_proof_samples(self):
        early = datetime.fromtimestamp((BASE + 170 * SECOND) // SECOND, tz=UTC)
        with self.assertRaisesRegex(FirstPartyT4SealError, "sealed_before_parent_evidence"):
            seal_t4_segment_v1(self.plan(), store=self.store(), sealed_at=early)
        seal = self.seal()
        with self.assertRaisesRegex(FirstPartyT4SealError, "sealed_before_parent_evidence"):
            self.verify(seal, sealed_at=early)
        available = seal.identity["replay_slice_proof"]["parent_proof_available_at_nanos"]
        # A seal after the parent's last record but before its closing bracket
        # sample is still too early.
        sample_early = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=(available - 1000) // 1000)
        with self.assertRaisesRegex(FirstPartyT4SealError, "sealed_before_parent_evidence"):
            self.verify(seal, sealed_at=sample_early)
