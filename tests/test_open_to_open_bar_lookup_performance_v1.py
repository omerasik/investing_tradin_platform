from __future__ import annotations

import dataclasses
import random
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.crypto_basis_mean_reversion_v1 import (
    BasisMeanReversionResearchRunV1,
    CryptoBasisMeanReversionDefinitionV1,
    run_crypto_basis_mean_reversion_research,
)
from trade_platform.crypto_instruments import CryptoInstrumentKind
from trade_platform.feature_authority import (
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
)
from trade_platform.open_to_open_validation_v1 import (
    _bar_series_fingerprint,
    _content_hash,
    _direction,
    _eligible_decisions_in_window,
    _has_evaluation_window_carry_in_trade,
    _identity,
    _wire_cost_model,
    build_realized_exit_daily_return_series_v1,
    circularly_shift_basis_values,
    evaluate_circular_shift_null_control_v1,
    evaluate_full_permutation_null_diagnostic_v1,
    non_annualized_daily_sharpe,
)
from trade_platform.research import CostModel
from trade_platform.signed_price_return_v2 import compute_signed_open_to_open_return
from trade_platform.strategy_feature_binding_v2 import (
    AuthoritativeFeatureSeriesV2,
    ResearchFeatureRequirementV2,
    ResearchQualityPolicyV2,
    SubjectAwareResearchFeatureBundle,
)
from trade_platform.tradable_bar_evidence_v2 import (
    AuthoritativeTradableBarSeriesV2,
    AuthoritativeTradableBarV2,
    TradableBarEvidenceV2Error,
)
from trade_platform.tradable_research_evidence_v2 import SubjectAwareTradableResearchEvidenceV2

START = datetime(2026, 1, 1, tzinfo=UTC)
DATASET_ID = uuid4()
INSTRUMENT = "TESTFIXTURE:3J2B2B2:PERF:BTCUSDT:PERP"
BASIS_FEATURE_ID = uuid4()
ZERO_COST = CostModel()
_ONE_BAR_INTERVAL = timedelta(minutes=1)


def _bar(offset: int, open_price: Decimal = Decimal("100")) -> AuthoritativeTradableBarV2:
    bar_open_at = START + timedelta(minutes=offset)
    return AuthoritativeTradableBarV2(
        dataset_version_id=DATASET_ID,
        dataset_content_hash="e" * 64,
        source_id=uuid4(),
        normalized_observation_id=uuid4(),
        raw_observation_id=uuid4(),
        raw_payload_sha256="f" * 64,
        instrument_id=INSTRUMENT,
        interval="1m",
        bar_open_at=bar_open_at,
        bar_close_at=bar_open_at + timedelta(minutes=1),
        normalized_at=bar_open_at + timedelta(minutes=2),
        revision=0,
        open=open_price,
        high=open_price + Decimal("1"),
        low=open_price - Decimal("1"),
        close=open_price + Decimal("0.5"),
        volume=Decimal("10"),
        provenance_uri="fixture://bar",
    )


def _series_from_offsets(offsets: tuple[int, ...], *, validated: bool = True) -> AuthoritativeTradableBarSeriesV2:
    bars = tuple(_bar(offset) for offset in offsets)
    series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", bars)
    if validated:
        series.validate()
    return series


def _linear_first_eligible_bar_after(
    bars: tuple[AuthoritativeTradableBarV2, ...], decision_at: datetime
) -> AuthoritativeTradableBarV2 | None:
    candidates = [bar for bar in bars if bar.bar_open_at > decision_at]
    if not candidates:
        return None
    earliest = min(bar.bar_open_at for bar in candidates)
    tied = [bar for bar in candidates if bar.bar_open_at == earliest]
    if len(tied) > 1:
        raise TradableBarEvidenceV2Error("ambiguous_first_eligible_bar")
    return tied[0]


def _linear_bar_at_open_time(
    bars: tuple[AuthoritativeTradableBarV2, ...], bar_open_at: datetime
) -> AuthoritativeTradableBarV2 | None:
    return next((bar for bar in bars if bar.bar_open_at == bar_open_at), None)


