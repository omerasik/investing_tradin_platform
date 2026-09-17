"""Focused unit tests for the Module 3B.2 OHLCV volume-semantics authority.

Pure in-memory tests of the typed authority itself: no PostgreSQL, no network,
no provider socket. The end-to-end persistence, sealed-hash binding and research
readback are proved in ``test_ohlcv_volume_semantics_postgres.py``.

LIVE BYBIT CALLS PERFORMED: NO
"""

from __future__ import annotations

import dataclasses
import unittest
from decimal import Decimal

from trade_platform.crypto_instruments import CryptoInstrumentKind, SettlementStyle
from trade_platform.ohlcv_volume_semantics import (
    BYBIT_LINEAR_KLINE_REQUIRED_ASSET_SCOPE,
    BYBIT_LINEAR_KLINE_REQUIRED_NAMESPACE,
    BYBIT_LINEAR_KLINE_REQUIRED_VENUE,
    BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION,
    BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS,
    BarVolumeUnit,
    OhlcvVolumeSemantics,
    OhlcvVolumeSemanticsError,
    OhlcvVolumeSemanticsRule,
    OhlcvVolumeSemanticsSourceContext,
    authorized_volume_semantics_rule,
    resolve_ohlcv_volume_semantics,
    volume_semantics_issues,
)

_RULE = BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS


def _context(**overrides: object) -> OhlcvVolumeSemanticsSourceContext:
    fields: dict[str, object] = {
        "provider": _RULE.provider,
        "dataset_name": _RULE.dataset_name,
        "provider_identifier_namespace": _RULE.provider_identifier_namespace,
        "asset_scope": _RULE.asset_scope,
    }
    fields.update(overrides)
    return OhlcvVolumeSemanticsSourceContext(**fields)  # type: ignore[arg-type]


def _resolve(provider_turnover: object, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "venue": BYBIT_LINEAR_KLINE_REQUIRED_VENUE,
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "settlement_asset": "USDT",
        "settlement_style": SettlementStyle.LINEAR,
        "kind": CryptoInstrumentKind.PERPETUAL,
        "provider_turnover": provider_turnover,
    }
    kwargs.update(overrides)
    return resolve_ohlcv_volume_semantics(_RULE, **kwargs)  # type: ignore[arg-type]


class SourceContractBindingTests(unittest.TestCase):
    """Item 2: the full source contract must match, not just provider/dataset_name."""

    def test_correct_full_bybit_source_contract_is_authorized(self) -> None:
        rule = authorized_volume_semantics_rule(_context())
        self.assertIs(rule, _RULE)
        self.assertEqual(rule.volume_unit, BarVolumeUnit.BASE_ASSET)
        self.assertEqual(rule.turnover_unit, BarVolumeUnit.QUOTE_ASSET)
        self.assertEqual(rule.semantic_version, BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION)

    def test_wrong_provider_receives_no_rule(self) -> None:
        self.assertIsNone(authorized_volume_semantics_rule(_context(provider="databento")))

    def test_wrong_dataset_receives_no_rule(self) -> None:
        self.assertIsNone(
            authorized_volume_semantics_rule(_context(dataset_name="bybit_v5_public_spot"))
        )

    def test_wrong_provider_identifier_namespace_receives_no_rule(self) -> None:
        # A synthetic source that copies provider/dataset_name exactly but
        # uses a different symbol namespace is not the authorized Bybit source.
        self.assertIsNone(
            authorized_volume_semantics_rule(
                _context(provider_identifier_namespace="some_other_namespace")
            )
        )

    def test_wrong_asset_scope_receives_no_rule(self) -> None:
        self.assertIsNone(authorized_volume_semantics_rule(_context(asset_scope="FUTURES")))

    def test_case_sensitive_provider_receives_no_rule(self) -> None:
        self.assertIsNone(authorized_volume_semantics_rule(_context(provider="BYBIT")))

    def test_required_contract_constants_match_the_rule(self) -> None:
        self.assertEqual(_RULE.provider_identifier_namespace, BYBIT_LINEAR_KLINE_REQUIRED_NAMESPACE)
        self.assertEqual(_RULE.asset_scope, BYBIT_LINEAR_KLINE_REQUIRED_ASSET_SCOPE)
        self.assertEqual(_RULE.required_venue, BYBIT_LINEAR_KLINE_REQUIRED_VENUE)


