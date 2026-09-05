import ast
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from trade_platform.config import (
    EnvironmentSecretResolver,
    LiveTradingForbiddenError,
    PersistenceTarget,
    PlatformConfig,
    SecretReferenceError,
)

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trade_platform"

# Any of these appearing as an environment-variable *name* read anywhere in
# the backend source would be a silent alias capable of enabling live
# trading outside the single, explicitly-forbidden `live_trading_enabled`
# constructor argument. None of these should ever exist.
_FORBIDDEN_ENV_ALIAS_PATTERN = re.compile(
    r"(LIVE_TRADING|LIVE_MODE|ENABLE_LIVE|ALLOW_LIVE|LIVE_EXECUTION)",
    re.IGNORECASE,
)


class ConfigTests(unittest.TestCase):
    def test_default_mode_is_paper_only(self) -> None:
        config = PlatformConfig()
        self.assertTrue(config.paper_trading_enabled)
        self.assertFalse(config.live_trading_enabled)

    def test_live_mode_is_impossible(self) -> None:
        with self.assertRaises(LiveTradingForbiddenError):
            PlatformConfig(live_trading_enabled=True)

    def test_live_trading_defaults_disabled_for_every_environment_shape(self) -> None:
        """No environment name (including production/staging-shaped ones) can
        cause `live_trading_enabled` to default to True; it is always False
        unless explicitly (and unsuccessfully, per test_live_mode_is_impossible)
        overridden."""
        for environment in ("local_research", "paper", "production", "staging"):
            config = PlatformConfig(
                environment=environment,
                persistence_target=PersistenceTarget.POSTGRES,
                persistence_location="postgresql://user:pass@localhost/db",  # pragma: allowlist secret
            )
            self.assertFalse(
                config.live_trading_enabled,
                f"live_trading_enabled must default to False for environment={environment!r}",
            )

    def test_no_environment_variable_alias_can_enable_live_trading(self) -> None:
        """Guards against a future regression that reads an env var (under any
        plausible alias name) into live_trading_enabled. Today, no environment
        variable feeds this field at all -- it is a hardcoded, unconditionally
        rejected constructor argument. This test fails loudly if that ever
        changes without an explicit, reviewed update to this guard."""
        offending: list[str] = []
        for path in _SRC_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"os\.(?:environ(?:\.get)?|getenv)\(\s*[\"']([A-Za-z0-9_]+)[\"']", text):
                env_name = match.group(1)
                if _FORBIDDEN_ENV_ALIAS_PATTERN.search(env_name):
                    offending.append(f"{path.relative_to(_SRC_ROOT.parent.parent)}: {env_name}")
        self.assertEqual(
            offending,
            [],
            "Found environment variable(s) that look like a live-trading toggle alias: "
            f"{offending}. Live trading must remain hardcoded-disabled with no env-driven path.",
        )

    def test_platform_config_call_sites_never_pass_a_dynamic_live_trading_flag(self) -> None:
        """Every construction of PlatformConfig in the backend source must
        either omit `live_trading_enabled` or pass the literal `False` --
        never a variable, env lookup, or other expression that could be
        swayed by configuration/environment."""
        offending: list[str] = []
        for path in _SRC_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "PlatformConfig"):
                    continue
                for kw in node.keywords:
                    if kw.arg != "live_trading_enabled":
                        continue
                    is_literal_false = isinstance(kw.value, ast.Constant) and kw.value.value is False
                    if not is_literal_false:
                        offending.append(f"{path.relative_to(_SRC_ROOT.parent.parent)}:{node.lineno}")
        self.assertEqual(
            offending,
            [],
            f"PlatformConfig(live_trading_enabled=...) must only ever be omitted or literal False: {offending}",
        )

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
