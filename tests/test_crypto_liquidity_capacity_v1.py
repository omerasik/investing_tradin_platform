"""Module 3B.3 -- canonical crypto liquidity / participation / capacity evidence.

Every price, volume, turnover, venue and instrument identifier in this file is a
FIXTURE. Nothing here was retrieved from or verified against Bybit or any other
venue, and no network call is made.

LIVE BYBIT CALLS PERFORMED: NO
LIVE ORDER/ACCOUNT CALLS PERFORMED: NO
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import pairwise
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from trade_platform import crypto_liquidity_capacity_v1 as liquidity
from trade_platform.crypto_basis_mean_reversion_v1 import (
    BasisMeanReversionDecisionV1,
    BasisMeanReversionOutcomeV1,
    BasisMeanReversionResearchRunV1,
    BasisMeanReversionTradeV1,
    CryptoBasisMeanReversionDefinitionV1,
)
from trade_platform.crypto_liquidity_capacity_v1 import (
    COMPLETE_UTC_DAY_BAR_COUNT,
    CRYPTO_LIQUIDITY_BASIS,
    CRYPTO_LIQUIDITY_SEMANTIC_VERSION,
    EVENT_INSUFFICIENT_PRIOR_COMPLETE_DAYS,
    EXCLUDED_DAY_INCOMPLETE_MINUTE_GRID,
    REASON_INSUFFICIENT_COMPLETE_LIQUIDITY_DAYS,
    REASON_INSUFFICIENT_PRIOR_LIQUIDITY_HISTORY,
    REASON_MISSING_AUTHORIZED_CAPACITY_POLICY,
    REASON_MISSING_CANONICAL_QUOTE_TURNOVER,
    REASON_MIXED_TURNOVER_ASSET,
    REASON_MIXED_VOLUME_SEMANTIC_VERSION,
    REASON_NO_CANONICAL_ORDER_EVENTS,
    REASON_UNSUPPORTED_TURNOVER_UNIT,
    REASON_UNSUPPORTED_VOLUME_UNIT,
    STATUS_AVAILABLE,
    STATUS_BLOCKED,
    STATUS_UNAVAILABLE,
    CryptoLiquidityCapacityV1Error,
    LiquidityCapacityPolicyV1,
    build_reduced_liquidity_stress_evidence_v1,
    complete_liquidity_days,
    evaluate_crypto_liquidity_capacity_v1,
)
from trade_platform.ohlcv_volume_semantics import BarVolumeUnit
from trade_platform.signed_research_exposure_v2 import SignedResearchSignalObservationV2
from trade_platform.tradable_bar_evidence_v2 import (
    AuthoritativeTradableBarSeriesV2,
    AuthoritativeTradableBarV2,
    TradableBarEvidenceV2Error,
)

INSTRUMENT = "TESTFIXTURE:3B3:BTCUSDT:PERP"
DATASET_ID = uuid5(NAMESPACE_URL, "fixture-3b3-dataset-version")
OTHER_DATASET_ID = uuid5(NAMESPACE_URL, "fixture-3b3-other-dataset-version")
SOURCE_ID = uuid5(NAMESPACE_URL, "fixture-3b3-source")
DATASET_CONTENT_HASH = "a" * 64
SEMANTIC_VERSION = "bybit-v5-linear-kline-volume-semantics-v1"

#: Five consecutive complete UTC days. Day k's every 1-minute bar publishes a
#: quote turnover of ``1000 + k``, so day k's whole-day turnover is exactly
#: ``1440 * (1000 + k)`` -- a figure no price is involved in producing.
DAY0 = date(2026, 3, 1)
DAYS = tuple(DAY0 + timedelta(days=index) for index in range(5))
TURNOVER_PER_BAR = {day: Decimal(1000 + index) for index, day in enumerate(DAYS)}
DAILY_TURNOVER = {
    day: Decimal(COMPLETE_UTC_DAY_BAR_COUNT) * value for day, value in TURNOVER_PER_BAR.items()
}

#: Deliberately unrelated to any turnover above: a fixture that multiplied
#: volume by a price would produce 12.5 * 27000 = 337500 per bar, which is not
#: any figure this module is allowed to reach.
BAR_VOLUME = Decimal("12.5")
BAR_OPEN_PRICE = Decimal("27000")

ORDER_DAY = DAYS[3]
ENTRY_AT = datetime(ORDER_DAY.year, ORDER_DAY.month, ORDER_DAY.day, 0, 5, tzinfo=UTC)
EXIT_AT = ENTRY_AT + timedelta(minutes=2)
EXPOSURE = Decimal("0.5")

POLICY = LiquidityCapacityPolicyV1(
    policy_version="fixture-capacity-policy-3b3-v1",
    lookback_complete_days=2,
    minimum_complete_days=2,
    maximum_participation=Decimal("0.05"),
    reduced_liquidity_multipliers=(Decimal("0.25"), Decimal("0.50")),
)
CAPITAL_LEVELS = (Decimal("1000000"), Decimal("5000000"))

#: An independent recomputation context, declared here rather than imported from
#: the module, so the determinism assertions do not merely agree with the
#: module's own private constant.
_CHECK = Context(prec=34, rounding=ROUND_HALF_EVEN)


def _bar(
    bar_open_at: datetime,
    *,
    turnover: Decimal | None,
    typed: bool = True,
    volume_unit: BarVolumeUnit = BarVolumeUnit.BASE_ASSET,
    turnover_unit: BarVolumeUnit = BarVolumeUnit.QUOTE_ASSET,
    volume_asset: str | None = "BTC",
    turnover_asset: str | None = "USDT",
    semantic_version: str = SEMANTIC_VERSION,
    dataset_version_id: object = DATASET_ID,
    dataset_content_hash: str = DATASET_CONTENT_HASH,
) -> AuthoritativeTradableBarV2:
    seed = bar_open_at.isoformat()
    typed_fields: dict[str, object] = (
        {
            "volume_unit": volume_unit,
            "volume_asset": volume_asset,
            "turnover": turnover,
            "turnover_unit": turnover_unit,
            "turnover_asset": turnover_asset,
            "volume_semantic_version": semantic_version,
        }
        if typed
        else {}
    )
    return AuthoritativeTradableBarV2(
        dataset_version_id=dataset_version_id,  # type: ignore[arg-type]
        dataset_content_hash=dataset_content_hash,
        source_id=SOURCE_ID,
        normalized_observation_id=uuid5(NAMESPACE_URL, f"fixture-3b3-normalized:{seed}"),
        raw_observation_id=uuid5(NAMESPACE_URL, f"fixture-3b3-raw:{seed}"),
        raw_payload_sha256="b" * 64,
        instrument_id=INSTRUMENT,
        interval="1m",
        bar_open_at=bar_open_at,
        bar_close_at=bar_open_at + timedelta(minutes=1),
        normalized_at=bar_open_at + timedelta(minutes=2),
        revision=0,
        open=BAR_OPEN_PRICE,
        high=BAR_OPEN_PRICE + Decimal("10"),
        low=BAR_OPEN_PRICE - Decimal("10"),
        close=BAR_OPEN_PRICE + Decimal("5"),
        volume=BAR_VOLUME,
        provenance_uri="fixture://bar",
        **typed_fields,  # type: ignore[arg-type]
    )


def _day_bars(day: date, *, minutes: int = COMPLETE_UTC_DAY_BAR_COUNT, **overrides: object):
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    turnover = TURNOVER_PER_BAR.get(day, Decimal("1000"))
    return [
        _bar(midnight + index * timedelta(minutes=1), turnover=turnover, **overrides)  # type: ignore[arg-type]
        for index in range(minutes)
    ]


def _series(bars, *, dataset_version_id=DATASET_ID) -> AuthoritativeTradableBarSeriesV2:
    return AuthoritativeTradableBarSeriesV2(
        dataset_version_id=dataset_version_id,
        instrument_id=INSTRUMENT,
        interval="1m",
        bars=tuple(sorted(bars, key=lambda bar: bar.bar_open_at)),
    )


_DEFINITION = CryptoBasisMeanReversionDefinitionV1(
    basis_entry_threshold=Decimal("0.001"),
    holding_horizon_bars=2,
    maximum_absolute_exposure=Decimal("0.5"),
)


def _trade(entry_at: datetime, exit_at: datetime, exposure: Decimal) -> BasisMeanReversionTradeV1:
    entry_bar = _bar(entry_at, turnover=Decimal("1000"))
    exit_bar = _bar(exit_at, turnover=Decimal("1000"))
    return BasisMeanReversionTradeV1(
        feature_materialization_id=uuid5(NAMESPACE_URL, f"fixture-3b3-mat:{entry_at.isoformat()}"),
        feature_materialization_content_hash="c" * 64,
        decision_at=entry_at - timedelta(minutes=1),
        basis_value=Decimal("0.002"),
        exposure=exposure,
        entry_bar=entry_bar,
        exit_bar=exit_bar,
        entry_time=entry_at,
        exit_time=exit_at,
        entry_open=BAR_OPEN_PRICE,
        exit_open=BAR_OPEN_PRICE,
        cost_model_version="fixture-cost-v1",
        gross_return=Decimal("0"),
        entry_cost=Decimal("0"),
        exit_cost=Decimal("0"),
        net_return=Decimal("0"),
    )


def _run(
    *,
    trades=((ENTRY_AT, EXIT_AT, EXPOSURE),),
    dataset_version_id=DATASET_ID,
    instrument_id: str = INSTRUMENT,
    content_hash: str = "d" * 64,
) -> BasisMeanReversionResearchRunV1:
    decisions = []
    for entry_at, exit_at, exposure in trades:
        trade = _trade(entry_at, exit_at, exposure)
        observation = SignedResearchSignalObservationV2(
            instrument_id=instrument_id,
            decision_at=trade.decision_at,
            exposure=exposure,
            maximum_absolute_exposure=Decimal("0.5"),
            evidence_content_hash="e" * 64,
        )
        decisions.append(
            BasisMeanReversionDecisionV1(
                feature_materialization_id=trade.feature_materialization_id,
                feature_materialization_content_hash=trade.feature_materialization_content_hash,
                basis_value=trade.basis_value,
                outcome=BasisMeanReversionOutcomeV1.EXECUTED,
                signal_observation=observation,
                trade=trade,
            )
        )
    return BasisMeanReversionResearchRunV1(
        definition=_DEFINITION,
        evidence_content_hash="f" * 64,
        dataset_version_id=dataset_version_id,
        instrument_id=instrument_id,
        cost_model_version="fixture-cost-v1",
        decisions=tuple(decisions),
        content_hash=content_hash,
        run_id=uuid5(NAMESPACE_URL, f"fixture-3b3-run:{content_hash}"),
    )


class _FiveCompleteDaysFixture(unittest.TestCase):
    """Five complete UTC days with one trade opening on the middle-plus-one day.

    The bar set is built once per class: five whole 1-minute grids is 7200 bars
    and every test below reads the same ones.
    """

    series: AuthoritativeTradableBarSeriesV2

    @classmethod
    def setUpClass(cls) -> None:
        bars: list[AuthoritativeTradableBarV2] = []
        for day in DAYS:
            bars.extend(_day_bars(day))
        cls.series = _series(bars)
        cls.research_run = _run()


# ---------------------------------------------------------------------------
# Canonical quote-turnover eligibility
# ---------------------------------------------------------------------------


class CanonicalTurnoverEligibilityTests(unittest.TestCase):
    def _evaluate(self, series, *, policy=POLICY):
        return evaluate_crypto_liquidity_capacity_v1(
            bar_series=series,
            run=_run(),
            capital_levels=CAPITAL_LEVELS,
            policy=policy,
        )

    def test_typed_bybit_linear_quote_turnover_is_accepted(self) -> None:
        evidence = self._evaluate(_series(_day_bars(DAYS[0])))
        # One complete day only, so capacity itself stays unavailable -- but the
        # semantics were accepted, which is what this test is about.
        self.assertEqual(evidence.turnover_unit, BarVolumeUnit.QUOTE_ASSET.value)
        self.assertEqual(evidence.turnover_asset, "USDT")
        self.assertEqual(evidence.volume_unit, BarVolumeUnit.BASE_ASSET.value)
        self.assertEqual(evidence.volume_asset, "BTC")
        self.assertEqual(evidence.volume_semantic_version, SEMANTIC_VERSION)
        self.assertEqual(evidence.liquidity_semantic_version, CRYPTO_LIQUIDITY_SEMANTIC_VERSION)
        self.assertEqual(evidence.liquidity_basis, CRYPTO_LIQUIDITY_BASIS)
        self.assertFalse(evidence.order_book_evidence)

    def test_legacy_unitless_bars_are_unavailable(self) -> None:
        evidence = self._evaluate(_series(_day_bars(DAYS[0], typed=False)))
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            evidence.unavailable_reasons, (REASON_MISSING_CANONICAL_QUOTE_TURNOVER,)
        )
        self.assertIsNone(evidence.turnover_unit)
        self.assertIsNone(evidence.baseline_envelope)
        self.assertEqual(evidence.complete_days, ())

    def test_one_legacy_bar_disqualifies_an_otherwise_typed_series(self) -> None:
        bars = _day_bars(DAYS[0])
        bars[17] = _bar(bars[17].bar_open_at, turnover=None, typed=False)
        evidence = self._evaluate(_series(bars))
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            evidence.unavailable_reasons, (REASON_MISSING_CANONICAL_QUOTE_TURNOVER,)
        )

    def test_wrong_turnover_unit_is_unavailable(self) -> None:
        bars = _day_bars(
            DAYS[0], turnover_unit=BarVolumeUnit.BASE_ASSET, turnover_asset="BTC"
        )
        evidence = self._evaluate(_series(bars))
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertIn(REASON_UNSUPPORTED_TURNOVER_UNIT, evidence.unavailable_reasons)

    def test_contract_counted_volume_is_unavailable(self) -> None:
        bars = _day_bars(DAYS[0], volume_unit=BarVolumeUnit.CONTRACTS, volume_asset=None)
        evidence = self._evaluate(_series(bars))
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertIn(REASON_UNSUPPORTED_VOLUME_UNIT, evidence.unavailable_reasons)

    def test_mixed_turnover_asset_is_unavailable(self) -> None:
        bars = _day_bars(DAYS[0])
        bars[3] = _bar(bars[3].bar_open_at, turnover=Decimal("1000"), turnover_asset="USDC")
        evidence = self._evaluate(_series(bars))
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertIn(REASON_MIXED_TURNOVER_ASSET, evidence.unavailable_reasons)

    def test_a_quote_turnover_with_no_asset_cannot_exist_at_all(self) -> None:
        with self.assertRaises(TradableBarEvidenceV2Error):
            _series(
                [_bar(datetime(2026, 3, 1, tzinfo=UTC), turnover=Decimal("1"), turnover_asset=None)]
            ).validate()

    def test_mixed_semantic_version_is_unavailable(self) -> None:
        bars = _day_bars(DAYS[0])
        bars[9] = _bar(
            bars[9].bar_open_at, turnover=Decimal("1000"), semantic_version="some-other-version"
        )
        evidence = self._evaluate(_series(bars))
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertIn(REASON_MIXED_VOLUME_SEMANTIC_VERSION, evidence.unavailable_reasons)

    def test_mixed_dataset_fails_closed(self) -> None:
        # A bar from another dataset cannot even form a series ...
        bars = _day_bars(DAYS[0])
        bars[5] = _bar(
            bars[5].bar_open_at, turnover=Decimal("1000"), dataset_version_id=OTHER_DATASET_ID
        )
        with self.assertRaises(TradableBarEvidenceV2Error):
            _series(bars).validate()
        # ... and a run bound to a different dataset than the series fails closed
        # rather than quietly measuring one dataset's trades against another's
        # liquidity.
        with self.assertRaises(CryptoLiquidityCapacityV1Error):
            evaluate_crypto_liquidity_capacity_v1(
                bar_series=_series(_day_bars(DAYS[0])),
                run=_run(dataset_version_id=OTHER_DATASET_ID),
                capital_levels=CAPITAL_LEVELS,
                policy=POLICY,
            )

    def test_mixed_instrument_fails_closed(self) -> None:
        with self.assertRaises(CryptoLiquidityCapacityV1Error):
            evaluate_crypto_liquidity_capacity_v1(
                bar_series=_series(_day_bars(DAYS[0])),
                run=_run(instrument_id="TESTFIXTURE:3B3:ETHUSDT:PERP"),
                capital_levels=CAPITAL_LEVELS,
                policy=POLICY,
            )

    def test_negative_and_non_finite_turnover_cannot_reach_the_calculation(self) -> None:
        for bad in (Decimal("-1"), Decimal("NaN")):
            with self.subTest(str(bad)), self.assertRaises(TradableBarEvidenceV2Error):
                _series([_bar(datetime(2026, 3, 1, tzinfo=UTC), turnover=bad)]).validate()

    def test_unsupported_interval_is_unavailable(self) -> None:
        series = replace(_series(_day_bars(DAYS[0], minutes=3)), interval="5m")
        evidence = evaluate_crypto_liquidity_capacity_v1(
            bar_series=replace(series, bars=()),
            run=_run(),
            capital_levels=CAPITAL_LEVELS,
            policy=POLICY,
        )
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertEqual(evidence.unavailable_reasons, (liquidity.REASON_UNSUPPORTED_BAR_INTERVAL,))


# ---------------------------------------------------------------------------
# Complete UTC liquidity days
# ---------------------------------------------------------------------------


class CompleteLiquidityDayTests(unittest.TestCase):
    def test_a_complete_day_is_exactly_1440_one_minute_bars(self) -> None:
        self.assertEqual(COMPLETE_UTC_DAY_BAR_COUNT, 1440)
        complete, excluded = complete_liquidity_days(_day_bars(DAYS[0]))
        self.assertEqual(excluded, ())
        self.assertEqual(len(complete), 1)
        self.assertEqual(complete[0].day, DAYS[0])
        self.assertEqual(complete[0].bar_count, 1440)

    def test_daily_quote_turnover_is_the_exact_sum_of_provider_turnover(self) -> None:
        complete, _ = complete_liquidity_days(_day_bars(DAYS[0]))
        self.assertEqual(complete[0].quote_turnover, DAILY_TURNOVER[DAYS[0]])
        self.assertEqual(complete[0].quote_turnover, Decimal(1440) * Decimal(1000))
        # It is not volume * close, nor volume * open, nor anything price-derived.
        self.assertNotEqual(
            complete[0].quote_turnover, Decimal(1440) * BAR_VOLUME * BAR_OPEN_PRICE
        )

    def test_one_missing_minute_excludes_the_whole_day(self) -> None:
        bars = _day_bars(DAYS[0])
        del bars[600]
        complete, excluded = complete_liquidity_days(bars)
        self.assertEqual(complete, ())
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0].day, DAYS[0])
        self.assertEqual(excluded[0].observed_bar_count, 1439)
        self.assertEqual(excluded[0].reason, EXCLUDED_DAY_INCOMPLETE_MINUTE_GRID)

    def test_a_1440_bar_day_that_is_not_the_right_grid_is_still_excluded(self) -> None:
        # 1440 bars, but they straddle a day boundary instead of covering
        # 00:00..23:59 of one UTC date: a bare count is not completeness.
        midnight = datetime(DAYS[0].year, DAYS[0].month, DAYS[0].day, tzinfo=UTC)
        bars = [
            _bar(midnight + timedelta(minutes=60 + index), turnover=Decimal("1000"))
            for index in range(1440)
        ]
        complete, excluded = complete_liquidity_days(bars)
        self.assertEqual(complete, ())
        self.assertEqual({item.day for item in excluded}, {DAYS[0], DAYS[1]})

    def test_a_duplicate_minute_fails_closed(self) -> None:
        bars = _day_bars(DAYS[0])
        bars.append(_bar(bars[10].bar_open_at, turnover=Decimal("999")))
        with self.assertRaises(CryptoLiquidityCapacityV1Error):
            complete_liquidity_days(bars)

    def test_partial_first_and_last_days_are_never_extrapolated(self) -> None:
        bars = _day_bars(DAYS[0], minutes=30) + _day_bars(DAYS[1]) + _day_bars(DAYS[2], minutes=90)
        complete, excluded = complete_liquidity_days(bars)
        self.assertEqual([item.day for item in complete], [DAYS[1]])
        self.assertEqual(complete[0].quote_turnover, DAILY_TURNOVER[DAYS[1]])
        self.assertEqual(
            [(item.day, item.observed_bar_count) for item in excluded],
            [(DAYS[0], 30), (DAYS[2], 90)],
        )
        # The 30-minute partial day contributed 30 * 1000 = 30000 of turnover and
        # it is nowhere in the complete-day statistics -- neither as itself nor
        # scaled by 48 to a notional full day.
        totals = {item.quote_turnover for item in complete}
        self.assertNotIn(Decimal("30000"), totals)
        self.assertNotIn(Decimal("30000") * 48, totals)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class LiquidityCapacityPolicyTests(unittest.TestCase):
    def test_policy_has_no_economically_meaningful_defaults(self) -> None:
        with self.assertRaises(TypeError):
            LiquidityCapacityPolicyV1()  # type: ignore[call-arg]

    def test_valid_fixture_policy_validates(self) -> None:
        POLICY.validate()

    def test_invalid_participation_limit_rejected(self) -> None:
        for limit in (Decimal("0"), Decimal("-0.1"), Decimal("1.5"), Decimal("NaN")):
            with self.subTest(str(limit)), self.assertRaises(CryptoLiquidityCapacityV1Error):
                replace(POLICY, maximum_participation=limit).validate()

    def test_invalid_lookback_rejected(self) -> None:
        for lookback, minimum in ((0, 1), (-1, 1), (2, 0), (2, 3)):
            with (
                self.subTest(f"{lookback}/{minimum}"),
                self.assertRaises(CryptoLiquidityCapacityV1Error),
            ):
                replace(
                    POLICY,
                    lookback_complete_days=lookback,
                    minimum_complete_days=minimum,
                ).validate()

    def test_invalid_stress_multipliers_rejected(self) -> None:
        for multipliers in (
            (),
            (Decimal("0"),),
            (Decimal("-0.5"),),
            (Decimal("1.5"),),
            (Decimal("0.5"), Decimal("0.5")),
            (Decimal("0.5"), Decimal("0.25")),
            (Decimal("NaN"),),
        ):
            with (
                self.subTest(str(multipliers)),
                self.assertRaises(CryptoLiquidityCapacityV1Error),
            ):
                replace(POLICY, reduced_liquidity_multipliers=multipliers).validate()

    def test_missing_policy_version_rejected(self) -> None:
        with self.assertRaises(CryptoLiquidityCapacityV1Error):
            replace(POLICY, policy_version="  ").validate()


# ---------------------------------------------------------------------------
# Causality
# ---------------------------------------------------------------------------


class CausalTrailingLiquidityTests(_FiveCompleteDaysFixture):
    def _evidence(self, *, policy=POLICY, run=None, capital=CAPITAL_LEVELS):
        return evaluate_crypto_liquidity_capacity_v1(
            bar_series=self.series,
            run=self.research_run if run is None else run,
            capital_levels=capital,
            policy=policy,
        )

    def test_only_days_strictly_before_the_order_timestamp_are_eligible(self) -> None:
        evidence = self._evidence()
        self.assertEqual(evidence.status, STATUS_AVAILABLE)
        self.assertEqual(len(evidence.order_events), 2)
        for event in evidence.order_events:
            reference = event.trailing_liquidity
            assert reference is not None
            # DAYS[3] is the order's own UTC day and DAYS[4] is in its future;
            # neither may appear. DAYS[0] is eligible but falls outside the
            # policy's two-day lookback window.
            self.assertEqual(reference.days_used, (DAYS[1], DAYS[2]))
            self.assertNotIn(ORDER_DAY, reference.days_used)
            self.assertNotIn(DAYS[4], reference.days_used)
            self.assertNotIn(DAYS[0], reference.days_used)

    def test_trailing_average_is_the_exact_mean_of_the_window(self) -> None:
        evidence = self._evidence()
        reference = evidence.order_events[0].trailing_liquidity
        assert reference is not None
        expected_total = DAILY_TURNOVER[DAYS[1]] + DAILY_TURNOVER[DAYS[2]]
        self.assertEqual(
            reference.daily_quote_turnovers,
            (DAILY_TURNOVER[DAYS[1]], DAILY_TURNOVER[DAYS[2]]),
        )
        self.assertEqual(
            reference.average_daily_quote_turnover, _CHECK.divide(expected_total, Decimal(2))
        )
        self.assertEqual(reference.minimum_daily_quote_turnover, DAILY_TURNOVER[DAYS[1]])

    def test_an_order_on_the_second_day_has_no_sufficient_prior_history(self) -> None:
        early = datetime(DAYS[1].year, DAYS[1].month, DAYS[1].day, 0, 5, tzinfo=UTC)
        evidence = self._evidence(
            run=_run(trades=((early, early + timedelta(minutes=2), EXPOSURE),))
        )
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            evidence.unavailable_reasons, (REASON_INSUFFICIENT_PRIOR_LIQUIDITY_HISTORY,)
        )
        for event in evidence.order_events:
            self.assertIsNone(event.trailing_liquidity)
            self.assertEqual(event.unavailable_reason, EVENT_INSUFFICIENT_PRIOR_COMPLETE_DAYS)

    def test_insufficient_lookback_is_unavailable(self) -> None:
        strict = replace(POLICY, lookback_complete_days=9, minimum_complete_days=9)
        evidence = self._evidence(policy=strict)
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            evidence.unavailable_reasons, (REASON_INSUFFICIENT_PRIOR_LIQUIDITY_HISTORY,)
        )

    def test_widening_the_lookback_widens_the_window_deterministically(self) -> None:
        wide = replace(POLICY, lookback_complete_days=3, minimum_complete_days=2)
        reference = self._evidence(policy=wide).order_events[0].trailing_liquidity
        assert reference is not None
        self.assertEqual(reference.days_used, (DAYS[0], DAYS[1], DAYS[2]))

    def test_a_run_with_no_trades_has_no_order_events(self) -> None:
        evidence = self._evidence(run=_run(trades=()))
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertEqual(evidence.unavailable_reasons, (REASON_NO_CANONICAL_ORDER_EVENTS,))

    def test_no_complete_day_at_all_is_unavailable_even_without_a_policy(self) -> None:
        pilot = _series(_day_bars(DAYS[3], minutes=30))
        evidence = evaluate_crypto_liquidity_capacity_v1(
            bar_series=pilot, run=self.research_run, capital_levels=CAPITAL_LEVELS, policy=None
        )
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            evidence.unavailable_reasons, (REASON_INSUFFICIENT_COMPLETE_LIQUIDITY_DAYS,)
        )


# ---------------------------------------------------------------------------
# Explicit policy requirement
# ---------------------------------------------------------------------------


class ExplicitPolicyRequirementTests(_FiveCompleteDaysFixture):
    def test_capacity_is_blocked_without_an_explicit_policy(self) -> None:
        evidence = evaluate_crypto_liquidity_capacity_v1(
            bar_series=self.series, run=self.research_run, capital_levels=CAPITAL_LEVELS, policy=None
        )
        self.assertEqual(evidence.status, STATUS_BLOCKED)
        self.assertEqual(
            evidence.unavailable_reasons, (REASON_MISSING_AUTHORIZED_CAPACITY_POLICY,)
        )
        self.assertIsNone(evidence.policy_version)
        self.assertIsNone(evidence.maximum_participation)
        self.assertIsNone(evidence.baseline_envelope)
        self.assertEqual(evidence.stress_envelopes, ())
        # The data work that does not need a policy still happened and is
        # reported, so the blocker is visibly about governance, not data.
        self.assertEqual(len(evidence.complete_days), 5)

    def test_capital_levels_must_be_positive_and_sorted_unique(self) -> None:
        for levels in (
            (Decimal("0"),),
            (Decimal("-1"),),
            (Decimal("5"), Decimal("1")),
            (Decimal("1"), Decimal("1")),
        ):
            with self.subTest(str(levels)), self.assertRaises(CryptoLiquidityCapacityV1Error):
                evaluate_crypto_liquidity_capacity_v1(
                    bar_series=self.series, run=self.research_run, capital_levels=levels, policy=POLICY
                )


# ---------------------------------------------------------------------------
# Participation and capacity
# ---------------------------------------------------------------------------


class ParticipationAndCapacityTests(_FiveCompleteDaysFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.evidence = evaluate_crypto_liquidity_capacity_v1(
            bar_series=cls.series, run=cls.research_run, capital_levels=CAPITAL_LEVELS, policy=POLICY
        )

    def _expected_liquidity(self) -> Decimal:
        return _CHECK.divide(DAILY_TURNOVER[DAYS[1]] + DAILY_TURNOVER[DAYS[2]], Decimal(2))

    def test_participation_is_notional_over_trailing_quote_turnover(self) -> None:
        envelope = self.evidence.baseline_envelope
        assert envelope is not None
        liquidity_reference = self._expected_liquidity()
        level = envelope.levels[0]
        expected_notional = _CHECK.multiply(CAPITAL_LEVELS[0], EXPOSURE)
        self.assertEqual(level.capital, CAPITAL_LEVELS[0])
        self.assertEqual(level.maximum_order_notional, expected_notional)
        self.assertEqual(
            level.maximum_participation, _CHECK.divide(expected_notional, liquidity_reference)
        )
        self.assertEqual(level.average_participation, level.maximum_participation)
        self.assertEqual(level.evaluated_order_event_count, 2)
        self.assertEqual(level.unavailable_order_event_count, 0)

    def test_exposure_delta_is_used_not_a_price_derived_quantity(self) -> None:
        # Entry is flat -> 0.5 and exit is 0.5 -> flat, so both transitions move
        # |0.5| of dimensionless exposure and neither consults a bar price.
        deltas = [event.exposure_delta for event in self.evidence.order_events]
        self.assertEqual(deltas, [EXPOSURE, EXPOSURE])
        self.assertEqual(
            [event.target_exposure for event in self.evidence.order_events],
            [EXPOSURE, Decimal("0")],
        )

    def test_larger_capital_is_weakly_larger_participation(self) -> None:
        envelope = self.evidence.baseline_envelope
        assert envelope is not None
        participations = [level.maximum_participation for level in envelope.levels]
        self.assertEqual(len(participations), 2)
        for smaller, larger in pairwise(participations):
            assert smaller is not None and larger is not None
            self.assertLessEqual(smaller, larger)
        self.assertLess(participations[0], participations[1])

    def test_capital_ceiling_follows_the_explicit_formula(self) -> None:
        envelope = self.evidence.baseline_envelope
        assert envelope is not None
        expected = _CHECK.divide(
            _CHECK.multiply(POLICY.maximum_participation, self._expected_liquidity()), EXPOSURE
        )
        self.assertEqual(envelope.capital_ceiling, expected)
        self.assertTrue(envelope.capital_ceiling_covers_every_order_event)

    def test_participation_limit_verdict_is_conservative(self) -> None:
        envelope = self.evidence.baseline_envelope
        assert envelope is not None
        for level in envelope.levels:
            participation = level.maximum_participation
            assert participation is not None
            self.assertEqual(
                level.maximum_participation_satisfied,
                participation <= POLICY.maximum_participation,
            )

    def test_lower_liquidity_weakly_lowers_capacity(self) -> None:
        thinner_days = tuple(
            replace(day, quote_turnover=day.quote_turnover / 2)
            for day in self.evidence.complete_days
        )
        self.assertTrue(
            all(
                thin.quote_turnover <= fat.quote_turnover
                for thin, fat in zip(thinner_days, self.evidence.complete_days, strict=True)
            )
        )
        thin_series = _series(
            [
                _bar(bar.bar_open_at, turnover=Decimal("500"))
                for bar in self.series.bars
            ]
        )
        thin = evaluate_crypto_liquidity_capacity_v1(
            bar_series=thin_series, run=self.research_run, capital_levels=CAPITAL_LEVELS, policy=POLICY
        )
        fat_envelope = self.evidence.baseline_envelope
        thin_envelope = thin.baseline_envelope
        assert fat_envelope is not None and thin_envelope is not None
        assert fat_envelope.capital_ceiling is not None
        assert thin_envelope.capital_ceiling is not None
        self.assertLessEqual(thin_envelope.capital_ceiling, fat_envelope.capital_ceiling)
        for thin_level, fat_level in zip(
            thin_envelope.levels, fat_envelope.levels, strict=True
        ):
            assert thin_level.maximum_participation is not None
            assert fat_level.maximum_participation is not None
            self.assertGreaterEqual(
                thin_level.maximum_participation, fat_level.maximum_participation
            )

    def test_no_order_book_spread_queue_or_fill_claim_is_exposed(self) -> None:
        for forbidden in (
            "spread",
            "queue",
            "fill_probability",
            "fill",
            "market_impact",
            "impact_coefficient",
            "top_of_book",
            "realized_same_bar_turnover",
        ):
            self.assertFalse(hasattr(self.evidence, forbidden), forbidden)
            envelope = self.evidence.baseline_envelope
            assert envelope is not None
            self.assertFalse(hasattr(envelope.levels[0], forbidden), forbidden)


# ---------------------------------------------------------------------------
# Reduced-liquidity stress
# ---------------------------------------------------------------------------


class ReducedLiquidityStressTests(_FiveCompleteDaysFixture):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.evidence = evaluate_crypto_liquidity_capacity_v1(
            bar_series=cls.series, run=cls.research_run, capital_levels=CAPITAL_LEVELS, policy=POLICY
        )

    def test_stress_uses_exactly_the_policy_multipliers(self) -> None:
        self.assertEqual(
            [item.liquidity_multiplier for item in self.evidence.stress_envelopes],
            list(POLICY.reduced_liquidity_multipliers),
        )

    def test_a_multiplier_below_one_weakly_worsens_capacity(self) -> None:
        baseline = self.evidence.baseline_envelope
        assert baseline is not None
        assert baseline.capital_ceiling is not None
        previous_ceiling = baseline.capital_ceiling
        for stressed in reversed(self.evidence.stress_envelopes):
            assert stressed.capital_ceiling is not None
            self.assertLessEqual(stressed.capital_ceiling, previous_ceiling)
            previous_ceiling = stressed.capital_ceiling
            for stressed_level, base_level in zip(
                stressed.levels, baseline.levels, strict=True
            ):
                assert stressed_level.maximum_participation is not None
                assert base_level.maximum_participation is not None
                self.assertGreaterEqual(
                    stressed_level.maximum_participation, base_level.maximum_participation
                )

    def test_a_half_liquidity_multiplier_exactly_halves_the_ceiling(self) -> None:
        baseline = self.evidence.baseline_envelope
        half = next(
            item
            for item in self.evidence.stress_envelopes
            if item.liquidity_multiplier == Decimal("0.50")
        )
        assert baseline is not None and baseline.capital_ceiling is not None
        assert half.capital_ceiling is not None
        self.assertEqual(
            half.capital_ceiling, _CHECK.divide(baseline.capital_ceiling, Decimal(2))
        )

    def test_stress_evidence_is_a_projection_of_the_capacity_artifact(self) -> None:
        stress = build_reduced_liquidity_stress_evidence_v1(capacity=self.evidence)
        self.assertEqual(stress.status, STATUS_AVAILABLE)
        self.assertTrue(stress.synthetic_validation_evidence)
        self.assertEqual(stress.capacity_content_hash, self.evidence.content_hash)
        self.assertEqual(
            stress.reduced_liquidity_multipliers, POLICY.reduced_liquidity_multipliers
        )

    def test_stress_inherits_an_unavailable_capacity_status(self) -> None:
        legacy = evaluate_crypto_liquidity_capacity_v1(
            bar_series=_series(_day_bars(DAYS[0], typed=False)),
            run=_run(),
            capital_levels=CAPITAL_LEVELS,
            policy=POLICY,
        )
        stress = build_reduced_liquidity_stress_evidence_v1(capacity=legacy)
        self.assertEqual(stress.status, STATUS_UNAVAILABLE)
        self.assertEqual(stress.reason, REASON_MISSING_CANONICAL_QUOTE_TURNOVER)
        self.assertEqual(stress.envelopes, ())


# ---------------------------------------------------------------------------
# Content-addressed identity
# ---------------------------------------------------------------------------


class EvidenceIdentityTests(_FiveCompleteDaysFixture):
    def _evaluate(self, *, series=None, policy=POLICY, capital=CAPITAL_LEVELS, run=None):
        return evaluate_crypto_liquidity_capacity_v1(
            bar_series=self.series if series is None else series,
            run=self.research_run if run is None else run,
            capital_levels=capital,
            policy=policy,
        )

    def test_same_inputs_reproduce_the_same_identity(self) -> None:
        first = self._evaluate()
        second = self._evaluate()
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.evidence_id, second.evidence_id)

    def test_ambient_decimal_precision_cannot_change_the_identity(self) -> None:
        baseline = self._evaluate().content_hash
        with localcontext() as context:
            context.prec = 7
            self.assertEqual(self._evaluate().content_hash, baseline)

    def test_policy_change_changes_the_identity(self) -> None:
        baseline = self._evaluate().content_hash
        for changed in (
            replace(POLICY, policy_version="fixture-capacity-policy-3b3-v2"),
            replace(POLICY, maximum_participation=Decimal("0.04")),
            replace(POLICY, lookback_complete_days=3),
            replace(POLICY, reduced_liquidity_multipliers=(Decimal("0.30"), Decimal("0.50"))),
        ):
            with self.subTest(changed.policy_version):
                self.assertNotEqual(self._evaluate(policy=changed).content_hash, baseline)

    def test_dataset_content_hash_change_changes_the_identity(self) -> None:
        baseline = self._evaluate().content_hash
        rehashed = _series(
            [
                _bar(
                    bar.bar_open_at,
                    turnover=bar.turnover,
                    dataset_content_hash="9" * 64,
                )
                for bar in self.series.bars
            ]
        )
        self.assertNotEqual(self._evaluate(series=rehashed).content_hash, baseline)

    def test_liquidity_evidence_change_changes_the_identity(self) -> None:
        baseline = self._evaluate().content_hash
        thinner = _series(
            [_bar(bar.bar_open_at, turnover=Decimal("999")) for bar in self.series.bars]
        )
        self.assertNotEqual(self._evaluate(series=thinner).content_hash, baseline)

    def test_capital_levels_change_the_identity(self) -> None:
        baseline = self._evaluate().content_hash
        self.assertNotEqual(
            self._evaluate(capital=(Decimal("1000000"),)).content_hash, baseline
        )

    def test_identity_binds_every_dimension_the_phase_requires(self) -> None:
        evidence = self._evaluate()
        self.assertEqual(evidence.dataset_version_id, DATASET_ID)
        self.assertEqual(evidence.dataset_content_hash, DATASET_CONTENT_HASH)
        self.assertEqual(evidence.instrument_id, INSTRUMENT)
        self.assertEqual(evidence.policy_content_hash, POLICY.content_hash())
        self.assertEqual(evidence.lookback_complete_days, POLICY.lookback_complete_days)
        self.assertEqual(evidence.maximum_participation, POLICY.maximum_participation)
        self.assertEqual([day.day for day in evidence.complete_days], list(DAYS))
        self.assertEqual(
            [day.quote_turnover for day in evidence.complete_days],
            [DAILY_TURNOVER[day] for day in DAYS],
        )
        self.assertEqual(evidence.capital_levels, CAPITAL_LEVELS)


# ---------------------------------------------------------------------------
# The module's own boundaries
# ---------------------------------------------------------------------------


def _executable_source(path: Path) -> str:
    """The module's source with docstrings and comments removed.

    The module's prose deliberately *names* the things it refuses to do (a
    ``volume * price`` notional, market impact, the legacy generic capacity
    helper), so scanning the raw file would flag its own explanation. These
    assertions are about the code.
    """
    lines: list[str] = []
    inside_docstring = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0] if raw_line.lstrip().startswith("#") else raw_line
        fences = line.count('"""')
        if inside_docstring:
            inside_docstring = fences % 2 == 0
            continue
        if fences:
            inside_docstring = fences % 2 == 1
            line = line.split('"""', 1)[0]
        lines.append(line)
    return "\n".join(lines)


class ModuleBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.code = _executable_source(Path(liquidity.__file__))

    def test_no_price_derived_notional_anywhere_in_the_module(self) -> None:
        for forbidden in (
            ".close",
            "average_price",
            "average_daily_volume",
            "vwap",
            "VWAP",
            # The unitless ``volume`` figure is never read at all -- only the
            # typed ``volume_unit``/``volume_asset``/``volume_semantic_version``
            # eligibility fields are, and none of them is ever arithmetic.
            ".volume *",
            ".volume)",
            ".volume,",
            "evaluate_capacity(",
        ):
            self.assertFalse(forbidden in self.code, forbidden)

    def test_module_never_parses_raw_payloads_or_calls_a_provider(self) -> None:
        for forbidden in (
            "raw_payload",
            "psycopg",
            "PostgresDatabase",
            "from .persistence",
            "httpx",
            "requests",
            "urlopen",
            "transport",
            "SELECT ",
        ):
            self.assertFalse(forbidden in self.code, forbidden)

    def test_module_models_no_impact_spread_or_fill(self) -> None:
        for forbidden in ("sqrt", "impact", "slippage", "fill_", "top_of_book"):
            self.assertFalse(forbidden in self.code, forbidden)
        # The only order-book mention the code is allowed to carry is the
        # explicit denial that this is order-book evidence.
        self.assertEqual(self.code.count("order_book"), self.code.count("order_book_evidence"))

    def test_module_does_not_touch_the_legacy_generic_capacity_path(self) -> None:
        self.assertFalse("from .quant_validation" in self.code)
        self.assertFalse("import quant_validation" in self.code)
        self.assertNotIn("quant_validation", liquidity.__dict__)


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    unittest.main()
