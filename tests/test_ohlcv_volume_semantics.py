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
    BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION,
    BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS,
    BarVolumeUnit,
    OhlcvVolumeSemantics,
    OhlcvVolumeSemanticsError,
    authorized_volume_semantics_rule,
    resolve_ohlcv_volume_semantics,
    volume_semantics_issues,
)

_RULE = BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS


def _resolve(provider_turnover: object, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "settlement_asset": "USDT",
        "settlement_style": SettlementStyle.LINEAR,
        "kind": CryptoInstrumentKind.PERPETUAL,
        "provider_turnover": provider_turnover,
    }
    kwargs.update(overrides)
    return resolve_ohlcv_volume_semantics(_RULE, **kwargs)  # type: ignore[arg-type]


class AuthorizedRuleTests(unittest.TestCase):
    def test_bybit_linear_source_contract_is_authorized(self) -> None:
        rule = authorized_volume_semantics_rule("bybit", "bybit_v5_public_market_linear")
        self.assertIs(rule, _RULE)
        self.assertEqual(rule.volume_unit, BarVolumeUnit.BASE_ASSET)
        self.assertEqual(rule.turnover_unit, BarVolumeUnit.QUOTE_ASSET)
        self.assertEqual(rule.semantic_version, BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION)

    def test_unauthorized_provider_or_dataset_receives_no_rule(self) -> None:
        self.assertIsNone(authorized_volume_semantics_rule("databento", "equities_us"))
        self.assertIsNone(authorized_volume_semantics_rule("bybit", "bybit_v5_public_spot"))
        self.assertIsNone(authorized_volume_semantics_rule("BYBIT", "bybit_v5_public_market_linear"))


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

    def _issues(self, semantics: OhlcvVolumeSemantics) -> tuple[str, ...]:
        return volume_semantics_issues(
            semantics,
            rule=_RULE,
            base_asset="BTC",
            quote_asset="USDT",
            settlement_style=SettlementStyle.LINEAR,
            kind=CryptoInstrumentKind.PERPETUAL,
        )

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
            volume_unit=BarVolumeUnit.CONTRACTS, volume_asset=""
        ).canonical_tuple()
        self.assertNotEqual(base, contracts)

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