class BarLookupExactEquivalenceTests(unittest.TestCase):
    SPARSE_OFFSETS = (0, 1, 2, 10, 11, 12, 20)
    PROBE_MINUTES = (-5, 0, 1, 2, 3, 5, 9, 10, 11, 12, 13, 15, 19, 20, 21, 30)

    def test_first_eligible_bar_after_matches_linear_reference_on_sparse_series(self) -> None:
        series = _series_from_offsets(self.SPARSE_OFFSETS)
        for minutes in self.PROBE_MINUTES:
            decision_at = START + timedelta(minutes=minutes)
            with self.subTest(decision_at=decision_at):
                expected = _linear_first_eligible_bar_after(series.bars, decision_at)
                actual = series.first_eligible_bar_after(decision_at)
                self.assertEqual(actual, expected)

    def test_bar_at_open_time_matches_linear_reference_on_sparse_series(self) -> None:
        series = _series_from_offsets(self.SPARSE_OFFSETS)
        for minutes in self.PROBE_MINUTES:
            bar_open_at = START + timedelta(minutes=minutes)
            with self.subTest(bar_open_at=bar_open_at):
                expected = _linear_bar_at_open_time(series.bars, bar_open_at)
                actual = series.bar_at_open_time(bar_open_at)
                self.assertEqual(actual, expected)

    def test_decision_before_first_bar(self) -> None:
        series = _series_from_offsets((5, 6, 7))
        self.assertEqual(series.first_eligible_bar_after(START), series.bars[0])

    def test_decision_exactly_at_bar_open_is_not_eligible(self) -> None:
        series = _series_from_offsets((0, 1, 2))
        result = series.first_eligible_bar_after(series.bars[1].bar_open_at)
        self.assertEqual(result, series.bars[2])

    def test_decision_between_bars_in_a_gap(self) -> None:
        series = _series_from_offsets((0, 10, 20))
        result = series.first_eligible_bar_after(START + timedelta(minutes=5))
        self.assertEqual(result, series.bars[1])

    def test_decision_after_final_bar_returns_none(self) -> None:
        series = _series_from_offsets((0, 1, 2))
        result = series.first_eligible_bar_after(series.bars[-1].bar_open_at + timedelta(minutes=1))
        self.assertIsNone(result)

    def test_exact_exit_exists(self) -> None:
        series = _series_from_offsets((0, 1, 2, 3))
        self.assertEqual(series.bar_at_open_time(series.bars[2].bar_open_at), series.bars[2])

    def test_exact_exit_missing_inside_a_gap(self) -> None:
        series = _series_from_offsets((0, 1, 2, 10, 11))
        self.assertIsNone(series.bar_at_open_time(START + timedelta(minutes=5)))

    def test_exact_exit_missing_before_first_bar(self) -> None:
        series = _series_from_offsets((5, 6, 7))
        self.assertIsNone(series.bar_at_open_time(START))

    def test_exact_exit_missing_after_final_bar(self) -> None:
        series = _series_from_offsets((0, 1, 2))
        self.assertIsNone(series.bar_at_open_time(series.bars[-1].bar_open_at + timedelta(minutes=1)))

    def test_ambiguous_duplicate_open_time_still_raises(self) -> None:
        duplicated_open = START + timedelta(minutes=5)
        bars = (_bar(0), _bar(5), dataclasses.replace(_bar(5), raw_observation_id=uuid4()))
        series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", bars)
        self.assertEqual(bars[1].bar_open_at, duplicated_open)
        self.assertEqual(bars[2].bar_open_at, duplicated_open)
        with self.assertRaisesRegex(TradableBarEvidenceV2Error, "ambiguous_first_eligible_bar"):
            series.first_eligible_bar_after(START)


def _materialization(*, event_at: datetime, value: Decimal) -> FeatureMaterializationV2:
    return FeatureMaterializationV2.create(
        feature_id=BASIS_FEATURE_ID,
        subject_type=FeatureSubjectType.INSTRUMENT,
        subject_id=INSTRUMENT,
        dataset_version=str(DATASET_ID),
        event_at=event_at,
        effective_at=event_at,
        knowledge_at=event_at,
        computed_at=event_at,
        source_observation_manifest=("fixture:manifest",),
        value=value,
        quality_status=FeatureQualityStatus.VALIDATED,
    )


