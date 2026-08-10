import unittest
from unittest.mock import patch

from trade_platform.config import EnvironmentSecretResolver, LiveTradingForbiddenError, PlatformConfig, SecretReferenceError


class ConfigTests(unittest.TestCase):
    def test_default_mode_is_paper_only(self) -> None:
        config = PlatformConfig()
        self.assertTrue(config.paper_trading_enabled)
        self.assertFalse(config.live_trading_enabled)

    def test_live_mode_is_impossible(self) -> None:
        with self.assertRaises(LiveTradingForbiddenError):
            PlatformConfig(live_trading_enabled=True)

    def test_assessment_integrity_key_uses_explicit_environment_reference(self) -> None:
        config = PlatformConfig(assessment_integrity_key_reference="env:TRADE_PLATFORM_ASSESSMENT_KEY")
        with patch.dict("os.environ", {"TRADE_PLATFORM_ASSESSMENT_KEY": "test-key"}, clear=True):
            store = config.create_assessment_store(resolver=EnvironmentSecretResolver())
            self.assertTrue(store.keyed_integrity_enabled)
            store.close()

    def test_assessment_integrity_key_fails_closed_when_unavailable_or_invalid(self) -> None:
        with self.assertRaisesRegex(SecretReferenceError, "reference_required"):
            PlatformConfig().assessment_integrity_key()
        with self.assertRaisesRegex(SecretReferenceError, "invalid_secret_reference"):
            PlatformConfig(assessment_integrity_key_reference="literal-key").assessment_integrity_key()
        with self.assertRaisesRegex(SecretReferenceError, "unavailable"):
            PlatformConfig(assessment_integrity_key_reference="env:MISSING_KEY").assessment_integrity_key()


if __name__ == "__main__":
    unittest.main()