class ResolutionTests(unittest.TestCase):
    def test_btcusdt_linear_resolves_canonical_base_and_quote_semantics(self) -> None:
        semantics, issues = _resolve("337512.5")
        self.assertEqual(issues, ())
        assert semantics is not None
        self.assertEqual(semantics.volume_unit, BarVolumeUnit.BASE_ASSET)
        self.assertEqual(semantics.volume_asset, "BTC")
        self.assertEqual(semantics.turnover_unit, BarVolumeUnit.QUOTE_ASSET)
        self.assertEqual(semantics.turnover_asset, "USDT")
        self.assertEqual(semantics.semantic_version, BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION)

    def test_provider_turnover_is_preserved_exactly_never_derived(self) -> None:
        # 12.5 BTC * 27000 close would be 337500; the provider published 337512.5
        # independently, and the authority records that figure verbatim rather
        # than synthesizing turnover from volume and price.
        semantics, issues = _resolve("337512.5")
        assert semantics is not None
        self.assertEqual(issues, ())
        self.assertEqual(semantics.turnover, Decimal("337512.5"))
        self.assertNotEqual(semantics.turnover, Decimal("12.5") * Decimal("27000"))

    def test_missing_provider_turnover_is_rejected(self) -> None:
        for missing in (None, "", "   "):
            semantics, issues = _resolve(missing)
            self.assertIsNone(semantics)
            self.assertIn("missing_provider_turnover", issues)

    def test_invalid_provider_turnover_is_rejected(self) -> None:
        for invalid in ("abc", "NaN", "Infinity"):
            semantics, issues = _resolve(invalid)
            self.assertIsNone(semantics)
            self.assertIn("invalid_provider_turnover", issues)

    def test_negative_provider_turnover_is_rejected(self) -> None:
        semantics, issues = _resolve("-1.0")
        self.assertIsNone(semantics)
        self.assertIn("negative_provider_turnover", issues)

    def test_non_perpetual_instrument_is_rejected(self) -> None:
        semantics, issues = _resolve(
            "337500.0", kind=CryptoInstrumentKind.SPOT, settlement_style=None
        )
        self.assertIsNone(semantics)
        self.assertIn("volume_semantics_requires_perpetual:SPOT", issues)

    def test_non_linear_settlement_is_rejected(self) -> None:
        semantics, issues = _resolve(
            "337500.0", settlement_style=SettlementStyle.INVERSE, settlement_asset="BTC"
        )
        self.assertIsNone(semantics)
        self.assertIn("volume_semantics_requires_linear_settlement", issues)

    def test_wrong_venue_is_rejected(self) -> None:
        # Item 3: the instrument venue is proven through the canonical
        # crypto-instrument authority, never assumed because the source
        # merely declares provider=bybit.
        semantics, issues = _resolve("337500.0", venue="SOME_OTHER_VENUE")
        self.assertIsNone(semantics)
        self.assertIn("instrument_venue_mismatch:SOME_OTHER_VENUE", issues)

    def test_wrong_settlement_asset_is_rejected(self) -> None:
        # A linear contract must settle in the quote asset; settlement_asset
        # disagreeing with quote_asset fails closed even though settlement_style
        # itself claims LINEAR.
        semantics, issues = _resolve("337500.0", settlement_asset="ETH")
        self.assertIsNone(semantics)
        self.assertIn("settlement_asset_must_equal_quote_asset_for_linear_contract", issues)

    def test_assets_come_from_instrument_not_hardcoded(self) -> None:
        # The resolver must not hardcode BTC/USDT: a different (still valid)
        # linear perpetual's base/quote assets flow straight through.
        semantics, issues = _resolve(
            "42.0", base_asset="ETH", quote_asset="USDC", settlement_asset="USDC"
        )
        self.assertEqual(issues, ())
        assert semantics is not None
        self.assertEqual(semantics.volume_asset, "ETH")
        self.assertEqual(semantics.turnover_asset, "USDC")


