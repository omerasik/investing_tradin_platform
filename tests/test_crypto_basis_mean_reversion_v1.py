"""Module 3J.2b.2a -- pure basis mean-reversion strategy core + trade ledger.

No PostgreSQL: every input authority this module consumes
(``SubjectAwareResearchFeatureBundle``, ``AuthoritativeTradableBarSeriesV2``,
``SubjectAwareTradableResearchEvidenceV2``) is a plain in-memory dataclass, so
fixtures are built directly, mirroring ``test_tradable_research_evidence_v2.py``.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trade_platform.crypto_basis_mean_reversion_v1 import (
    BasisMeanReversionOutcomeV1,
    CryptoBasisMeanReversionDefinitionV1,
    CryptoBasisMeanReversionLifecycleV1,
    CryptoBasisMeanReversionV1Error,
    run_crypto_basis_mean_reversion_research,
)
from trade_platform.crypto_instruments import CryptoInstrumentKind
from trade_platform.feature_authority import (
    FeatureMaterializationV2,
    FeatureQualityStatus,
    FeatureSubjectType,
)
from trade_platform.research import CostModel
from trade_platform.strategy_feature_binding_v2 import (
    AuthoritativeFeatureSeriesV2,
    ResearchFeatureRequirementV2,
    ResearchQualityPolicyV2,
    SubjectAwareResearchFeatureBundle,
)
from trade_platform.tradable_bar_evidence_v2 import (
    AuthoritativeTradableBarSeriesV2,
    AuthoritativeTradableBarV2,
)
from trade_platform.tradable_research_evidence_v2 import SubjectAwareTradableResearchEvidenceV2

START = datetime(2026, 1, 1, tzinfo=UTC)
DATASET_ID = uuid4()
OTHER_DATASET_ID = uuid4()
INSTRUMENT = "TESTFIXTURE:3J2B2A:BTCUSDT:PERP"
BASIS_FEATURE_ID = uuid4()
OI_FEATURE_ID = uuid4()
FUNDING_FEATURE_ID = uuid4()
ZERO_COST = CostModel()
NONZERO_COST = CostModel(
    fixed_per_turnover=Decimal("0"), percentage_per_turnover=Decimal("0.001"), spread_fraction_per_turnover=Decimal("0")
)


def materialization(
    *,
    feature_id=BASIS_FEATURE_ID,
    event_at: datetime,
    effective_at: datetime | None = None,
    knowledge_at: datetime | None = None,
    computed_at: datetime | None = None,
    value: Decimal | None = Decimal("0.001"),
    quality_status: FeatureQualityStatus = FeatureQualityStatus.VALIDATED,
    dataset_version_id=DATASET_ID,
    subject_id: str = INSTRUMENT,
    subject_type: FeatureSubjectType = FeatureSubjectType.INSTRUMENT,
) -> FeatureMaterializationV2:
    return FeatureMaterializationV2.create(
        feature_id=feature_id,
        subject_type=subject_type,
        subject_id=subject_id,
        dataset_version=str(dataset_version_id),
        event_at=event_at,
        effective_at=effective_at or event_at,
        knowledge_at=knowledge_at or event_at,
        computed_at=computed_at or event_at,
        source_observation_manifest=("fixture:manifest",),
        value=value,
        quality_status=quality_status,
    )


def basis_series(
    materializations: tuple[FeatureMaterializationV2, ...],
    *,
    feature_id=BASIS_FEATURE_ID,
    name: str = "crypto_mark_index_basis",
    semantic_version: str = "1.0.0",
    subject_id: str = INSTRUMENT,
    subject_type: FeatureSubjectType = FeatureSubjectType.INSTRUMENT,
    dataset_version_id=DATASET_ID,
) -> AuthoritativeFeatureSeriesV2:
    requirement = ResearchFeatureRequirementV2(
        feature_id=feature_id, name=name, semantic_version=semantic_version, expected_subject_type=subject_type
    )
    return AuthoritativeFeatureSeriesV2(
        requirement=requirement,
        subject_type=subject_type,
        subject_id=subject_id,
        dataset_version=str(dataset_version_id),
        materializations=materializations,
    )


def bundle(
    *,
    feature_series: tuple[AuthoritativeFeatureSeriesV2, ...],
    dataset_version_id=DATASET_ID,
    subject_id: str = INSTRUMENT,
    subject_type: FeatureSubjectType = FeatureSubjectType.INSTRUMENT,
    decision_at: datetime = START + timedelta(days=1),
) -> SubjectAwareResearchFeatureBundle:
    return SubjectAwareResearchFeatureBundle.create(
        dataset_version_id=dataset_version_id,
        subject_type=subject_type,
        subject_id=subject_id,
        decision_at=decision_at,
        quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY,
        feature_series=feature_series,
    )


def basis_bundle(
    *,
    values: list[Decimal],
    start: datetime = START,
    spacing: timedelta = timedelta(minutes=10),
    dataset_version_id=DATASET_ID,
    subject_id: str = INSTRUMENT,
    decision_at: datetime = START + timedelta(days=1),
) -> SubjectAwareResearchFeatureBundle:
    materializations = tuple(
        materialization(
            event_at=start + index * spacing, value=value, dataset_version_id=dataset_version_id, subject_id=subject_id
        )
        for index, value in enumerate(values)
    )
    series = basis_series(materializations, dataset_version_id=dataset_version_id, subject_id=subject_id)
    return bundle(
        feature_series=(series,), dataset_version_id=dataset_version_id, subject_id=subject_id, decision_at=decision_at
    )


def bar(
    dataset_version_id, instrument_id: str, bar_open_at: datetime, *, open_price: Decimal = Decimal("100")
) -> AuthoritativeTradableBarV2:
    return AuthoritativeTradableBarV2(
        dataset_version_id=dataset_version_id,
        dataset_content_hash="e" * 64,
        source_id=uuid4(),
        normalized_observation_id=uuid4(),
        raw_observation_id=uuid4(),
        raw_payload_sha256="f" * 64,
        instrument_id=instrument_id,
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


def minute_bar_series(
    *,
    opens: dict[int, Decimal],
    minutes: int = 120,
    start: datetime = START,
    dataset_version_id=DATASET_ID,
    instrument_id: str = INSTRUMENT,
) -> AuthoritativeTradableBarSeriesV2:
    """A dense, gap-free 1m bar series from ``start`` for ``minutes`` bars.

    ``opens`` overrides specific minute offsets' open prices; every other bar
    defaults to ``Decimal("100")``.
    """
    bars = tuple(
        bar(dataset_version_id, instrument_id, start + timedelta(minutes=offset), open_price=opens.get(offset, Decimal("100")))
        for offset in range(minutes)
    )
    series = AuthoritativeTradableBarSeriesV2(dataset_version_id, instrument_id, "1m", bars)
    series.validate()
    return series


def evidence(
    feature_bundle: SubjectAwareResearchFeatureBundle, bar_series: AuthoritativeTradableBarSeriesV2
) -> SubjectAwareTradableResearchEvidenceV2:
    return SubjectAwareTradableResearchEvidenceV2.create(feature_bundle=feature_bundle, bar_series=bar_series)


def definition(
    *,
    threshold: Decimal = Decimal("0.0005"),
    horizon: int = 10,
    cap: Decimal = Decimal("1"),
) -> CryptoBasisMeanReversionDefinitionV1:
    return CryptoBasisMeanReversionDefinitionV1(
        basis_entry_threshold=threshold, holding_horizon_bars=horizon, maximum_absolute_exposure=cap
    )


def run(
    *,
    strategy_definition: CryptoBasisMeanReversionDefinitionV1 | None = None,
    research_evidence: SubjectAwareTradableResearchEvidenceV2,
    instrument_kind: CryptoInstrumentKind = CryptoInstrumentKind.PERPETUAL,
    cost_model: CostModel = ZERO_COST,
    cost_model_version: str = "cost-model-v1",
):
    return run_crypto_basis_mean_reversion_research(
        definition=strategy_definition or definition(),
        evidence=research_evidence,
        instrument_kind=instrument_kind,
        cost_model=cost_model,
        cost_model_version=cost_model_version,
    )


class DefinitionTests(unittest.TestCase):
    def test_metadata_and_version_and_status(self) -> None:
        d = definition()
        self.assertEqual(d.strategy_name, "crypto_basis_mean_reversion")
        self.assertEqual(d.semantic_version, "1.0.0")
        self.assertEqual(d.lifecycle, CryptoBasisMeanReversionLifecycleV1.RESEARCH_ONLY)
        self.assertEqual(d.required_subject_type, FeatureSubjectType.INSTRUMENT)
        self.assertEqual(d.required_instrument_kind, CryptoInstrumentKind.PERPETUAL)
        self.assertEqual(d.required_feature_name, "crypto_mark_index_basis")
        self.assertEqual(d.required_feature_semantic_version, "1.0.0")
        d.validate()

    def test_invalid_threshold_rejected(self) -> None:
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "basis_entry_threshold_must_be_positive"):
            definition(threshold=Decimal("0")).validate()
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "basis_entry_threshold_must_be_positive"):
            definition(threshold=Decimal("-0.001")).validate()
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "basis_entry_threshold_must_be_finite"):
            definition(threshold=Decimal("Infinity")).validate()

    def test_holding_horizon_below_one_rejected(self) -> None:
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "holding_horizon_bars_must_be_at_least_one"):
            definition(horizon=0).validate()

    def test_invalid_exposure_cap_rejected(self) -> None:
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "maximum_absolute_exposure_out_of_bounds"):
            definition(cap=Decimal("0")).validate()
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "maximum_absolute_exposure_out_of_bounds"):
            definition(cap=Decimal("1.5")).validate()

    def test_deterministic_definition_hash_changes_with_parameters(self) -> None:
        base = definition()
        self.assertEqual(base.content_hash(), definition().content_hash())
        self.assertNotEqual(base.content_hash(), definition(threshold=Decimal("0.001")).content_hash())
        self.assertNotEqual(base.content_hash(), definition(horizon=20).content_hash())
        self.assertNotEqual(base.content_hash(), definition(cap=Decimal("0.5")).content_hash())


class FeaturePurityTests(unittest.TestCase):
    def test_exactly_one_required_feature(self) -> None:
        bundle_evidence = evidence(basis_bundle(values=[Decimal("0")]), minute_bar_series(opens={}))
        result = run(research_evidence=bundle_evidence)
        self.assertEqual(len(result.decisions), 1)

    def test_additional_oi_feature_rejected(self) -> None:
        basis_mat = materialization(event_at=START, value=Decimal("0"))
        oi_mat = materialization(feature_id=OI_FEATURE_ID, event_at=START, value=Decimal("1"))
        oi_series = basis_series((oi_mat,), feature_id=OI_FEATURE_ID, name="open_interest_change", semantic_version="1.0.0")
        b = bundle(feature_series=(basis_series((basis_mat,)), oi_series))
        e = evidence(b, minute_bar_series(opens={}))
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "requires_exactly_one_feature"):
            run(research_evidence=e)

    def test_funding_feature_rejected(self) -> None:
        basis_mat = materialization(event_at=START, value=Decimal("0"))
        funding_mat = materialization(feature_id=FUNDING_FEATURE_ID, event_at=START, value=Decimal("0.1"))
        funding_series = basis_series(
            (funding_mat,), feature_id=FUNDING_FEATURE_ID, name="crypto_realized_funding_annualized", semantic_version="1.0.0"
        )
        b = bundle(feature_series=(basis_series((basis_mat,)), funding_series))
        e = evidence(b, minute_bar_series(opens={}))
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "requires_exactly_one_feature"):
            run(research_evidence=e)

    def test_wrong_required_feature_rejected(self) -> None:
        mat = materialization(feature_id=OI_FEATURE_ID, event_at=START, value=Decimal("1"))
        series = basis_series((mat,), feature_id=OI_FEATURE_ID, name="open_interest_change", semantic_version="1.0.0")
        b = bundle(feature_series=(series,))
        e = evidence(b, minute_bar_series(opens={}))
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "wrong_required_feature"):
            run(research_evidence=e)


class DirectionClassificationTests(unittest.TestCase):
    def test_positive_basis_above_threshold_is_short(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        result = run(research_evidence=e)
        self.assertEqual(result.decisions[0].exposure, Decimal("-1"))
        self.assertEqual(result.decisions[0].signal_observation.direction, "SHORT")

    def test_negative_basis_below_threshold_is_long(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("-0.001")]), minute_bar_series(opens={}))
        result = run(research_evidence=e)
        self.assertEqual(result.decisions[0].exposure, Decimal("1"))
        self.assertEqual(result.decisions[0].signal_observation.direction, "LONG")

    def test_inside_threshold_is_flat(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.0001")]), minute_bar_series(opens={}))
        result = run(research_evidence=e)
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.FLAT)
        self.assertEqual(result.decisions[0].exposure, Decimal("0"))

    def test_exact_positive_threshold_is_flat(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.0005")]), minute_bar_series(opens={}))
        result = run(research_evidence=e)
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.FLAT)

    def test_exact_negative_threshold_is_flat(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("-0.0005")]), minute_bar_series(opens={}))
        result = run(research_evidence=e)
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.FLAT)

    def test_non_finite_basis_rejected(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("Infinity")]), minute_bar_series(opens={}))
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "non_finite_basis"):
            run(research_evidence=e)


class SubjectAndInstrumentGateTests(unittest.TestCase):
    def test_wrong_subject_rejected(self) -> None:
        # A self-consistent FUTURES_SERIES-subject bundle (passes its own
        # validate()), paired directly (bypassing composite create()'s
        # pairing check) with an unrelated INSTRUMENT bar series -- proves
        # this module's OWN subject-type gate, not merely the upstream
        # bundle/series-internal consistency check.
        subject_id = "TESTFIXTURE:3J2B2A:SERIES:GC"
        requirement = ResearchFeatureRequirementV2(
            feature_id=BASIS_FEATURE_ID,
            name="crypto_mark_index_basis",
            semantic_version="1.0.0",
            expected_subject_type=FeatureSubjectType.FUTURES_SERIES,
        )
        mat = FeatureMaterializationV2.create(
            feature_id=BASIS_FEATURE_ID,
            subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=subject_id,
            dataset_version=str(DATASET_ID),
            event_at=START,
            effective_at=START,
            knowledge_at=START,
            computed_at=START,
            source_observation_manifest=("fixture:manifest",),
            value=Decimal("0.001"),
            quality_status=FeatureQualityStatus.VALIDATED,
        )
        series = AuthoritativeFeatureSeriesV2(
            requirement=requirement,
            subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=subject_id,
            dataset_version=str(DATASET_ID),
            materializations=(mat,),
        )
        futures_bundle = SubjectAwareResearchFeatureBundle.create(
            dataset_version_id=DATASET_ID,
            subject_type=FeatureSubjectType.FUTURES_SERIES,
            subject_id=subject_id,
            decision_at=START + timedelta(days=1),
            quality_policy=ResearchQualityPolicyV2.VALIDATED_ONLY,
            feature_series=(series,),
        )
        malformed = SubjectAwareTradableResearchEvidenceV2(
            feature_bundle=futures_bundle, bar_series=minute_bar_series(opens={}), content_hash="0" * 64
        )
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "wrong_subject_type"):
            run(research_evidence=malformed)

    def test_non_perpetual_instrument_rejected(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0")]), minute_bar_series(opens={}))
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "requires_perpetual_instrument"):
            run(research_evidence=e, instrument_kind=CryptoInstrumentKind.SPOT)

    def test_wrong_dataset_rejected_through_composite_authority(self) -> None:
        from trade_platform.tradable_research_evidence_v2 import TradableResearchEvidenceV2Error

        mismatched_bundle = basis_bundle(values=[Decimal("0")], dataset_version_id=DATASET_ID)
        mismatched_bars = minute_bar_series(opens={}, dataset_version_id=OTHER_DATASET_ID)
        with self.assertRaisesRegex(TradableResearchEvidenceV2Error, "dataset_mismatch"):
            evidence(mismatched_bundle, mismatched_bars)


class DecisionTimingTests(unittest.TestCase):
    def test_decision_time_uses_max_of_four_pit_clocks(self) -> None:
        mat = materialization(
            event_at=START,
            effective_at=START + timedelta(minutes=1),
            knowledge_at=START + timedelta(minutes=2),
            computed_at=START + timedelta(minutes=3),
            value=Decimal("0.001"),
        )
        series = basis_series((mat,))
        b = bundle(feature_series=(series,), decision_at=START + timedelta(minutes=5))
        e = evidence(b, minute_bar_series(opens={}))
        result = run(research_evidence=e)
        self.assertEqual(result.decisions[0].decision_at, START + timedelta(minutes=3))

    def test_future_known_feature_cannot_create_decision(self) -> None:
        good = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        future_mat = FeatureMaterializationV2(
            feature_id=BASIS_FEATURE_ID,
            subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=INSTRUMENT,
            dataset_version=str(DATASET_ID),
            event_at=START,
            effective_at=START,
            knowledge_at=START,
            computed_at=good.feature_bundle.decision_at + timedelta(days=1),
            source_observation_manifest=("fixture:manifest",),
            value=Decimal("0.001"),
            quality_status=FeatureQualityStatus.VALIDATED,
            content_hash="a" * 64,
        )
        malformed_bundle = SubjectAwareResearchFeatureBundle(
            dataset_version_id=good.feature_bundle.dataset_version_id,
            subject_type=FeatureSubjectType.INSTRUMENT,
            subject_id=good.feature_bundle.subject_id,
            decision_at=good.feature_bundle.decision_at,
            quality_policy=good.feature_bundle.quality_policy,
            feature_series=(basis_series((future_mat,)),),
            content_hash=good.feature_bundle.content_hash,
            bundle_id=good.feature_bundle.bundle_id,
        )
        malformed_evidence = SubjectAwareTradableResearchEvidenceV2(
            feature_bundle=malformed_bundle, bar_series=good.bar_series, content_hash=good.content_hash
        )
        from trade_platform.strategy_feature_binding_v2 import StrategyFeatureBindingV2Error

        with self.assertRaisesRegex(StrategyFeatureBindingV2Error, "future_feature_knowledge"):
            run(research_evidence=malformed_evidence)


class EntryExitTests(unittest.TestCase):
    def test_entry_bar_at_exact_decision_timestamp_rejected(self) -> None:
        # Only a bar exactly AT decision_at exists (no later bar) -> excluded.
        decision_at = START + timedelta(minutes=5)
        b = basis_bundle(values=[Decimal("0.001")], start=decision_at, spacing=timedelta(0))
        series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", (bar(DATASET_ID, INSTRUMENT, decision_at),))
        series.validate()
        e = evidence(b, series)
        result = run(research_evidence=e)
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_ENTRY)

    def test_first_bar_strictly_after_decision_selected(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        result = run(research_evidence=e)
        trade = result.decisions[0].trade
        assert trade is not None
        self.assertEqual(trade.entry_bar.bar_open_at, START + timedelta(minutes=1))

    def test_no_future_entry_excluded_no_synthetic_trade(self) -> None:
        decision_at = START + timedelta(minutes=118)
        b = basis_bundle(values=[Decimal("0.001")], start=decision_at, spacing=timedelta(0))
        e = evidence(b, minute_bar_series(opens={}, minutes=119))
        result = run(research_evidence=e)
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_ENTRY)
        self.assertIsNone(result.decisions[0].trade)

    def test_exact_n_interval_exit_selected(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        result = run(research_evidence=e, strategy_definition=definition(horizon=10))
        trade = result.decisions[0].trade
        assert trade is not None
        self.assertEqual(trade.exit_bar.bar_open_at, trade.entry_bar.bar_open_at + timedelta(minutes=10))

    def test_missing_exact_exit_excluded(self) -> None:
        # Only 3 bars total; horizon=10 means no bar 10 minutes after entry exists.
        b = basis_bundle(values=[Decimal("0.001")])
        e = evidence(b, minute_bar_series(opens={}, minutes=3))
        result = run(research_evidence=e, strategy_definition=definition(horizon=10))
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_EXIT)
        self.assertIsNone(result.decisions[0].trade)

    def test_nearest_later_exit_not_substituted(self) -> None:
        # Remove exactly the bar at the required exit offset; a later bar exists
        # but must never be substituted.
        decision_at = START
        materializations = (materialization(event_at=decision_at, value=Decimal("0.001")),)
        b = bundle(feature_series=(basis_series(materializations),))
        bars = tuple(
            bar(DATASET_ID, INSTRUMENT, START + timedelta(minutes=offset))
            for offset in range(60)
            if offset != 11  # entry at minute 1, required exit at minute 1+10=11 is missing
        )
        series = AuthoritativeTradableBarSeriesV2(DATASET_ID, INSTRUMENT, "1m", bars)
        series.validate()
        e = evidence(b, series)
        result = run(research_evidence=e, strategy_definition=definition(horizon=10))
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.EXCLUDED_MISSING_EXIT)

    def test_mark_index_basis_cannot_become_execution_price(self) -> None:
        # The only price fields ever read for entry/exit are bar.open values
        # sourced from AuthoritativeTradableBarV2 -- never the basis/feature
        # value. Confirmed structurally: entry_open/exit_open always trace to
        # bar_series bars, and basis_value is carried only as provenance.
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={1: Decimal("12345")}))
        result = run(research_evidence=e, strategy_definition=definition(horizon=1))
        trade = result.decisions[0].trade
        assert trade is not None
        self.assertEqual(trade.entry_open, Decimal("12345"))
        self.assertNotEqual(trade.entry_open, result.decisions[0].basis_value)


class LifecycleAndOverlapTests(unittest.TestCase):
    def test_open_trade_ignores_later_entry_candidate_no_pyramiding(self) -> None:
        values = [Decimal("0.001"), Decimal("-0.001")]
        b = basis_bundle(values=values, start=START, spacing=timedelta(minutes=1))
        e = evidence(b, minute_bar_series(opens={}, minutes=60))
        result = run(research_evidence=e, strategy_definition=definition(horizon=10))
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.EXECUTED)
        self.assertEqual(result.decisions[1].outcome, BasisMeanReversionOutcomeV1.IGNORED_ACTIVE_TRADE)
        self.assertEqual(result.executed_trade_count, 1)

    def test_next_trade_requires_strictly_later_decision_than_prior_exit(self) -> None:
        first_decision_at = START
        # entry at minute 1, exit at minute 1+10=11 for horizon=10
        second_decision_at_equal_exit = START + timedelta(minutes=11)
        third_decision_at_after_exit = START + timedelta(minutes=12)
        materializations = (
            materialization(event_at=first_decision_at, value=Decimal("0.001")),
            materialization(event_at=second_decision_at_equal_exit, value=Decimal("-0.001")),
            materialization(event_at=third_decision_at_after_exit, value=Decimal("-0.001")),
        )
        b = bundle(feature_series=(basis_series(materializations),))
        e = evidence(b, minute_bar_series(opens={}, minutes=60))
        result = run(research_evidence=e, strategy_definition=definition(horizon=10))
        self.assertEqual(result.decisions[0].outcome, BasisMeanReversionOutcomeV1.EXECUTED)
        self.assertEqual(result.decisions[1].outcome, BasisMeanReversionOutcomeV1.IGNORED_ACTIVE_TRADE)
        self.assertEqual(result.decisions[2].outcome, BasisMeanReversionOutcomeV1.EXECUTED)
        self.assertEqual(result.executed_trade_count, 2)


class ReturnAccountingTests(unittest.TestCase):
    def test_long_profitable_fixture(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("-0.001")]), minute_bar_series(opens={1: Decimal("100"), 2: Decimal("110")}))
        result = run(research_evidence=e, strategy_definition=definition(horizon=1))
        trade = result.decisions[0].trade
        assert trade is not None
        self.assertEqual(trade.exposure, Decimal("1"))
        self.assertEqual(trade.gross_return, Decimal("0.1"))
        self.assertEqual(trade.net_return, Decimal("0.1"))

    def test_long_losing_fixture(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("-0.001")]), minute_bar_series(opens={1: Decimal("100"), 2: Decimal("90")}))
        result = run(research_evidence=e, strategy_definition=definition(horizon=1))
        trade = result.decisions[0].trade
        assert trade is not None
        self.assertEqual(trade.gross_return, Decimal("-0.1"))
        self.assertEqual(trade.net_return, Decimal("-0.1"))

    def test_short_profitable_fixture(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={1: Decimal("100"), 2: Decimal("90")}))
        result = run(research_evidence=e, strategy_definition=definition(horizon=1))
        trade = result.decisions[0].trade
        assert trade is not None
        self.assertEqual(trade.exposure, Decimal("-1"))
        self.assertEqual(trade.gross_return, Decimal("0.1"))
        self.assertEqual(trade.net_return, Decimal("0.1"))

    def test_short_losing_fixture(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={1: Decimal("100"), 2: Decimal("110")}))
        result = run(research_evidence=e, strategy_definition=definition(horizon=1))
        trade = result.decisions[0].trade
        assert trade is not None
        self.assertEqual(trade.gross_return, Decimal("-0.1"))
        self.assertEqual(trade.net_return, Decimal("-0.1"))

    def test_entry_and_exit_costs_both_charged(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("-0.001")]), minute_bar_series(opens={1: Decimal("100"), 2: Decimal("110")}))
        result = run(research_evidence=e, strategy_definition=definition(horizon=1), cost_model=NONZERO_COST)
        trade = result.decisions[0].trade
        assert trade is not None
        self.assertEqual(trade.entry_cost, Decimal("0.001"))
        self.assertEqual(trade.exit_cost, Decimal("0.001"))
        self.assertEqual(trade.net_return, trade.gross_return - trade.entry_cost - trade.exit_cost)

    def test_funding_absent(self) -> None:
        import trade_platform.crypto_basis_mean_reversion_v1 as module

        self.assertNotIn("apply_funding", module.__dict__)
        self.assertNotIn("paper_execution", module.__dict__)
        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            contents = handle.read()
        # The module docstring names "apply_funding"/"paper_execution" only in
        # prose explaining why they are NOT used -- assert no import statement.
        self.assertNotIn("import paper_execution", contents)
        self.assertNotIn("from .paper_execution", contents)
        # These labels are prohibited as CLAIMS about the strategy's identity,
        # not as negated mentions -- the module docstring names them only to
        # say the strategy must NOT be described this way. There must be no
        # class/function/constant actually named after any of them.
        normalized = " ".join(contents.lower().split())
        sentence_start = normalized.index("it must not be described as a funding strategy")
        sentence = normalized[sentence_start : sentence_start + 200]
        for forbidden in ("funding strategy", "carry strategy", "funding capture", "funding arbitrage"):
            self.assertIn(forbidden, sentence)
        for identifier in dir(module):
            self.assertNotIn("funding", identifier.lower())


class DeterminismAndHashingTests(unittest.TestCase):
    def test_deterministic_decision_evidence_hash(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        first = run(research_evidence=e)
        second = run(research_evidence=e)
        self.assertEqual(first.decisions[0].evidence_content_hash, second.decisions[0].evidence_content_hash)
        self.assertEqual(len(first.decisions[0].evidence_content_hash), 64)

    def test_changed_feature_content_hash_changes_decision_and_run_identity(self) -> None:
        base_evidence = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        changed_evidence = evidence(basis_bundle(values=[Decimal("0.002")]), minute_bar_series(opens={}))
        base_run = run(research_evidence=base_evidence)
        changed_run = run(research_evidence=changed_evidence)
        self.assertNotEqual(base_run.decisions[0].evidence_content_hash, changed_run.decisions[0].evidence_content_hash)
        self.assertNotEqual(base_run.content_hash, changed_run.content_hash)

    def test_changed_parameter_changes_run_identity(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        base_run = run(research_evidence=e, strategy_definition=definition(threshold=Decimal("0.0005")))
        changed_run = run(research_evidence=e, strategy_definition=definition(threshold=Decimal("0.0006")))
        self.assertNotEqual(base_run.content_hash, changed_run.content_hash)

    def test_changed_cost_model_changes_run_identity(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("-0.001")]), minute_bar_series(opens={1: Decimal("100"), 2: Decimal("110")}))
        base_run = run(research_evidence=e, strategy_definition=definition(horizon=1), cost_model=ZERO_COST)
        changed_run = run(research_evidence=e, strategy_definition=definition(horizon=1), cost_model=NONZERO_COST)
        self.assertNotEqual(base_run.content_hash, changed_run.content_hash)

    def test_changed_cost_model_version_changes_run_identity(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        base_run = run(research_evidence=e, cost_model_version="cost-model-v1")
        changed_run = run(research_evidence=e, cost_model_version="cost-model-v2")
        self.assertNotEqual(base_run.content_hash, changed_run.content_hash)

    def test_identical_replay_gives_identical_run_hash_and_id(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001"), Decimal("-0.001")]), minute_bar_series(opens={}, minutes=60))
        first = run(research_evidence=e, strategy_definition=definition(horizon=10))
        second = run(research_evidence=e, strategy_definition=definition(horizon=10))
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.run_id, second.run_id)


class LedgerIntegrityTests(unittest.TestCase):
    def test_excluded_and_ignored_decisions_do_not_become_zero_return_trades(self) -> None:
        values = [Decimal("0.001"), Decimal("-0.001")]
        b = basis_bundle(values=values, start=START, spacing=timedelta(minutes=1))
        e = evidence(b, minute_bar_series(opens={}, minutes=60))
        result = run(research_evidence=e, strategy_definition=definition(horizon=10))
        ignored = [d for d in result.decisions if d.outcome is BasisMeanReversionOutcomeV1.IGNORED_ACTIVE_TRADE]
        self.assertTrue(ignored)
        for decision in ignored:
            self.assertIsNone(decision.trade)
        self.assertEqual(result.ignored_count, len(ignored))

    def test_trade_ledger_remains_chronological(self) -> None:
        values = [Decimal("0.001"), Decimal("0"), Decimal("-0.001")]
        b = basis_bundle(values=values, start=START, spacing=timedelta(minutes=20))
        e = evidence(b, minute_bar_series(opens={}, minutes=60))
        result = run(research_evidence=e, strategy_definition=definition(horizon=5))
        timestamps = [d.decision_at for d in result.decisions]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_no_persistence_or_new_table(self) -> None:
        import trade_platform.crypto_basis_mean_reversion_v1 as module

        source = module.__file__
        assert source is not None
        with open(source, encoding="utf-8") as handle:
            contents = handle.read()
        self.assertNotIn("CREATE TABLE", contents.upper())
        self.assertNotIn("INSERT INTO", contents.upper())

    def test_trend_v2_remains_unchanged(self) -> None:
        from trade_platform.trend_strategy_v2 import (
            TrendSignalObservation,
            TrendSignalState,
            TrendStrategyV2Error,
        )

        with self.assertRaises(TrendStrategyV2Error):
            TrendSignalObservation(
                event_at=START, exposure=Decimal("-0.1"), state=TrendSignalState.AVAILABLE, reason=None
            ).validate(Decimal("1"))

    def test_flat_and_no_trade_counts_exposed(self) -> None:
        values = [Decimal("0"), Decimal("0.001")]
        b = basis_bundle(values=values, start=START, spacing=timedelta(minutes=1))
        e = evidence(b, minute_bar_series(opens={}, minutes=60))
        result = run(research_evidence=e, strategy_definition=definition(horizon=10))
        self.assertEqual(result.flat_decision_count, 1)
        self.assertEqual(result.executed_trade_count, 1)
        self.assertEqual(len(result.trade_returns), 1)

    def test_run_does_not_expose_performance_statistics(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        result = run(research_evidence=e)
        forbidden_attrs = ("sharpe", "sortino", "calmar", "pbo", "dsr", "capacity")
        for attr in forbidden_attrs:
            self.assertFalse(hasattr(result, attr), f"unexpected performance attribute exposed: {attr}")


class CostModelValidationTests(unittest.TestCase):
    def test_cost_model_version_required(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "cost_model_version_required"):
            run(research_evidence=e, cost_model_version="")

    def test_negative_cost_model_field_rejected(self) -> None:
        e = evidence(basis_bundle(values=[Decimal("0.001")]), minute_bar_series(opens={}))
        bad_cost = CostModel(percentage_per_turnover=Decimal("-0.01"))
        with self.assertRaisesRegex(CryptoBasisMeanReversionV1Error, "cost_model_.*_invalid"):
            run(research_evidence=e, cost_model=bad_cost)


if __name__ == "__main__":
    unittest.main()
