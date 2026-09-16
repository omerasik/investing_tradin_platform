"""Focused unit tests for AuthoritativeTradableBarV2 volume-semantics coherence
and its Module 3B.2 evidence fingerprint helper.

Pure in-memory tests: no PostgreSQL, no network. The reader itself
(``PostgresTradableBarEvidenceReaderV2``) is exercised in
``test_tradable_bar_evidence_v2_postgres.py``.

LIVE BYBIT CALLS PERFORMED: NO
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.ohlcv_volume_semantics import BarVolumeUnit
from trade_platform.tradable_bar_evidence_v2 import (
    AuthoritativeTradableBarSeriesV2,
    AuthoritativeTradableBarV2,
    TradableBarEvidenceV2Error,
    bar_volume_semantics_fingerprint,
)

DATASET_ID = uuid4()
INSTRUMENT = "TESTFIXTURE:3B2:BTCUSDT:PERP"
START = datetime(2026, 1, 1, tzinfo=UTC)


def _bar(offset: int = 0, **overrides: object) -> AuthoritativeTradableBarV2:
    bar_open_at = START + timedelta(minutes=offset)
    fields: dict[str, object] = {
        "dataset_version_id": DATASET_ID,
        "dataset_content_hash": "e" * 64,
        "source_id": uuid4(),
        "normalized_observation_id": uuid4(),
        "raw_observation_id": uuid4(),
        "raw_payload_sha256": "f" * 64,
        "instrument_id": INSTRUMENT,
        "interval": "1m",
        "bar_open_at": bar_open_at,
        "bar_close_at": bar_open_at + timedelta(minutes=1),
        "normalized_at": bar_open_at + timedelta(minutes=2),
        "revision": 0,
        "open": Decimal("100"),
        "high": Decimal("101"),
        "low": Decimal("99"),
        "close": Decimal("100.5"),
        "volume": Decimal("12.5"),
        "provenance_uri": "fixture://bar",
    }
    fields.update(overrides)
    return AuthoritativeTradableBarV2(**fields)  # type: ignore[arg-type]


def _typed_bar(**overrides: object) -> AuthoritativeTradableBarV2:
    fields: dict[str, object] = {
        "volume_unit": BarVolumeUnit.BASE_ASSET,
        "volume_asset": "BTC",
        "turnover": Decimal("1337.5"),
        "turnover_unit": BarVolumeUnit.QUOTE_ASSET,
        "turnover_asset": "USDT",
        "volume_semantic_version": "bybit-v5-linear-kline-volume-semantics-v1",
    }
    fields.update(overrides)
    return _bar(**fields)


def _series(bars: tuple[AuthoritativeTradableBarV2, ...]) -> AuthoritativeTradableBarSeriesV2:
    return AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", bars)


class BarVolumeSemanticsCoherenceTests(unittest.TestCase):
    """Item 5: a bar must be fully legacy or fully typed, never a partial mix."""

    def test_legacy_bar_validates(self) -> None:
        _series((_bar(),)).validate()

    def test_typed_bar_validates(self) -> None:
        _series((_typed_bar(),)).validate()

    def test_contracts_bar_with_no_asset_validates(self) -> None:
        _series(
            (
                _typed_bar(
                    volume_unit=BarVolumeUnit.CONTRACTS, volume_asset=None,
                    turnover_unit=BarVolumeUnit.CONTRACTS, turnover_asset=None,
                ),
            )
        ).validate()

    def test_partial_semantic_state_rejected(self) -> None:
        # volume_unit present but the other three core fields absent.
        bar = _bar(volume_unit=BarVolumeUnit.BASE_ASSET, volume_asset="BTC")
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "incoherent_bar_volume_semantics"):
            _series((bar,)).validate()

    def test_semantic_version_alone_is_also_partial(self) -> None:
        bar = _bar(volume_semantic_version="v1")
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "incoherent_bar_volume_semantics"):
            _series((bar,)).validate()

    def test_volume_asset_alone_is_not_legacy_and_is_rejected(self) -> None:
        # An asset with no unit at all -- every other semantic field None --
        # must not silently pass as "legacy" just because none of the four
        # CORE fields happens to be populated.
        bar = _bar(volume_asset="BTC")
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "incoherent_bar_volume_semantics"):
            _series((bar,)).validate()

    def test_turnover_asset_alone_is_not_legacy_and_is_rejected(self) -> None:
        bar = _bar(turnover_asset="USDT")
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "incoherent_bar_volume_semantics"):
            _series((bar,)).validate()

    def test_both_assets_alone_are_not_legacy_and_are_rejected(self) -> None:
        bar = _bar(volume_asset="BTC", turnover_asset="USDT")
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "incoherent_bar_volume_semantics"):
            _series((bar,)).validate()

    def test_asset_and_semantic_version_with_no_units_or_turnover_rejected(self) -> None:
        bar = _bar(volume_asset="BTC", volume_semantic_version="v1")
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "incoherent_bar_volume_semantics"):
            _series((bar,)).validate()

    def test_contracts_unit_with_asset_rejected(self) -> None:
        bar = _typed_bar(volume_unit=BarVolumeUnit.CONTRACTS, volume_asset="BTC")
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "bar_volume_contracts_cannot_declare_asset"):
            _series((bar,)).validate()

    def test_base_asset_unit_with_no_asset_rejected(self) -> None:
        bar = _typed_bar(volume_asset=None)
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "bar_volume_unit_requires_asset"):
            _series((bar,)).validate()

    def test_quote_asset_unit_with_no_asset_rejected(self) -> None:
        bar = _typed_bar(turnover_asset=None)
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "bar_turnover_unit_requires_asset"):
            _series((bar,)).validate()

    def test_negative_turnover_rejected(self) -> None:
        bar = _typed_bar(turnover=Decimal("-1"))
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "negative_bar_turnover"):
            _series((bar,)).validate()

    def test_empty_semantic_version_rejected(self) -> None:
        bar = _typed_bar(volume_semantic_version="   ")
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "invalid_bar_volume_semantic_version"):
            _series((bar,)).validate()

    def test_positive_infinity_turnover_rejected_deterministically(self) -> None:
        bar = _typed_bar(turnover=Decimal("Infinity"))
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "non_finite_bar_turnover"):
            _series((bar,)).validate()

    def test_negative_infinity_turnover_rejected_deterministically(self) -> None:
        bar = _typed_bar(turnover=Decimal("-Infinity"))
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "non_finite_bar_turnover"):
            _series((bar,)).validate()

    def test_nan_turnover_rejected_deterministically(self) -> None:
        # Decimal('NaN') < 0 raises decimal.InvalidOperation under the default
        # context traps -- is_finite() must be checked first so this never
        # leaks past TradableBarEvidenceV2Error.
        bar = _typed_bar(turnover=Decimal("NaN"))
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "non_finite_bar_turnover"):
            _series((bar,)).validate()


class BarVolumeSemanticsFingerprintTests(unittest.TestCase):
    """Item 1's dimension-level regressions, at the single-bar helper level."""

    def test_legacy_bar_fingerprint_is_none(self) -> None:
        self.assertIsNone(bar_volume_semantics_fingerprint(_bar()))

    def test_typed_bar_fingerprint_is_populated(self) -> None:
        fingerprint = bar_volume_semantics_fingerprint(_typed_bar())
        assert fingerprint is not None
        self.assertEqual(fingerprint["volume_unit"], "BASE_ASSET")
        self.assertEqual(fingerprint["volume_asset"], "BTC")
        self.assertEqual(fingerprint["turnover"], "1337.5")
        self.assertEqual(fingerprint["turnover_unit"], "QUOTE_ASSET")
        self.assertEqual(fingerprint["turnover_asset"], "USDT")

    def test_same_volume_and_semantics_same_fingerprint(self) -> None:
        first = bar_volume_semantics_fingerprint(_typed_bar())
        second = bar_volume_semantics_fingerprint(_typed_bar())
        self.assertEqual(first, second)

    def test_base_asset_vs_contracts_differ(self) -> None:
        base_asset = bar_volume_semantics_fingerprint(_typed_bar())
        contracts = bar_volume_semantics_fingerprint(
            _typed_bar(volume_unit=BarVolumeUnit.CONTRACTS, volume_asset=None)
        )
        self.assertNotEqual(base_asset, contracts)

    def test_different_volume_asset_differs(self) -> None:
        first = bar_volume_semantics_fingerprint(_typed_bar())
        second = bar_volume_semantics_fingerprint(_typed_bar(volume_asset="ETH"))
        self.assertNotEqual(first, second)

    def test_different_turnover_asset_differs(self) -> None:
        first = bar_volume_semantics_fingerprint(_typed_bar())
        second = bar_volume_semantics_fingerprint(_typed_bar(turnover_asset="USDC"))
        self.assertNotEqual(first, second)

    def test_different_semantic_version_differs(self) -> None:
        first = bar_volume_semantics_fingerprint(_typed_bar())
        second = bar_volume_semantics_fingerprint(_typed_bar(volume_semantic_version="v2"))
        self.assertNotEqual(first, second)

    def test_different_turnover_value_differs(self) -> None:
        first = bar_volume_semantics_fingerprint(_typed_bar())
        second = bar_volume_semantics_fingerprint(_typed_bar(turnover=Decimal("1337.6")))
        self.assertNotEqual(first, second)

    def test_fingerprint_fails_closed_on_directly_constructed_incoherent_bar(self) -> None:
        # validate() would already refuse this bar; the fingerprint helper does
        # not trust that it was called and re-derives the same refusal.
        bar = _bar(volume_unit=BarVolumeUnit.BASE_ASSET, volume_asset="BTC")
        with self.assertRaises(TradableBarEvidenceV2Error):
            bar_volume_semantics_fingerprint(bar)

    def test_asset_only_states_never_fingerprint_as_legacy(self) -> None:
        # None of these partial states may return None (the legacy sentinel)
        # from the fingerprint helper -- each must fail closed instead, with
        # no prior call to AuthoritativeTradableBarSeriesV2.validate().
        partial_bars = (
            _bar(volume_asset="BTC"),
            _bar(turnover_asset="USDT"),
            _bar(volume_asset="BTC", turnover_asset="USDT"),
            _bar(volume_asset="BTC", volume_semantic_version="v1"),
        )
        for bar in partial_bars:
            with self.subTest(bar=bar), self.assertRaisesRegex(
                TradableBarEvidenceV2Error, "incoherent_bar_volume_semantics"
            ):
                bar_volume_semantics_fingerprint(bar)

    def test_fingerprint_fails_closed_on_non_finite_turnover_without_prior_validation(self) -> None:
        for non_finite in (Decimal("Infinity"), Decimal("-Infinity"), Decimal("NaN")):
            bar = _typed_bar(turnover=non_finite)
            with self.subTest(turnover=non_finite), self.assertRaisesRegex(
                TradableBarEvidenceV2Error, "non_finite_bar_turnover"
            ):
                bar_volume_semantics_fingerprint(bar)


if __name__ == "__main__":
    unittest.main()
