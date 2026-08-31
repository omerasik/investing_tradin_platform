import json
import unittest
from pathlib import Path


class NautilusStaticReviewTests(unittest.TestCase):
    def test_static_review_is_pinned_non_executing_and_defers_adoption(self) -> None:
        path = Path(__file__).resolve().parents[1] / "docs" / "upstream" / "nautilus_static_review_2026-08-31.json"
        review = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(review["candidate"]["name"], "nautilus_trader")
        self.assertEqual(review["candidate"]["license"], "LGPL-3.0-or-later")
        self.assertEqual(review["candidate"]["working_tree"], "CLEAN")
        self.assertRegex("".join(review["candidate"]["pinned_sha_fragments"]), r"^[0-9a-f]{40}$")
        self.assertIn("no candidate import", " ".join(review["restrictions"]))
        self.assertEqual(review["sast"]["status"], "COMPLETE")
        self.assertEqual(review["sast"]["findings"]["high"], 0)
        self.assertEqual(review["secret_scan"]["findings"], 0)
        self.assertEqual(review["lock_inventory"], {"python_packages": 143, "rust_packages": 908})
        self.assertEqual(review["sca"]["status"], "NOT_RUN_LOCKFILE_ONLY")
        self.assertEqual(review["decision"]["status"], "DEFER_REFERENCE_ONLY")


if __name__ == "__main__":
    unittest.main()