def _bundle(offsets_values: list[tuple[int, Decimal]]) -> SubjectAwareResearchFeatureBundle:
    materializations = tuple(
        _materialization(event_at=START + timedelta(minutes=offset), value=value) for offset, value in offsets_values
    )
    requirement = ResearchFeatureRequirementV2(
        feature_id=BASIS_FEATURE_ID,
        name="crypto_mark_index_basis",
        semantic_version="1.0.0",
        expected_subject_type=FeatureSubjectType.INSTRUMENT,
    )
    series = AuthoritativeFeatureSeriesV2(
        requirement=requirement,
        subject_type=FeatureSubjectType.INSTRUMENT,
        subject_id=INSTRUMENT,
        dataset_version=str(DATASET_ID),
        materializations=materializations,
    )
    return SubjectAwareResearchFeatureBundle.create(
        dataset_version_id=DATASET_ID,
        subject_type=FeatureSubjectType.INSTRUMENT,
        subject_id=INSTRUMENT,
        decision_at=START + timedelta(days=1),
        quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY,
        feature_series=(series,),
    )


def _definition(*, threshold: Decimal = Decimal("0.0005"), horizon: int = 10) -> CryptoBasisMeanReversionDefinitionV1:
    return CryptoBasisMeanReversionDefinitionV1(
        basis_entry_threshold=threshold, holding_horizon_bars=horizon, maximum_absolute_exposure=Decimal("1")
    )


def _random_walk_bar_series(bar_minutes: int, generator: random.Random) -> AuthoritativeTradableBarSeriesV2:
    price = Decimal("100")
    bars = []
    for offset in range(bar_minutes):
        bars.append(_bar(offset, open_price=price))
        step = Decimal(generator.randint(-50, 50)) / Decimal("100")
        price = price + step
    series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", tuple(bars))
    series.validate()
    return series


def _dense_null_control_run(
    *, event_count: int = 90, bar_minutes: int = 120, horizon: int = 10
) -> tuple[BasisMeanReversionResearchRunV1, AuthoritativeTradableBarSeriesV2]:
    generator = random.Random(20260114)  # nosec B311
    offsets_values: list[tuple[int, Decimal]] = []
    for index in range(event_count):
        roll = generator.random()
        if roll < 0.2:
            value = Decimal("0.001")
        elif roll < 0.4:
            value = Decimal("-0.001")
        else:
            value = Decimal("0")
        offsets_values.append((index, value))
    bundle = _bundle(offsets_values)
    bar_series = _random_walk_bar_series(bar_minutes, generator)
    evidence = SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=bundle, bar_series=bar_series)
    run = run_crypto_basis_mean_reversion_research(
        definition=_definition(horizon=horizon),
        evidence=evidence,
        instrument_kind=CryptoInstrumentKind.PERPETUAL,
        cost_model=ZERO_COST,
        cost_model_version="cost-model-v1",
    )
    return run, bar_series


def _legacy_replay_open_to_open(
    *,
    decision_inputs: tuple[tuple[datetime, Decimal], ...],
    bar_series: AuthoritativeTradableBarSeriesV2,
    cap: Decimal,
    horizon: timedelta,
    cost_model: CostModel,
) -> list[tuple[datetime, Decimal]]:
    pairs: list[tuple[datetime, Decimal]] = []
    last_executed_exit_time: datetime | None = None
    for decision_at, exposure in decision_inputs:
        if exposure == 0:
            continue
        if last_executed_exit_time is not None and decision_at <= last_executed_exit_time:
            continue
        entry_bar = _linear_first_eligible_bar_after(bar_series.bars, decision_at)
        if entry_bar is None:
            continue
        exit_open_at = entry_bar.bar_open_at + horizon
        exit_bar = _linear_bar_at_open_time(bar_series.bars, exit_open_at)
        if exit_bar is None:
            continue
        computed = compute_signed_open_to_open_return(
            entry_bar=entry_bar,
            exit_bar=exit_bar,
            exposure=exposure,
            maximum_absolute_exposure=cap,
            cost_model=cost_model,
        )
        pairs.append((computed.exit_time, computed.net_return))
        last_executed_exit_time = exit_bar.bar_open_at
    return pairs


