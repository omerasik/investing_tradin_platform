import os
import tempfile
import unittest
from pathlib import Path

from trade_platform.secrets_manager import (
    EnvironmentSecretProvider,
    FileSecretProvider,
    SecretUnavailableError,
)


class EnvironmentSecretProviderTests(unittest.TestCase):
    def test_not_production_capable(self) -> None:
        self.assertFalse(EnvironmentSecretProvider().is_production_capable)

    def test_resolves_prefixed_environment_variable(self) -> None:
        os.environ["TRADE_PLATFORM_SECRET_UNIT_TEST_TOKEN"] = "shh"  # pragma: allowlist secret
        try:
            self.assertEqual(
                EnvironmentSecretProvider().get_secret("UNIT_TEST_TOKEN"), "shh"
            )
        finally:
            del os.environ["TRADE_PLATFORM_SECRET_UNIT_TEST_TOKEN"]

    def test_missing_or_empty_secret_fails_closed(self) -> None:
        with self.assertRaises(SecretUnavailableError):
            EnvironmentSecretProvider().get_secret("DOES_NOT_EXIST_UNIT_TEST")

    def test_invalid_secret_name_rejected(self) -> None:
        with self.assertRaises(SecretUnavailableError):
            EnvironmentSecretProvider().get_secret("../etc/passwd")


@unittest.skipUnless(os.name == "posix", "file permission enforcement is POSIX-only")
class FileSecretProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _write_secret(self, name: str, value: str, *, mode: int = 0o600) -> Path:
        path = self.directory / name
        path.write_text(value, encoding="utf-8")
        path.chmod(mode)
        return path

    def test_is_production_capable(self) -> None:
        self.assertTrue(FileSecretProvider(self.directory).is_production_capable)

    def test_missing_directory_fails_closed(self) -> None:
        with self.assertRaises(SecretUnavailableError):
            FileSecretProvider(self.directory / "does-not-exist")

    def test_reads_secret_file_with_safe_permissions(self) -> None:
        self._write_secret("DB_PASSWORD", "correct-horse-battery-staple\n")  # pragma: allowlist secret
        provider = FileSecretProvider(self.directory)
        self.assertEqual(provider.get_secret("DB_PASSWORD"), "correct-horse-battery-staple")

    def test_missing_secret_file_fails_closed(self) -> None:
        provider = FileSecretProvider(self.directory)
        with self.assertRaises(SecretUnavailableError):
            provider.get_secret("MISSING")

    def test_empty_secret_file_fails_closed(self) -> None:
        self._write_secret("EMPTY", "   \n")
        provider = FileSecretProvider(self.directory)
        with self.assertRaises(SecretUnavailableError):
            provider.get_secret("EMPTY")

    def test_group_or_world_readable_secret_file_fails_closed(self) -> None:
        self._write_secret("TOO_OPEN", "value", mode=0o644)  # pragma: allowlist secret
        provider = FileSecretProvider(self.directory)
        with self.assertRaises(SecretUnavailableError):
            provider.get_secret("TOO_OPEN")

    def test_invalid_secret_name_rejected(self) -> None:
        provider = FileSecretProvider(self.directory)
        with self.assertRaises(SecretUnavailableError):
            provider.get_secret("../escape")

    def test_rotation_timestamp_reflects_file_mtime(self) -> None:
        path = self._write_secret("ROTATING", "v1")  # pragma: allowlist secret
        provider = FileSecretProvider(self.directory)
        first = provider.rotation_timestamp("ROTATING")
        os.utime(path, (first + 10, first + 10))
        second = provider.rotation_timestamp("ROTATING")
        self.assertGreater(second, first)

    def test_rotation_timestamp_missing_file_fails_closed(self) -> None:
        provider = FileSecretProvider(self.directory)
        with self.assertRaises(SecretUnavailableError):
            provider.rotation_timestamp("MISSING")


if __name__ == "__main__":
    unittest.main()
