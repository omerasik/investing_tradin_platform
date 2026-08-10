from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from trade_platform.backup_recovery import BackupRecoveryError, SQLiteBackupService
from trade_platform.domain import OrderIntent, OrderSide
from trade_platform.paper_execution import PaperOrder
from trade_platform.paper_oms import SQLitePaperOms


class BackupRecoveryTests(unittest.TestCase):
    def test_online_backup_verification_and_non_overwriting_restore_preserve_oms_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory); source = root / "paper.sqlite"
            oms = SQLitePaperOms(source)
            intent = OrderIntent(uuid4(), uuid4(), "US:NYSE:SPY", "paper", OrderSide.BUY, Decimal("10"), Decimal("100"), datetime(2025, 1, 2, tzinfo=timezone.utc))
            oms.create(PaperOrder(intent)); oms.close()
            service = SQLiteBackupService(); artifact = service.create(source, root / "backups")
            service.verify(artifact)
            restored_path = service.restore(artifact, root / "restored.sqlite")
            restored = SQLitePaperOms(restored_path)
            self.assertEqual(restored.get(intent.intent_id).intent, intent)
            restored.close()
            with self.assertRaisesRegex(BackupRecoveryError, "destination"):
                service.restore(artifact, restored_path)

    def test_checksum_tampering_blocks_restore(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory); source = root / "empty.sqlite"; SQLitePaperOms(source).close()
            artifact = SQLiteBackupService().create(source, root / "backups")
            Path(artifact.backup_path).write_bytes(b"not a database")
            with self.assertRaisesRegex(BackupRecoveryError, "checksum"):
                SQLiteBackupService().restore(artifact, root / "restored.sqlite")


if __name__ == "__main__":
    unittest.main()
