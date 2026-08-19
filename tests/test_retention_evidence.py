import hashlib
import unittest
from datetime import UTC, datetime, timedelta

from trade_platform.retention_evidence import (
    ObjectEvidenceKind,
    RetentionClassification,
    RetentionDisposition,
    RetentionEvidenceError,
    build_object_manifest,
    build_retention_policy,
    evaluate_retention,
)


class RetentionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 19, 12, tzinfo=UTC)
        self.policy = build_retention_policy(
            policy_name="database-backup-retention",
            version="v1",
            classification=RetentionClassification.BACKUP,
            retention=timedelta(days=30),
            legal_hold=False,
            owner="paper-operations",
            approved_by="operator",
            approved_at=self.now,
        )
        self.manifest = build_object_manifest(
            object_reference="backups/postgres/2026-08-19.dump",
            object_kind=ObjectEvidenceKind.DATABASE_BACKUP,
            media_type="application/octet-stream",
            byte_size=1234,
            sha256=hashlib.sha256(b"deterministic-backup-fixture").hexdigest(),
            source_reference="postgres/primary-paper",
            policy_id=self.policy.policy_id,
            captured_at=self.now,
        )

    def test_active_window_retains_and_elapsed_window_only_requests_review(self) -> None:
        active = evaluate_retention(
            self.policy,
            self.manifest,
            evaluated_at=self.now + timedelta(days=29),
            idempotency_key="backup:day29",
        )
        elapsed = evaluate_retention(
            self.policy,
            self.manifest,
            evaluated_at=self.now + timedelta(days=30),
            idempotency_key="backup:day30",
        )
        self.assertEqual(active.disposition, RetentionDisposition.RETAIN)
        self.assertEqual(elapsed.disposition, RetentionDisposition.ELIGIBLE_FOR_REVIEW)
        self.assertEqual(elapsed.reason, "RETENTION_WINDOW_ELAPSED_REVIEW_REQUIRED")

    def test_legal_hold_and_disabled_policy_both_retain(self) -> None:
        held = build_retention_policy(
            policy_name="held-audit-evidence",
            version="v1",
            classification=RetentionClassification.AUDIT_EVIDENCE,
            retention=timedelta(days=1),
            legal_hold=True,
            owner="compliance",
            approved_by="operator",
            approved_at=self.now,
        )
        manifest = build_object_manifest(
            object_reference="audit/held/export.json",
            object_kind=ObjectEvidenceKind.AUDIT_EXPORT,
            media_type="application/json",
            byte_size=2,
            sha256=hashlib.sha256(b"{}").hexdigest(),
            source_reference="audit/runtime",
            policy_id=held.policy_id,
            captured_at=self.now,
        )
        result = evaluate_retention(
            held,
            manifest,
            evaluated_at=self.now + timedelta(days=100),
            idempotency_key="held:day100",
        )
        self.assertEqual(result.disposition, RetentionDisposition.RETAIN)
        self.assertEqual(result.reason, "LEGAL_HOLD")
        disabled = build_retention_policy(
            policy_name="disabled-backup-policy",
            version="v1",
            classification=RetentionClassification.BACKUP,
            retention=timedelta(days=1),
            legal_hold=False,
            owner="paper-operations",
            approved_by="operator",
            approved_at=self.now,
            enabled=False,
        )
        disabled_manifest = build_object_manifest(
            object_reference="backups/disabled/fixture.dump",
            object_kind=ObjectEvidenceKind.DATABASE_BACKUP,
            media_type="application/octet-stream",
            byte_size=1,
            sha256="0" * 64,
            source_reference="postgres/disabled-paper",
            policy_id=disabled.policy_id,
            captured_at=self.now,
        )
        disabled_result = evaluate_retention(
            disabled,
            disabled_manifest,
            evaluated_at=self.now + timedelta(days=100),
            idempotency_key="disabled:day100",
        )
        self.assertEqual(disabled_result.disposition, RetentionDisposition.RETAIN)
        self.assertEqual(disabled_result.reason, "POLICY_DISABLED")

    def test_manifest_is_hash_only_and_rejects_endpoint_or_path_traversal(self) -> None:
        self.assertEqual(self.manifest.storage_state, "MANIFEST_ONLY")
        self.assertEqual(len(self.manifest.content_hash), 64)
        for reference in ("https://storage.invalid/item", "backups/../secret"):
            with self.subTest(reference=reference), self.assertRaisesRegex(
                RetentionEvidenceError, "invalid_object_manifest"
            ):
                build_object_manifest(
                    object_reference=reference,
                    object_kind=ObjectEvidenceKind.DATABASE_BACKUP,
                    media_type="application/octet-stream",
                    byte_size=1,
                    sha256="0" * 64,
                    source_reference="postgres/primary-paper",
                    policy_id=self.policy.policy_id,
                    captured_at=self.now,
                )

    def test_policy_manifest_and_time_must_align(self) -> None:
        other = build_retention_policy(
            policy_name="other",
            version="v1",
            classification=RetentionClassification.BACKUP,
            retention=timedelta(days=1),
            legal_hold=False,
            owner="ops",
            approved_by="operator",
            approved_at=self.now,
        )
        with self.assertRaisesRegex(
            RetentionEvidenceError, "retention_policy_manifest_mismatch"
        ):
            evaluate_retention(
                other,
                self.manifest,
                evaluated_at=self.now + timedelta(days=1),
                idempotency_key="mismatch",
            )
        with self.assertRaisesRegex(RetentionEvidenceError, "invalid_retention_evaluation"):
            evaluate_retention(
                self.policy,
                self.manifest,
                evaluated_at=self.now - timedelta(seconds=1),
                idempotency_key="past",
            )


if __name__ == "__main__":
    unittest.main()