class CoherenceTests(unittest.TestCase):
    def _semantics(self, **overrides: object) -> OhlcvVolumeSemantics:
        base: dict[str, object] = {
            "volume_unit": BarVolumeUnit.BASE_ASSET,
            "volume_asset": "BTC",
            "turnover": Decimal("337500.0"),
            "turnover_unit": BarVolumeUnit.QUOTE_ASSET,
            "turnover_asset": "USDT",
            "semantic_version": _RULE.semantic_version,
            "source_reference": _RULE.source_reference,
        }
        base.update(overrides)
        return OhlcvVolumeSemantics(**base)  # type: ignore[arg-type]

    def _issues(self, semantics: OhlcvVolumeSemantics, **overrides: object) -> tuple[str, ...]:
        kwargs: dict[str, object] = {
            "rule": _RULE,
            "venue": BYBIT_LINEAR_KLINE_REQUIRED_VENUE,
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "settlement_asset": "USDT",
            "settlement_style": SettlementStyle.LINEAR,
            "kind": CryptoInstrumentKind.PERPETUAL,
        }
        kwargs.update(overrides)
        return volume_semantics_issues(semantics, **kwargs)  # type: ignore[arg-type]

    def test_coherent_btcusdt_semantics_have_no_issues(self) -> None:
        self.assertEqual(self._issues(self._semantics()), ())

    def test_wrong_volume_asset_is_rejected(self) -> None:
        self.assertIn("volume_asset_mismatch", self._issues(self._semantics(volume_asset="ETH")))

    def test_wrong_turnover_asset_is_rejected(self) -> None:
        self.assertIn(
            "turnover_asset_mismatch", self._issues(self._semantics(turnover_asset="USDC"))
        )

    def test_unsupported_volume_unit_is_rejected(self) -> None:
        issues = self._issues(
            self._semantics(volume_unit=BarVolumeUnit.QUOTE_ASSET, volume_asset="USDT")
        )
        self.assertIn("unsupported_volume_unit:QUOTE_ASSET", issues)

    def test_wrong_semantic_version_is_rejected(self) -> None:
        issues = self._issues(self._semantics(semantic_version="some-other-version"))
        self.assertIn("semantic_version_mismatch", issues)

    def test_wrong_venue_is_rejected(self) -> None:
        issues = self._issues(self._semantics(), venue="NOT_BYBIT")
        self.assertIn("instrument_venue_mismatch:NOT_BYBIT", issues)

    def test_wrong_kind_is_rejected(self) -> None:
        issues = self._issues(self._semantics(), kind=CryptoInstrumentKind.DATED_FUTURE)
        self.assertTrue(any(issue.startswith("volume_semantics_requires_perpetual") for issue in issues))

    def test_wrong_settlement_style_is_rejected(self) -> None:
        issues = self._issues(self._semantics(), settlement_style=SettlementStyle.INVERSE)
        self.assertIn("volume_semantics_requires_linear_settlement", issues)


class ContractsUnitTests(unittest.TestCase):
    """Item 4: CONTRACTS is a bare count -- representable, and never invents an asset."""

    def test_resolver_supports_a_contracts_rule_without_inventing_an_asset(self) -> None:
        contracts_rule = OhlcvVolumeSemanticsRule(
            provider="fixture_provider",
            dataset_name="fixture_dataset",
            provider_identifier_namespace="fixture_namespace",
            asset_scope="CRYPTO",
            semantic_version="fixture-contracts-v1",
            volume_unit=BarVolumeUnit.CONTRACTS,
            turnover_unit=BarVolumeUnit.QUOTE_ASSET,
            source_reference="fixture:contracts",
            required_venue="FIXTURE_VENUE",
            required_kind=CryptoInstrumentKind.PERPETUAL,
            required_settlement_style=SettlementStyle.LINEAR,
        )
        semantics, issues = resolve_ohlcv_volume_semantics(
            contracts_rule,
            venue="FIXTURE_VENUE",
            base_asset="BTC",
            quote_asset="USDT",
            settlement_asset="USDT",
            settlement_style=SettlementStyle.LINEAR,
            kind=CryptoInstrumentKind.PERPETUAL,
            provider_turnover="1000.0",
        )
        self.assertEqual(issues, ())
        assert semantics is not None
        self.assertEqual(semantics.volume_unit, BarVolumeUnit.CONTRACTS)
        self.assertIsNone(semantics.volume_asset)
        self.assertEqual(semantics.turnover_unit, BarVolumeUnit.QUOTE_ASSET)
        self.assertEqual(semantics.turnover_asset, "USDT")


