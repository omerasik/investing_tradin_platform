import json
import unittest
from pathlib import Path


class UpstreamIdentityRefreshTests(unittest.TestCase):
    def test_snapshot_is_complete_pinned_and_non_executing(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "upstream"
            / "identity_activity_refresh_2026-08-31.json"
        )
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertIn("no fetch, checkout, install, build, test", snapshot["method"])
        repositories = snapshot["repositories"]
        self.assertEqual(len(repositories), 16)
        self.assertEqual(len({item["name"] for item in repositories}), 16)
        self.assertEqual({item["comparison"] for item in repositories}, {"ADVANCED", "CURRENT"})
        self.assertEqual(sum(item["comparison"] == "ADVANCED" for item in repositories), 12)
        self.assertEqual(sum(item["comparison"] == "CURRENT" for item in repositories), 4)
        for repository in repositories:
            self.assertRegex(repository["pinned_sha"], r"^[0-9a-f]{40}$")
            self.assertRegex(repository["remote_sha"], r"^[0-9a-f]{40}$")
            self.assertTrue(repository["github_repository"].count("/") == 1)
            if repository["comparison"] == "CURRENT":
                self.assertEqual(repository["pinned_sha"], repository["remote_sha"])
            else:
                self.assertNotEqual(repository["pinned_sha"], repository["remote_sha"])
        partial = [
            item for item in repositories if item["working_tree"] == "PARTIAL_WINDOWS_CHECKOUT"
        ]
        self.assertEqual([item["name"] for item in partial], ["machine-learning-for-trading"])


if __name__ == "__main__":
    unittest.main()