def _legacy_daily_sharpe(
    pairs: list[tuple[datetime, Decimal]], window_start: datetime, window_end: datetime
) -> float | None:
    days = (window_end - window_start).days
    buckets: dict[int, list[Decimal]] = {}
    for exit_time, net_return in pairs:
        index = (exit_time.astimezone(UTC).date() - window_start.date()).days
        if 0 <= index < days:
            buckets.setdefault(index, []).append(net_return)
    daily: list[Decimal] = []
    for index in range(days):
        compounded = Decimal("1")
        for net_return in buckets.get(index, ()):
            compounded *= Decimal("1") + net_return
        daily.append(compounded - Decimal("1"))
    return non_annualized_daily_sharpe(tuple(daily))


class NullControlLegacyEquivalenceTests(unittest.TestCase):
    def test_circular_shift_matches_legacy_linear_replay(self) -> None:
        run, bar_series = _dense_null_control_run()
        window_start = START
        window_end = START + timedelta(days=2)
        seed = 4242
        target_null_runs = 47
        minimum_valid_shifts = 5

        optimized = evaluate_circular_shift_null_control_v1(
            run=run,
            bar_series=bar_series,
            base_cost_model=ZERO_COST,
            window_start=window_start,
            window_end=window_end,
            seed=seed,
            target_null_runs=target_null_runs,
            minimum_valid_shifts=minimum_valid_shifts,
        )

        carry_in = _has_evaluation_window_carry_in_trade(run, window_start, window_end)
        self.assertFalse(carry_in)
        timestamps, basis_values = _eligible_decisions_in_window(run, window_start, window_end)
        event_count = len(basis_values)
        cap = run.definition.maximum_absolute_exposure
        threshold = run.definition.basis_entry_threshold
        horizon = run.definition.holding_horizon_bars * _ONE_BAR_INTERVAL
        observed_series = build_realized_exit_daily_return_series_v1(
            run=run, window_start=window_start, window_end=window_end
        )
        observed_sharpe = non_annualized_daily_sharpe(observed_series.daily_returns)

        attempted = min(target_null_runs, event_count - 1) if event_count >= 2 else 0
        self.assertGreater(attempted, 0)
        rng = random.Random(seed)  # nosec B311
        selected_offsets = tuple(sorted(rng.sample(range(1, event_count), attempted)))

        valid_stats: list[float] = []
        unavailable_shifts = 0
        for offset in selected_offsets:
            shifted = circularly_shift_basis_values(basis_values, offset)
            decision_inputs = tuple(
                (timestamps[index], _direction(shifted[index], threshold, cap)) for index in range(event_count)
            )
            pairs = _legacy_replay_open_to_open(
                decision_inputs=decision_inputs, bar_series=bar_series, cap=cap, horizon=horizon, cost_model=ZERO_COST
            )
            stat = _legacy_daily_sharpe(pairs, window_start, window_end)
            if stat is None:
                unavailable_shifts += 1
            else:
                valid_stats.append(stat)

        valid_shifts = len(valid_stats)
        self.assertGreaterEqual(valid_shifts, minimum_valid_shifts)
        assert observed_sharpe is not None
        greater_or_equal = sum(1 for stat in valid_stats if stat >= observed_sharpe)
        legacy_p_value = Decimal(1 + greater_or_equal) / Decimal(1 + valid_shifts)

        self.assertEqual(optimized.attempted_shifts, attempted)
        self.assertEqual(optimized.selected_offsets, selected_offsets)
        self.assertEqual(optimized.valid_shifts, valid_shifts)
        self.assertEqual(optimized.unavailable_shifts, unavailable_shifts)
        self.assertEqual(optimized.observed_sharpe, observed_sharpe)
        self.assertEqual(optimized.empirical_p_value, legacy_p_value)

        bar_fingerprint = _bar_series_fingerprint(bar_series)
        legacy_payload = {
            "status": optimized.status,
            "unavailable_reasons": optimized.unavailable_reasons,
            "synthetic_null_control_evidence": True,
            "source_run_content_hash": run.content_hash,
            "bar_series_fingerprint": bar_fingerprint,
            "base_cost_model": _wire_cost_model(ZERO_COST),
            "base_cost_model_version": run.cost_model_version,
            "seed": seed,
            "window_start": window_start,
            "window_end": window_end,
            "eligible_feature_event_count": event_count,
            "target_null_runs": target_null_runs,
            "attempted_shifts": attempted,
            "valid_shifts": valid_shifts,
            "unavailable_shifts": unavailable_shifts,
            "selected_offsets": selected_offsets,
            "observed_sharpe": observed_sharpe,
            "empirical_p_value": legacy_p_value,
        }
        legacy_content_hash = _content_hash(legacy_payload)
        legacy_evidence_id = _identity("circular-shift-null-control-v1", legacy_content_hash)
        self.assertEqual(optimized.content_hash, legacy_content_hash)
        self.assertEqual(optimized.evidence_id, legacy_evidence_id)

    def test_full_permutation_matches_legacy_linear_replay(self) -> None:
        run, bar_series = _dense_null_control_run()
        window_start = START
        window_end = START + timedelta(days=2)
        seed = 777
        permutations = 41
        minimum_valid_permutations = 5

        optimized = evaluate_full_permutation_null_diagnostic_v1(
            run=run,
            bar_series=bar_series,
            base_cost_model=ZERO_COST,
            window_start=window_start,
            window_end=window_end,
            seed=seed,
            permutations=permutations,
            minimum_valid_permutations=minimum_valid_permutations,
        )

        carry_in = _has_evaluation_window_carry_in_trade(run, window_start, window_end)
        self.assertFalse(carry_in)
        timestamps, basis_tuple = _eligible_decisions_in_window(run, window_start, window_end)
        basis_values = list(basis_tuple)
        event_count = len(basis_values)
        cap = run.definition.maximum_absolute_exposure
        threshold = run.definition.basis_entry_threshold
        horizon = run.definition.holding_horizon_bars * _ONE_BAR_INTERVAL
        observed_series = build_realized_exit_daily_return_series_v1(
            run=run, window_start=window_start, window_end=window_end
        )
        observed_sharpe = non_annualized_daily_sharpe(observed_series.daily_returns)

        rng = random.Random(seed)  # nosec B311
        valid_stats: list[float] = []
        for _ in range(permutations):
            shuffled = basis_values[:]
            rng.shuffle(shuffled)
            decision_inputs = tuple(
                (timestamps[index], _direction(shuffled[index], threshold, cap)) for index in range(event_count)
            )
            pairs = _legacy_replay_open_to_open(
                decision_inputs=decision_inputs, bar_series=bar_series, cap=cap, horizon=horizon, cost_model=ZERO_COST
            )
            stat = _legacy_daily_sharpe(pairs, window_start, window_end)
            if stat is not None:
                valid_stats.append(stat)

        valid_permutations = len(valid_stats)
        self.assertGreaterEqual(valid_permutations, minimum_valid_permutations)
        assert observed_sharpe is not None
        greater_or_equal = sum(1 for stat in valid_stats if stat >= observed_sharpe)
        legacy_p_value = Decimal(1 + greater_or_equal) / Decimal(1 + valid_permutations)

        self.assertEqual(optimized.attempted_permutations, permutations)
        self.assertEqual(optimized.valid_permutations, valid_permutations)
        self.assertEqual(optimized.observed_sharpe, observed_sharpe)
        self.assertEqual(optimized.diagnostic_p_value, legacy_p_value)

        bar_fingerprint = _bar_series_fingerprint(bar_series)
        legacy_payload = {
            "is_primary": False,
            "status": optimized.status,
            "unavailable_reasons": optimized.unavailable_reasons,
            "synthetic_null_control_evidence": True,
            "source_run_content_hash": run.content_hash,
            "bar_series_fingerprint": bar_fingerprint,
            "base_cost_model": _wire_cost_model(ZERO_COST),
            "base_cost_model_version": run.cost_model_version,
            "seed": seed,
            "window_start": window_start,
            "window_end": window_end,
            "attempted_permutations": permutations,
            "valid_permutations": valid_permutations,
            "observed_sharpe": observed_sharpe,
            "diagnostic_p_value": legacy_p_value,
        }
        legacy_content_hash = _content_hash(legacy_payload)
        legacy_evidence_id = _identity("full-permutation-null-diagnostic-v1", legacy_content_hash)
        self.assertEqual(optimized.content_hash, legacy_content_hash)
        self.assertEqual(optimized.evidence_id, legacy_evidence_id)


