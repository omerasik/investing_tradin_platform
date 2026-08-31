import json
import unittest
from pathlib import Path


class FinrlStaticReviewTests(unittest.TestCase):
    def test_static_review_is_pinned_non_executing_and_defers_adoption(self) -> None:
        review_path = Path(__file__).resolve().parents[1] / "docs" / "upstream" / "finrl_static_review_2026-08-31.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))

        self.assertEqual(review["schema_version"], 1)
        self.assertEqual(review["candidate"]["name"], "FinRL-Trading")
        self.assertEqual(review["candidate"]["license"], "Apache-2.0")
        self.assertEqual(review["candidate"]["working_tree"], "CLEAN")
        self.assertRegex("".join(review["candidate"]["pinned_sha_fragments"]), r"^[0-9a-f]{40}$")
        for fragments in review["input_sha256_fragments"].values():
            self.assertEqual(len(fragments), 8)
            self.assertRegex("".join(fragments), r"^[0-9a-f]{64}$")
        self.assertIn("no candidate import", " ".join(review["restrictions"]))
        self.assertEqual(review["sast"]["status"], "INCOMPLETE_FILE_DECODE_ERROR")
        self.assertEqual(review["sast"]["findings"]["high"], 0)
        self.assertEqual(review["secret_scan"]["findings"], 0)
        self.assertEqual(review["sbom"]["direct_declared_components"], 25)
        self.assertEqual(review["sbom"]["exact_pinned_components"], 0)
        self.assertEqual(review["sca"]["status"], "NOT_RUN_UNPINNED_DECLARATIONS")
        self.assertEqual(review["decision"]["status"], "DEFER_REFERENCE_ONLY")


if __name__ == "__main__":
    unittest.main()
