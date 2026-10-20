"""Authorized temporary fixtures only; no market acquisition or database writes."""
from dataclasses import replace
from datetime import timedelta
from decimal import localcontext
from unittest.mock import patch
from uuid import uuid4

from tests.test_first_party_t4_forward_bars_v1 import (
    LATER,
    _acceptance,
    _ForwardFixture,
    _micros,
    _venue_minute,
)
from tests.test_paper_economics_v1 import GROSS, fill, hashed, only
from trade_platform.paper_economics_v1 import paper_economics_v1
from trade_platform.paper_proven_prices_v1 import resolve_paper_trade_price_v1
from trade_platform.strategy_lab_study_v1 import identity_hash_v1


class ProvenPriceTests(_ForwardFixture):
    def setUp(self):
        super().setUp()
        self.sealed = self.seal()
        self.instant = _venue_minute(2)

    def resolve(self, **overrides):
        options = {"store": self.store(), "capture_root": self.capture,
                       "source_id": self.sealed.source_id, "session_id": self.session_id,
                       "instrument": self.sealed.identity["instrument"], "symbol": "BTCUSDT",
                       "instant": self.instant, "entry_bar_open_micros": _micros(_venue_minute(1)),
                       "as_of": LATER, "max_age_micros": 3 * 86400 * 1000000,
                       "capture_max_unproven_nanos": 30_000_000_000}
        seals = overrides.pop("seals", [self.sealed])
        options.update(overrides)
        return resolve_paper_trade_price_v1(seals, **options)

    def accepted(self, **overrides):
        # Four-minute archive cannot prove an hour: isolate the existing hourly
        # evaluator for economic/clock tests. Unmocked coverage refusal is below.
        with patch("trade_platform.paper_proven_prices_v1.derive_hourly_acceptance_v1",
                   return_value=_acceptance(self.session_id)):
            return self.resolve(**overrides)

    def test_reference_rederives_and_projects_only_unrealized_gross(self):
        result = self.accepted()
        self.assertEqual(result.status, "AVAILABLE", result.reasons)
        self.assertEqual(result.content_hash, self.accepted().content_hash)
        self.assertEqual(result.mark.price, format(result.reference.price, "f"))
        self.assertEqual(result.mark.source_content_hash, identity_hash_v1(result.evidence))
        self.assertIn("host_arrival_utc_nanos", result.evidence)
        self.assertIn("venue_trade_ts_millis", result.evidence)
        self.assertEqual(result.evidence["bar"]["clock"], result.reference.bar.clock.payload())
        entry = fill(0, price="84000", base=_micros(_venue_minute(1)))
        projected = only(paper_economics_v1([hashed(entry)], GROSS, as_of=LATER, marks=[result.mark]))
        self.assertEqual(projected["gross_unrealized"]["status"], "AVAILABLE")
        self.assertEqual(projected["net"]["status"], "NOT_COSTABLE")
        self.assertIsNone(projected["net"]["by_scenario"])

    def test_missing_unsealed_future_and_conflicting_seals_are_unavailable(self):
        for options, reason in [
            ({"seals": []}, "NO_SEALED"),
            ({"seals": [replace(self.sealed, raw_replayed=False)]}, "NOT_INTACT"),
            ({"as_of": self.sealed.sealed_at - timedelta(seconds=1)}, "NOT_KNOWN"),
            ({"seals": [self.sealed, self.sealed]}, "DUPLICATE"),
        ]:
            with self.subTest(reason=reason):
                resolution = self.accepted(**options)
                self.assertEqual(resolution.status, "UNAVAILABLE")
                self.assertIn(reason, resolution.reasons[0])
                self.assertIsNone(resolution.mark)

    def test_source_session_symbol_instrument_and_after_entry_are_bound(self):
        for options, reason in [
            ({"source_id": uuid4()}, "SOURCE_INSTRUMENT"),
            ({"session_id": uuid4()}, "SOURCE_SESSION"),
            ({"instrument": "wrong"}, "SOURCE_INSTRUMENT"),
            ({"symbol": "ETHUSDT"}, "SOURCE_INSTRUMENT"),
            ({"entry_bar_open_micros": _micros(self.instant)}, "NOT_AFTER"),
            ({"max_age_micros": 60_000_000}, "STALE"),
            ({"instant": _venue_minute(4)}, "NO_UNIQUE"),
        ]:
            with self.subTest(reason=reason):
                resolution = self.accepted(**options)
                self.assertEqual(resolution.status, "UNAVAILABLE")
                self.assertIn(reason, resolution.reasons[0])

    def test_capture_coverage_is_independently_required(self):
        result = self.resolve()
        self.assertEqual(result.status, "UNAVAILABLE")
        self.assertIn("hour_not_judged", result.reasons[0])

    def test_acceptance_of_another_session_and_ambiguous_open_are_refused(self):
        with patch("trade_platform.paper_proven_prices_v1.derive_hourly_acceptance_v1",
                   return_value=_acceptance(uuid4())):
            result = self.resolve()
        self.assertIn("another_capture_session", result.reasons[0])
        from trade_platform.first_party_t4_forward_bars_v1 import forward_bars_from_seal_v1
        dataset = forward_bars_from_seal_v1(self.sealed, store=self.store())
        ambiguous = replace(dataset, bars=tuple(replace(bar, open_is_sequence_ambiguous=True)
                                               for bar in dataset.bars))
        with patch("trade_platform.paper_proven_prices_v1.forward_bars_from_seal_v1", return_value=ambiguous):
            result = self.accepted()
        self.assertIn("sequence_ambiguous", result.reasons[0])

    def test_frame_tampering_is_unavailable(self):
        store = self.store()
        # Exercise the same byte-verifier used for real immutable frame artifacts.
        with patch.object(type(store), "verify", side_effect=ValueError("fixture_frame_bytes_changed")):
            result = self.accepted()
        self.assertEqual(result.reasons, ("fixture_frame_bytes_changed",))

    def test_manifest_proven_hour_resolves_without_mocking_acceptance(self):
        from tests.test_first_party_t4_v1 import BASE, SECOND, FixtureArchive, _standard_samples
        from trade_platform.first_party_capture_hourly_acceptance_v1 import HOUR_NANOS
        self.capture = self.temp / "hour-capture"
        self.archive = FixtureArchive(self.capture)
        end = (BASE // HOUR_NANOS + 1) * HOUR_NANOS + 10 * SECOND
        duration = (end - BASE) // SECOND
        self.sealed = self.seal(windows=[(BASE, end)], samples=_standard_samples(duration + 60))
        result = self.resolve(capture_max_unproven_nanos=601 * SECOND)
        self.assertEqual(result.status, "AVAILABLE", result.reasons)
        self.assertTrue(result.evidence["capture_coverage"])
        self.assertEqual(result.reference.session_id, self.session_id)
        self.assertGreaterEqual(result.mark.knowable_at_micros, _micros(self.sealed.sealed_at))

    def test_decimal_context_does_not_round_or_refuse_valid_frame_prices(self):
        with localcontext() as context:
            context.prec = 6
            result = self.accepted()
        self.assertEqual(result.status, "AVAILABLE", result.reasons)
        self.assertEqual(result.mark.price, format(result.reference.price, "f"))