class ReplayGeometryStructuralPerformanceTests(unittest.TestCase):
    def test_bar_lookups_scale_with_events_not_with_null_runs(self) -> None:
        run, bar_series = _dense_null_control_run()
        window_start = START
        window_end = START + timedelta(days=2)
        _timestamps, basis_values = _eligible_decisions_in_window(run, window_start, window_end)
        event_count = len(basis_values)

        original_first = AuthoritativeTradableBarSeriesV2.first_eligible_bar_after
        original_bar_at = AuthoritativeTradableBarSeriesV2.bar_at_open_time
        calls = {"first_eligible": 0, "bar_at_open": 0}

        def counting_first(self: AuthoritativeTradableBarSeriesV2, decision_at: datetime) -> object:
            calls["first_eligible"] += 1
            return original_first(self, decision_at)

        def counting_bar_at(self: AuthoritativeTradableBarSeriesV2, bar_open_at: datetime) -> object:
            calls["bar_at_open"] += 1
            return original_bar_at(self, bar_open_at)

        AuthoritativeTradableBarSeriesV2.first_eligible_bar_after = counting_first  # type: ignore[method-assign]
        AuthoritativeTradableBarSeriesV2.bar_at_open_time = counting_bar_at  # type: ignore[method-assign]
        try:
            evaluate_circular_shift_null_control_v1(
                run=run,
                bar_series=bar_series,
                base_cost_model=ZERO_COST,
                window_start=window_start,
                window_end=window_end,
                seed=1,
                target_null_runs=5,
                minimum_valid_shifts=1,
            )
            small_run_calls = dict(calls)

            calls["first_eligible"] = 0
            calls["bar_at_open"] = 0
            evaluate_circular_shift_null_control_v1(
                run=run,
                bar_series=bar_series,
                base_cost_model=ZERO_COST,
                window_start=window_start,
                window_end=window_end,
                seed=1,
                target_null_runs=300,
                minimum_valid_shifts=1,
            )
            large_run_calls = dict(calls)
        finally:
            AuthoritativeTradableBarSeriesV2.first_eligible_bar_after = original_first  # type: ignore[method-assign]
            AuthoritativeTradableBarSeriesV2.bar_at_open_time = original_bar_at  # type: ignore[method-assign]

        self.assertEqual(small_run_calls["first_eligible"], event_count)
        self.assertEqual(large_run_calls["first_eligible"], event_count)
        self.assertLessEqual(small_run_calls["bar_at_open"], event_count)
        self.assertEqual(small_run_calls["bar_at_open"], large_run_calls["bar_at_open"])

    def test_full_permutation_bar_lookups_scale_with_events_not_permutations(self) -> None:
        run, bar_series = _dense_null_control_run()
        window_start = START
        window_end = START + timedelta(days=2)
        _timestamps, basis_values = _eligible_decisions_in_window(run, window_start, window_end)
        event_count = len(basis_values)

        original_first = AuthoritativeTradableBarSeriesV2.first_eligible_bar_after
        calls = {"first_eligible": 0}

        def counting_first(self: AuthoritativeTradableBarSeriesV2, decision_at: datetime) -> object:
            calls["first_eligible"] += 1
            return original_first(self, decision_at)

        AuthoritativeTradableBarSeriesV2.first_eligible_bar_after = counting_first  # type: ignore[method-assign]
        try:
            evaluate_full_permutation_null_diagnostic_v1(
                run=run,
                bar_series=bar_series,
                base_cost_model=ZERO_COST,
                window_start=window_start,
                window_end=window_end,
                seed=1,
                permutations=8,
                minimum_valid_permutations=1,
            )
            small_calls = calls["first_eligible"]

            calls["first_eligible"] = 0
            evaluate_full_permutation_null_diagnostic_v1(
                run=run,
                bar_series=bar_series,
                base_cost_model=ZERO_COST,
                window_start=window_start,
                window_end=window_end,
                seed=1,
                permutations=250,
                minimum_valid_permutations=1,
            )
            large_calls = calls["first_eligible"]
        finally:
            AuthoritativeTradableBarSeriesV2.first_eligible_bar_after = original_first  # type: ignore[method-assign]

        self.assertEqual(small_calls, event_count)
        self.assertEqual(large_calls, event_count)


if __name__ == "__main__":
    unittest.main()
