import json
import unittest
from pathlib import Path


class BacktestingStaticReviewTests(unittest.TestCase):
    def test_static_review_is_pinned_non_executing_and_defers_adoption(self) -> None:
        path = Path(__file__).resolve().parents[1] / "docs" / "upstream" / "backtesting_static_review_2026-08-31.json"
        review = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(review["candidate"]["name"], "backtesting.py")
        self.assertEqual(review["candidate"]["license"], "AGPL-3.0-or-later")
        self.assertEqual(review["candidate"]["working_tree"], "CLEAN")
        self.assertRegex("".join(review["candidate"]["pinned_sha_fragments"]), r"^[0-9a-f]{40}$")
        self.assertIn("no candidate import", " ".join(review["restrictions"]))
        self.assertEqual(review["dependencies"]["exact_pinned_production_declarations"], 0)
        self.assertEqual(review["sast"]["findings"], {"total": 113, "high": 0, "medium": 0, "low": 113})
        self.assertEqual(review["sast"]["rules"], {"B101": 113})
        self.assertEqual(review["secret_scan"]["findings"], 0)
        self.assertEqual(review["sca"]["status"], "NOT_RUN_MANIFEST_ONLY")
        self.assertEqual(review["decision"]["status"], "DEFER_REFERENCE_ONLY")


if __name__ == "__main__":
    unittest.main()
