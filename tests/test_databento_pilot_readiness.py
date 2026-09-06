import os
import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from trade_platform.data_providers import HttpResponse, ProviderConfiguration
from trade_platform.databento_pilot_readiness import (
    ActivationAttestation,
    ActivationChecklistCode,
    DatabentoPilotConfiguration,
    PilotReadinessError,
    assess_pilot_readiness,
)
from trade_platform.databento_provider import plan_chunks
from trade_platform.historical_market_data import AuthorizedHistoricalSource

SECRET_ENV_VAR = "DATABENTO_PILOT_READINESS_TEST_KEY"  # pragma: allowlist secret

_FULL_ATTESTATION = ActivationAttestation(confirmed=frozenset(ActivationChecklistCode))


class NetworkCallForbiddenTransport:
    """Fails loudly the instant either method is called -- proves the dry run never
    reaches the network, rather than merely asserting a call counter afterward."""

    def post(self, url: str, form: dict[str, str], api_key: str, timeout_seconds: float) -> HttpResponse:
        raise AssertionError("pilot readiness assessment must never make a network call (post)")

    def get(self, url: str, api_key: str, timeout_seconds: float) -> HttpResponse:
        raise AssertionError("pilot readiness assessment must never make a network call (get)")


def _source(**overrides: object) -> AuthorizedHistoricalSource:
    from datetime import UTC, datetime

    registered_at = datetime(2026, 1, 1, tzinfo=UTC)
    defaults: dict[str, object] = {
        "provider": "databento",
        "dataset_name": "databento-us-equities-pilot",
        "provider_identifier_namespace": "DATABENTO:INSTRUMENT_ID",
        "provider_terms_version": "databento-terms-v1",
        "authorization_reference": "test-authorization://pilot-readiness",
        "authorized_at": registered_at,
        "created_at": registered_at,
    }
    defaults.update(overrides)
    return AuthorizedHistoricalSource(**defaults)  # type: ignore[arg-type]


def _provider_configuration(**overrides: object) -> ProviderConfiguration:
    defaults: dict[str, object] = {
        "provider": "databento",
        "base_url": "https://hist.databento.com",
        "terms_accepted": True,
        "secret_reference": f"env:{SECRET_ENV_VAR}",
    }
    defaults.update(overrides)
    return ProviderConfiguration(**defaults)  # type: ignore[arg-type]


def _config(**overrides: object) -> DatabentoPilotConfiguration:
    defaults: dict[str, object] = {
        "source": _source(),
        "provider_configuration": _provider_configuration(),
        "symbols": ("AAPL", "MSFT", "SPY"),
        "dataset": "EQUS.SUMMARY",
        "schema": "ohlcv-1d",
        "start": date(2020, 1, 1),
        "end": date(2020, 12, 31),
        "chunk_size": timedelta(days=31),
        "cost_ceiling_usd": Decimal("125"),
        "estimated_cost_usd": Decimal("12.50"),
        "execution_approved": True,
        "attestation": _FULL_ATTESTATION,
    }
    defaults.update(overrides)
    return DatabentoPilotConfiguration(**defaults)  # type: ignore[arg-type]


class DatabentoPilotReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ[SECRET_ENV_VAR] = "fake-pilot-readiness-key"  # pragma: allowlist secret
        self.addCleanup(os.environ.pop, SECRET_ENV_VAR, None)

    def test_fully_valid_configuration_is_ready_and_never_touches_the_network(self) -> None:
        report = assess_pilot_readiness(_config(), transport=NetworkCallForbiddenTransport())
        self.assertTrue(report.ready)
        self.assertEqual(report.secret_reference, f"env:{SECRET_ENV_VAR}")
        self.assertEqual(len(report.chunk_plan), len(plan_chunks(date(2020, 1, 1), date(2020, 12, 31), timedelta(days=31))))

    def test_daily_and_minute_legs_are_both_independently_ready(self) -> None:
        daily = assess_pilot_readiness(_config(), transport=NetworkCallForbiddenTransport())
        minute = assess_pilot_readiness(
            _config(
                dataset="EQUS.MINI", schema="ohlcv-1m",
                start=date(2024, 6, 1), end=date(2024, 6, 30), chunk_size=timedelta(days=7),
                estimated_cost_usd=Decimal("0"),
            ),
            transport=NetworkCallForbiddenTransport(),
        )
        self.assertTrue(daily.ready and minute.ready)
        self.assertNotEqual(daily.configuration_identity, minute.configuration_identity)

    def test_configuration_identity_is_deterministic_and_sensitive_to_the_universe(self) -> None:
        config = _config()
        self.assertEqual(config.configuration_identity(), config.configuration_identity())
        # Same source (not re-randomized), different universe -> different identity.
        different_universe = replace(config, symbols=("AAPL", "MSFT", "SPY", "QQQ"))
        self.assertNotEqual(config.configuration_identity(), different_universe.configuration_identity())

    def test_incomplete_activation_checklist_blocks_before_anything_else(self) -> None:
        incomplete = ActivationAttestation(confirmed=frozenset(ActivationChecklistCode) - {ActivationChecklistCode.PER_RUN_AUTHORIZATION_RECEIVED})
        with self.assertRaisesRegex(PilotReadinessError, "activation_checklist_incomplete.*PER_RUN_AUTHORIZATION_RECEIVED"):
            assess_pilot_readiness(_config(attestation=incomplete), transport=NetworkCallForbiddenTransport())

    def test_missing_source_authorization_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_source_provider_must_be_databento"):
            assess_pilot_readiness(_config(source=_source(provider="some-other-provider")), transport=NetworkCallForbiddenTransport())

    def test_wrong_asset_scope_is_rejected(self) -> None:
        source = replace(_source(), asset_scope="FOREX")
        with self.assertRaisesRegex(PilotReadinessError, "pilot_source_asset_scope_not_approved"):
            assess_pilot_readiness(_config(source=source), transport=NetworkCallForbiddenTransport())

    def test_missing_terms_approval_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_provider_terms_not_accepted"):
            assess_pilot_readiness(
                _config(provider_configuration=_provider_configuration(terms_accepted=False)),
                transport=NetworkCallForbiddenTransport(),
            )

    def test_missing_secret_reference_is_rejected(self) -> None:
        from trade_platform.data_providers import ProviderConfigurationError

        with self.assertRaisesRegex(ProviderConfigurationError, "databento_secret_reference_required"):
            assess_pilot_readiness(
                _config(provider_configuration=_provider_configuration(secret_reference=None)),
                transport=NetworkCallForbiddenTransport(),
            )

    def test_unresolved_secret_is_rejected(self) -> None:
        from trade_platform.config import SecretReferenceError

        del os.environ[SECRET_ENV_VAR]
        with self.assertRaisesRegex(SecretReferenceError, "secret_reference_unavailable"):
            assess_pilot_readiness(_config(), transport=NetworkCallForbiddenTransport())

    def test_empty_universe_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_universe_out_of_bounds"):
            assess_pilot_readiness(_config(symbols=()), transport=NetworkCallForbiddenTransport())

    def test_oversized_universe_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_universe_out_of_bounds"):
            assess_pilot_readiness(_config(symbols=tuple(f"SYM{i}" for i in range(31))), transport=NetworkCallForbiddenTransport())

    def test_duplicate_symbols_in_universe_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_universe_contains_duplicate_symbols"):
            assess_pilot_readiness(_config(symbols=("AAPL", "AAPL")), transport=NetworkCallForbiddenTransport())

    def test_unsupported_schema_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_schema_not_supported"):
            assess_pilot_readiness(_config(schema="trades"), transport=NetworkCallForbiddenTransport())

    def test_dataset_schema_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_dataset_schema_mismatch"):
            assess_pilot_readiness(_config(dataset="EQUS.MINI", schema="ohlcv-1d"), transport=NetworkCallForbiddenTransport())

    def test_invalid_date_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_date_range_invalid"):
            assess_pilot_readiness(_config(start=date(2020, 12, 31), end=date(2020, 1, 1)), transport=NetworkCallForbiddenTransport())

    def test_unbounded_date_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_date_range_exceeds_bounded_window"):
            assess_pilot_readiness(
                _config(start=date(1990, 1, 1), end=date(2026, 1, 1)), transport=NetworkCallForbiddenTransport(),
            )
        with self.assertRaisesRegex(PilotReadinessError, "pilot_date_range_exceeds_bounded_window"):
            assess_pilot_readiness(
                _config(dataset="EQUS.MINI", schema="ohlcv-1m", start=date(2024, 1, 1), end=date(2024, 12, 31)),
                transport=NetworkCallForbiddenTransport(),
            )

    def test_missing_cost_estimate_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_cost_estimate_missing"):
            assess_pilot_readiness(_config(estimated_cost_usd=None), transport=NetworkCallForbiddenTransport())

    def test_cost_exceeding_ceiling_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_cost_exceeds_approved_ceiling"):
            assess_pilot_readiness(
                _config(cost_ceiling_usd=Decimal("10"), estimated_cost_usd=Decimal("10.01")),
                transport=NetworkCallForbiddenTransport(),
            )

    def test_missing_execution_approval_is_rejected(self) -> None:
        with self.assertRaisesRegex(PilotReadinessError, "pilot_execution_not_approved"):
            assess_pilot_readiness(_config(execution_approved=False), transport=NetworkCallForbiddenTransport())

    def test_chunk_plan_matches_the_real_adapters_own_chunking(self) -> None:
        report = assess_pilot_readiness(
            _config(start=date(2020, 1, 1), end=date(2020, 3, 1), chunk_size=timedelta(days=31)),
            transport=NetworkCallForbiddenTransport(),
        )
        self.assertEqual(
            report.chunk_plan,
            ((date(2020, 1, 1), date(2020, 2, 1)), (date(2020, 2, 1), date(2020, 3, 1))),
        )


if __name__ == "__main__":
    unittest.main()
