"""Real PostgreSQL evidence for Module 3B.3 canonical liquidity/participation/capacity.

Every OHLCV value, turnover, price and timestamp here is a synthetic FIXTURE
written directly through the existing Historical Data Authority. No socket is
opened, no provider adapter runs, and nothing is retrieved from or verified
against Bybit. The canonical ``CRYPTO:BYBIT:BTCUSDT:PERP`` instrument and its
authorized Bybit linear source are onboarded through the existing onboarding, so
the Module 3B.2 typed volume/turnover sidecar is produced by the production
normalization path rather than by this test.

Two facts this file exists to prove on a real database:

* with three COMPLETE UTC days of typed 1-minute evidence and an explicit owner
  policy, canonical capacity evidence becomes AVAILABLE, sums only the
  provider-published quote turnover, and uses only days strictly before each
  order timestamp; and
* the Phase 3A-shaped 30-minute pilot window stays UNAVAILABLE for insufficient
  complete liquidity history -- it never becomes a 48x extrapolated day.

LIVE BYBIT CALLS PERFORMED: NO
LIVE ORDER/ACCOUNT CALLS PERFORMED: NO
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from uuid import NAMESPACE_URL, uuid5

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

VENUE = "BYBIT"
SYMBOL = "BTCUSDT"
INSTRUMENT_ID = "CRYPTO:BYBIT:BTCUSDT:PERP"

ONBOARDED_AT = datetime(2026, 9, 15, tzinfo=UTC)
NORMALIZATION_VERSION = "bybit-v5-md-phase3b3"
DATASET_VERSION = "bybit-v5-linear-btcusdt-phase3b3-liquidity"
PILOT_DATASET_VERSION = "bybit-v5-linear-btcusdt-phase3b3-pilot"

COMPLETE_DAYS = (date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17))
PILOT_DAY = date(2026, 9, 19)
MINUTES_PER_DAY = 1440
PILOT_MINUTES = 30
DATASET_CREATED_AT = datetime(2026, 9, 20, tzinfo=UTC)
RESEARCH_RUN_AT = datetime(2026, 9, 21, tzinfo=UTC)

VOLUME = "12.5"

#: Day k, minute i publishes a quote turnover of ``1000.5 + i + 10000*k``. No
#: two bars agree, and the figure is nowhere near ``volume * close``
#: (12.5 * ~27000 = ~337500), so an exact daily sum can only have come from the
#: provider-published turnover.
def _turnover(day_index: int, minute_index: int) -> Decimal:
    return Decimal("1000.5") + Decimal(minute_index) + Decimal(10000 * day_index)


def _close(minute_index: int) -> Decimal:
    return Decimal(27000) + Decimal(minute_index)


_CHECK = Context(prec=34, rounding=ROUND_HALF_EVEN)


def _disposable_dsn(source_dsn: str, database_name: str) -> str:
    parsed = urlparse(source_dsn)
    return urlunparse(parsed._replace(path=f"/{database_name}"))


def _migrate(dsn: str) -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    )
    old_dsn = os.environ.get("POSTGRES_TEST_DSN")
    try:
        os.environ["POSTGRES_TEST_DSN"] = dsn
        command.upgrade(config, "head")
    finally:
        if old_dsn is not None:
            os.environ["POSTGRES_TEST_DSN"] = old_dsn


@unittest.skipUnless(os.environ.get("POSTGRES_TEST_DSN"), "POSTGRES_TEST_DSN not configured")
class CanonicalBybitLiquidityCapacityPostgresTests(unittest.TestCase):
    database_name: str
    dsn: str

    @classmethod
    def setUpClass(cls) -> None:
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        if urlparse(source_dsn).hostname not in LOCAL_HOSTS:
            raise unittest.SkipTest(
                "Phase 3B.3 liquidity capacity requires a local or CI disposable PostgreSQL DSN"
            )
        cls.database_name = f"crypto_liquidity_capacity_phase3b3_{os.getpid()}"
        cls.dsn = _disposable_dsn(source_dsn, cls.database_name)
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')
        _migrate(cls.dsn)
        cls._build_evidence()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()
        source_dsn = os.environ["POSTGRES_TEST_DSN"]
        with psycopg.connect(source_dsn, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(f'DROP DATABASE IF EXISTS "{cls.database_name}" WITH (FORCE)')

    # -- fixture construction -------------------------------------------------

    @classmethod
    def _build_evidence(cls) -> None:
        from trade_platform.bybit_instrument_onboarding import (
            captured_btcusdt_snapshot_v1,
            onboard_bybit_btcusdt_perpetual_v1,
        )
        from trade_platform.historical_market_data import (
            AdjustmentStatus,
            ObservationKind,
            PostgresHistoricalMarketDataPipeline,
            RawHistoricalObservation,
        )
        from trade_platform.persistence import PostgresDatabase
        from trade_platform.tradable_bar_evidence_v2 import (
            BAR_TIMESTAMP_SEMANTICS_MARKER_V1,
            PostgresTradableBarEvidenceReaderV2,
        )

        cls.database = PostgresDatabase(cls.dsn)
        onboarding = onboard_bybit_btcusdt_perpetual_v1(
            cls.database, captured_btcusdt_snapshot_v1(), ONBOARDED_AT
        )
        assert onboarding.instrument_id == INSTRUMENT_ID
        cls.source_id = onboarding.source_id
        pipeline = PostgresHistoricalMarketDataPipeline(cls.database)

        def raw(bar_open: datetime, *, turnover: Decimal, minute_index: int):
            close = _close(minute_index)
            return RawHistoricalObservation(
                source_id=cls.source_id,
                observation_kind=ObservationKind.OHLCV,
                provider_identifier=SYMBOL,
                provider_symbol=SYMBOL,
                exchange=VENUE,
                event_at=bar_open,
                effective_at=bar_open + timedelta(minutes=1),
                ingested_at=bar_open + timedelta(minutes=2),
                adjustment_status=AdjustmentStatus.AS_REPORTED,
                revision=0,
                provenance_uri=f"fixture://phase3b3/ohlcv/{bar_open.isoformat()}",
                raw_payload={
                    "bar_timestamp_semantics": BAR_TIMESTAMP_SEMANTICS_MARKER_V1,
                    "interval": "1m",
                    "open": str(close - 5),
                    "high": str(close + 10),
                    "low": str(close - 10),
                    "close": str(close),
                    "volume": VOLUME,
                    "provider_category": "linear",
                    "provider_interval": "1",
                    "provider_turnover": str(turnover),
                },
            )

        cls.daily_turnover: dict[date, Decimal] = {}
        observations = []
        for day_index, day in enumerate(COMPLETE_DAYS):
            midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
            total = Decimal("0")
            for minute_index in range(MINUTES_PER_DAY):
                turnover = _turnover(day_index, minute_index)
                total += turnover
                observations.append(
                    raw(
                        midnight + minute_index * timedelta(minutes=1),
                        turnover=turnover,
                        minute_index=minute_index,
                    )
                )
            cls.daily_turnover[day] = total

        pilot_midnight = datetime(PILOT_DAY.year, PILOT_DAY.month, PILOT_DAY.day, tzinfo=UTC)
        pilot_observations = [
            raw(
                pilot_midnight + minute_index * timedelta(minutes=1),
                turnover=_turnover(9, minute_index),
                minute_index=minute_index,
            )
            for minute_index in range(PILOT_MINUTES)
        ]

        raw_ids = pipeline.capture_raw(observations)
        pilot_raw_ids = pipeline.capture_raw(pilot_observations)
        normalized_ids = tuple(
            pipeline.normalize(
                raw_id, NORMALIZATION_VERSION, observation.ingested_at + timedelta(seconds=30)
            ).normalized_observation_id
            for raw_id, observation in zip(raw_ids, observations, strict=True)
        )
        pilot_normalized_ids = tuple(
            pipeline.normalize(
                raw_id, NORMALIZATION_VERSION, observation.ingested_at + timedelta(seconds=30)
            ).normalized_observation_id
            for raw_id, observation in zip(pilot_raw_ids, pilot_observations, strict=True)
        )

        cls.dataset = pipeline.seal_dataset(
            cls.source_id, DATASET_VERSION, NORMALIZATION_VERSION, normalized_ids,
            DATASET_CREATED_AT,
        )
        cls.pilot_dataset = pipeline.seal_dataset(
            cls.source_id, PILOT_DATASET_VERSION, NORMALIZATION_VERSION, pilot_normalized_ids,
            DATASET_CREATED_AT,
        )
        reader = PostgresTradableBarEvidenceReaderV2(cls.database)
        cls.series = reader.series(
            dataset_version_id=cls.dataset.dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            interval="1m",
            research_run_at=RESEARCH_RUN_AT,
        )
        cls.pilot_series = reader.series(
            dataset_version_id=cls.pilot_dataset.dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            interval="1m",
            research_run_at=RESEARCH_RUN_AT,
        )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _policy():
        from trade_platform.crypto_liquidity_capacity_v1 import LiquidityCapacityPolicyV1

        return LiquidityCapacityPolicyV1(
            policy_version="fixture-postgres-capacity-policy-3b3-v1",
            lookback_complete_days=2,
            minimum_complete_days=2,
            maximum_participation=Decimal("0.05"),
            reduced_liquidity_multipliers=(Decimal("0.25"), Decimal("0.50")),
        )

    @classmethod
    def _run(cls, series, entry_at: datetime, *, exposure=Decimal("0.5")):
        from trade_platform.crypto_basis_mean_reversion_v1 import (
            BasisMeanReversionDecisionV1,
            BasisMeanReversionOutcomeV1,
            BasisMeanReversionResearchRunV1,
            BasisMeanReversionTradeV1,
            CryptoBasisMeanReversionDefinitionV1,
        )
        from trade_platform.signed_research_exposure_v2 import SignedResearchSignalObservationV2

        exit_at = entry_at + timedelta(minutes=2)
        entry_bar = series.bar_at_open_time(entry_at)
        exit_bar = series.bar_at_open_time(exit_at)
        assert entry_bar is not None and exit_bar is not None
        trade = BasisMeanReversionTradeV1(
            feature_materialization_id=uuid5(NAMESPACE_URL, f"phase3b3-mat:{entry_at.isoformat()}"),
            feature_materialization_content_hash="c" * 64,
            decision_at=entry_at - timedelta(minutes=1),
            basis_value=Decimal("0.002"),
            exposure=exposure,
            entry_bar=entry_bar,
            exit_bar=exit_bar,
            entry_time=entry_bar.bar_open_at,
            exit_time=exit_bar.bar_open_at,
            entry_open=entry_bar.open,
            exit_open=exit_bar.open,
            cost_model_version="fixture-cost-v1",
            gross_return=Decimal("0"),
            entry_cost=Decimal("0"),
            exit_cost=Decimal("0"),
            net_return=Decimal("0"),
        )
        observation = SignedResearchSignalObservationV2(
            instrument_id=INSTRUMENT_ID,
            decision_at=trade.decision_at,
            exposure=exposure,
            maximum_absolute_exposure=Decimal("0.5"),
            evidence_content_hash="e" * 64,
        )
        content_hash = f"{entry_at.isoformat()}".ljust(64, "0")[:64]
        return BasisMeanReversionResearchRunV1(
            definition=CryptoBasisMeanReversionDefinitionV1(
                basis_entry_threshold=Decimal("0.001"),
                holding_horizon_bars=2,
                maximum_absolute_exposure=Decimal("0.5"),
            ),
            evidence_content_hash="f" * 64,
            dataset_version_id=series.dataset_version_id,
            instrument_id=INSTRUMENT_ID,
            cost_model_version="fixture-cost-v1",
            decisions=(
                BasisMeanReversionDecisionV1(
                    feature_materialization_id=trade.feature_materialization_id,
                    feature_materialization_content_hash=trade.feature_materialization_content_hash,
                    basis_value=trade.basis_value,
                    outcome=BasisMeanReversionOutcomeV1.EXECUTED,
                    signal_observation=observation,
                    trade=trade,
                ),
            ),
            content_hash=content_hash,
            run_id=uuid5(NAMESPACE_URL, f"phase3b3-run:{content_hash}"),
        )

    @staticmethod
    def _midnight(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=UTC)

    # -- tests ----------------------------------------------------------------

    def test_typed_3b2_sidecars_are_consumed_with_no_raw_payload_parsing(self) -> None:
        from trade_platform.ohlcv_volume_semantics import (
            BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION,
            BarVolumeUnit,
        )

        self.assertEqual(len(self.series.bars), len(COMPLETE_DAYS) * MINUTES_PER_DAY)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM historical_normalized_observations n "
                "JOIN historical_raw_observations r "
                "  ON r.raw_observation_id=n.raw_observation_id "
                "LEFT JOIN historical_ohlcv_volume_semantics vs "
                "  ON vs.normalized_observation_id=n.normalized_observation_id "
                "WHERE r.observation_kind='OHLCV' AND vs.normalized_observation_id IS NULL"
            )
            self.assertEqual(int(str(cursor.fetchone()[0])), 0)
        for bar in self.series.bars[:5]:
            self.assertEqual(bar.volume_unit, BarVolumeUnit.BASE_ASSET)
            self.assertEqual(bar.volume_asset, "BTC")
            self.assertEqual(bar.turnover_unit, BarVolumeUnit.QUOTE_ASSET)
            self.assertEqual(bar.turnover_asset, "USDT")
            self.assertEqual(
                bar.volume_semantic_version, BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION
            )

    def test_capacity_becomes_available_with_complete_history_and_a_policy(self) -> None:
        from trade_platform.crypto_liquidity_capacity_v1 import (
            CRYPTO_LIQUIDITY_BASIS,
            STATUS_AVAILABLE,
            evaluate_crypto_liquidity_capacity_v1,
        )

        policy = self._policy()
        order_day = COMPLETE_DAYS[2]
        run = self._run(self.series, self._midnight(order_day) + timedelta(minutes=5))
        evidence = evaluate_crypto_liquidity_capacity_v1(
            bar_series=self.series,
            run=run,
            capital_levels=(Decimal("1000000"), Decimal("5000000")),
            policy=policy,
        )
        self.assertEqual(evidence.status, STATUS_AVAILABLE)
        self.assertEqual(evidence.liquidity_basis, CRYPTO_LIQUIDITY_BASIS)
        self.assertFalse(evidence.order_book_evidence)
        self.assertEqual(evidence.turnover_asset, "USDT")
        self.assertEqual(evidence.volume_asset, "BTC")
        self.assertEqual(evidence.instrument_id, INSTRUMENT_ID)
        self.assertEqual(evidence.dataset_version_id, self.dataset.dataset_version_id)
        self.assertEqual(evidence.dataset_content_hash, self.dataset.content_hash)
        self.assertEqual(evidence.policy_version, policy.policy_version)

        # ---- every complete UTC day is whole and its turnover exact ---------
        self.assertEqual([day.day for day in evidence.complete_days], list(COMPLETE_DAYS))
        for day in evidence.complete_days:
            self.assertEqual(day.bar_count, MINUTES_PER_DAY)
            self.assertEqual(day.quote_turnover, self.daily_turnover[day.day])
        self.assertEqual(evidence.excluded_days, ())

        # ---- the daily figure is the provider turnover, never volume*price --
        first_day_turnover = evidence.complete_days[0].quote_turnover
        self.assertNotEqual(
            first_day_turnover,
            sum(
                (Decimal(VOLUME) * _close(index) for index in range(MINUTES_PER_DAY)),
                Decimal("0"),
            ),
        )

        # ---- PIT cutoff: only days strictly before the order's UTC day ------
        for event in evidence.order_events:
            reference = event.trailing_liquidity
            assert reference is not None
            self.assertEqual(reference.days_used, (COMPLETE_DAYS[0], COMPLETE_DAYS[1]))
            self.assertNotIn(order_day, reference.days_used)

        expected_liquidity = _CHECK.divide(
            self.daily_turnover[COMPLETE_DAYS[0]] + self.daily_turnover[COMPLETE_DAYS[1]],
            Decimal(2),
        )
        envelope = evidence.baseline_envelope
        assert envelope is not None
        level = envelope.levels[0]
        expected_notional = _CHECK.multiply(Decimal("1000000"), Decimal("0.5"))
        self.assertEqual(level.maximum_order_notional, expected_notional)
        self.assertEqual(
            level.maximum_participation, _CHECK.divide(expected_notional, expected_liquidity)
        )
        self.assertEqual(level.unavailable_order_event_count, 0)
        self.assertEqual(
            envelope.capital_ceiling,
            _CHECK.divide(
                _CHECK.multiply(policy.maximum_participation, expected_liquidity), Decimal("0.5")
            ),
        )
        self.assertEqual(
            [item.liquidity_multiplier for item in evidence.stress_envelopes],
            [Decimal("0.25"), Decimal("0.50")],
        )

    def test_future_and_current_day_liquidity_never_enters_the_reference(self) -> None:
        from trade_platform.crypto_liquidity_capacity_v1 import (
            REASON_INSUFFICIENT_PRIOR_LIQUIDITY_HISTORY,
            STATUS_UNAVAILABLE,
            evaluate_crypto_liquidity_capacity_v1,
        )

        # An order on the SECOND complete day can only look back at the first,
        # which is one day short of the policy's minimum -- proving the two
        # later complete days in the very same sealed dataset are invisible to
        # it because they had not happened yet.
        run = self._run(self.series, self._midnight(COMPLETE_DAYS[1]) + timedelta(minutes=5))
        evidence = evaluate_crypto_liquidity_capacity_v1(
            bar_series=self.series,
            run=run,
            capital_levels=(Decimal("1000000"),),
            policy=self._policy(),
        )
        self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
        self.assertEqual(
            evidence.unavailable_reasons, (REASON_INSUFFICIENT_PRIOR_LIQUIDITY_HISTORY,)
        )
        self.assertEqual(len(evidence.complete_days), 3)
        for event in evidence.order_events:
            self.assertIsNone(event.trailing_liquidity)

    def test_dataset_identity_is_bound_and_reproducible(self) -> None:
        from trade_platform.crypto_liquidity_capacity_v1 import (
            evaluate_crypto_liquidity_capacity_v1,
        )

        run = self._run(self.series, self._midnight(COMPLETE_DAYS[2]) + timedelta(minutes=5))
        kwargs = {
            "bar_series": self.series,
            "run": run,
            "capital_levels": (Decimal("1000000"),),
            "policy": self._policy(),
        }
        first = evaluate_crypto_liquidity_capacity_v1(**kwargs)  # type: ignore[arg-type]
        second = evaluate_crypto_liquidity_capacity_v1(**kwargs)  # type: ignore[arg-type]
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertEqual(first.dataset_content_hash, self.dataset.content_hash)

    def test_thirty_minute_pilot_window_stays_insufficient_history(self) -> None:
        from trade_platform.crypto_liquidity_capacity_v1 import (
            REASON_INSUFFICIENT_COMPLETE_LIQUIDITY_DAYS,
            STATUS_UNAVAILABLE,
            evaluate_crypto_liquidity_capacity_v1,
        )

        self.assertEqual(len(self.pilot_series.bars), PILOT_MINUTES)
        run = self._run(self.pilot_series, self._midnight(PILOT_DAY) + timedelta(minutes=5))
        for policy in (None, self._policy()):
            with self.subTest(policy=None if policy is None else policy.policy_version):
                evidence = evaluate_crypto_liquidity_capacity_v1(
                    bar_series=self.pilot_series,
                    run=run,
                    capital_levels=(Decimal("1000000"),),
                    policy=policy,
                )
                self.assertEqual(evidence.status, STATUS_UNAVAILABLE)
                self.assertEqual(
                    evidence.unavailable_reasons,
                    (REASON_INSUFFICIENT_COMPLETE_LIQUIDITY_DAYS,),
                )
                self.assertEqual(evidence.complete_days, ())
                self.assertIsNone(evidence.baseline_envelope)
                self.assertEqual(evidence.stress_envelopes, ())
                # The 30 observed minutes are recorded only as an excluded
                # diagnostic; nothing scales them to a notional whole day.
                self.assertEqual(len(evidence.excluded_days), 1)
                self.assertEqual(evidence.excluded_days[0].observed_bar_count, PILOT_MINUTES)


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    unittest.main()
