import hashlib
import json
import unittest
from pathlib import Path


class QlibStaticReviewTests(unittest.TestCase):
    def test_static_review_is_pinned_non_executing_and_defers_adoption(self) -> None:
        upstream = Path(__file__).resolve().parents[1] / "docs" / "upstream"
        review_path = upstream / "qlib_static_review_2026-08-31.json"
        sbom_path = upstream / "qlib_declared_dependencies_2026-08-31.cdx.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))

        self.assertEqual(review["schema_version"], 1)
        self.assertEqual(review["candidate"]["name"], "qlib")
        self.assertEqual(review["candidate"]["license"], "MIT")
        self.assertEqual(review["candidate"]["working_tree"], "CLEAN")
        pinned_sha = "".join(review["candidate"]["pinned_sha_fragments"])
        self.assertRegex(pinned_sha, r"^[0-9a-f]{40}$")
        for fragments in review["input_sha256_fragments"].values():
            self.assertEqual(len(fragments), 8)
            self.assertTrue(all(len(fragment) == 8 for fragment in fragments))
            self.assertRegex("".join(fragments), r"^[0-9a-f]{64}$")
        self.assertIn("no candidate import", " ".join(review["restrictions"]))
        self.assertEqual(review["sast"]["findings"]["total"], 477)
        self.assertEqual(review["sast"]["findings"]["high"], 18)
        self.assertEqual(review["sast"]["findings"]["high_by_rule"], {"B324": 1, "B602": 5, "B605": 12})
        self.assertEqual(review["secret_scan"]["findings"], 0)
        self.assertEqual(review["sbom"]["direct_declared_components"], 23)
        self.assertEqual(review["sbom"]["exact_pinned_components"], 0)
        self.assertEqual(review["sca"]["status"], "NOT_RUN_UNPINNED_DECLARATIONS")
        self.assertEqual(review["decision"]["status"], "DEFER_REFERENCE_ONLY")
        self.assertEqual(
            hashlib.sha256(sbom_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "".join(review["sbom"]["artifact_sha256_fragments"]),
        )
        self.assertEqual(review["sbom"]["artifact_hash_canonicalization"], "LF line endings")
        self.assertEqual(sbom["specVersion"], "1.5")
        self.assertEqual(sbom["metadata"]["component"]["name"], "pyqlib")
        self.assertEqual(len(sbom["components"]), 23)


if __name__ == "__main__":
    unittest.main()