class ObjectValidationTests(unittest.TestCase):
    def _make(self, **overrides: object) -> OhlcvVolumeSemantics:
        base: dict[str, object] = {
            "volume_unit": BarVolumeUnit.BASE_ASSET,
            "volume_asset": "BTC",
            "turnover": Decimal("337500.0"),
            "turnover_unit": BarVolumeUnit.QUOTE_ASSET,
            "turnover_asset": "USDT",
            "semantic_version": _RULE.semantic_version,
            "source_reference": _RULE.source_reference,
        }
        base.update(overrides)
        return OhlcvVolumeSemantics(**base)  # type: ignore[arg-type]

    def test_valid_object_constructs(self) -> None:
        self.assertEqual(self._make().volume_asset, "BTC")

    def test_valid_contracts_object_constructs_with_no_asset(self) -> None:
        semantics = self._make(
            volume_unit=BarVolumeUnit.CONTRACTS, volume_asset=None,
            turnover_unit=BarVolumeUnit.CONTRACTS, turnover_asset=None,
        )
        self.assertIsNone(semantics.volume_asset)
        self.assertIsNone(semantics.turnover_asset)

    def test_contracts_with_asset_fails_closed(self) -> None:
        with self.assertRaises(OhlcvVolumeSemanticsError):
            self._make(volume_unit=BarVolumeUnit.CONTRACTS, volume_asset="BTC")

    def test_base_asset_with_no_asset_fails_closed(self) -> None:
        with self.assertRaises(OhlcvVolumeSemanticsError):
            self._make(volume_asset=None)

    def test_quote_asset_with_no_asset_fails_closed(self) -> None:
        with self.assertRaises(OhlcvVolumeSemanticsError):
            self._make(turnover_asset=None)

    def test_invalid_volume_asset_fails_closed(self) -> None:
        for bad in ("btc", "B", "TOOLONGASSETCODE", ""):
            with self.assertRaises(OhlcvVolumeSemanticsError):
                self._make(volume_asset=bad)

    def test_negative_or_non_finite_turnover_fails_closed(self) -> None:
        with self.assertRaises(OhlcvVolumeSemanticsError):
            self._make(turnover=Decimal("-1"))
        with self.assertRaises(OhlcvVolumeSemanticsError):
            self._make(turnover=Decimal("NaN"))

    def test_empty_semantic_version_or_source_reference_fails_closed(self) -> None:
        with self.assertRaises(OhlcvVolumeSemanticsError):
            self._make(semantic_version="  ")
        with self.assertRaises(OhlcvVolumeSemanticsError):
            self._make(source_reference="")

    def test_semantics_are_immutable(self) -> None:
        semantics = self._make()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            semantics.turnover = Decimal("1")  # type: ignore[misc]


class CanonicalTupleTests(unittest.TestCase):
    def _make(self, **overrides: object) -> OhlcvVolumeSemantics:
        base: dict[str, object] = {
            "volume_unit": BarVolumeUnit.BASE_ASSET,
            "volume_asset": "BTC",
            "turnover": Decimal("337500.0"),
            "turnover_unit": BarVolumeUnit.QUOTE_ASSET,
            "turnover_asset": "USDT",
            "semantic_version": _RULE.semantic_version,
            "source_reference": _RULE.source_reference,
        }
        base.update(overrides)
        return OhlcvVolumeSemantics(**base)  # type: ignore[arg-type]

    def test_same_semantics_same_canonical_tuple(self) -> None:
        self.assertEqual(self._make().canonical_tuple(), self._make().canonical_tuple())

    def test_base_asset_vs_contracts_differ(self) -> None:
        base = self._make().canonical_tuple()
        contracts = self._make(
            volume_unit=BarVolumeUnit.CONTRACTS, volume_asset=None
        ).canonical_tuple()
        self.assertNotEqual(base, contracts)

    def test_contracts_asset_serializes_deterministically_as_empty_string(self) -> None:
        contracts = self._make(
            volume_unit=BarVolumeUnit.CONTRACTS, volume_asset=None
        ).canonical_tuple()
        self.assertIn("", contracts)
        self.assertNotIn(None, contracts)  # type: ignore[comparison-overlap]

    def test_turnover_asset_change_differs(self) -> None:
        self.assertNotEqual(
            self._make().canonical_tuple(),
            self._make(turnover_asset="USDC").canonical_tuple(),
        )

    def test_turnover_value_change_differs(self) -> None:
        self.assertNotEqual(
            self._make().canonical_tuple(),
            self._make(turnover=Decimal("337500.5")).canonical_tuple(),
        )

    def test_semantic_version_change_differs(self) -> None:
        self.assertNotEqual(
            self._make().canonical_tuple(),
            self._make(semantic_version="v2").canonical_tuple(),
        )

    def test_projection_exposes_units_without_raw_json(self) -> None:
        projection = self._make().as_projection()
        self.assertEqual(projection["volume_unit"], "BASE_ASSET")
        self.assertEqual(projection["volume_asset"], "BTC")
        self.assertEqual(projection["turnover_unit"], "QUOTE_ASSET")
        self.assertEqual(projection["turnover_asset"], "USDT")


if __name__ == "__main__":
    unittest.main()
