"""Phase R8.2 -- the owner's research watch list (OR-9): recorded, never chosen (offline)."""

from __future__ import annotations

import unittest
from uuid import uuid4

from trade_platform.research_watchlist_v1 import (
    MAX_WATCH_ENTRIES_V1,
    STATUS_ACTIVE,
    STATUS_DRAFT,
    UNRESOLVED_APPROVAL,
    UNRESOLVED_SELECTION,
    ResearchWatchlistError,
    ResearchWatchlistV1,
    WatchEntryV1,
)


def entry(symbol: str = "SOLUSDT") -> WatchEntryV1:
    return WatchEntryV1(uuid4(), "a" * 64, uuid4(), symbol)


class WatchlistTests(unittest.TestCase):
    def test_nothing_is_selected_or_approved_by_default(self) -> None:
        empty = ResearchWatchlistV1("owner-watch")
        self.assertEqual(STATUS_DRAFT, empty.status)
        self.assertEqual((UNRESOLVED_SELECTION, UNRESOLVED_APPROVAL), empty.unresolved)
        chosen = ResearchWatchlistV1("owner-watch", (entry(),))
        self.assertEqual((UNRESOLVED_APPROVAL,), chosen.unresolved)
        approved = ResearchWatchlistV1("owner-watch", (entry(),), "owner", "2026-10-09")
        self.assertEqual((STATUS_ACTIVE, ()), (approved.status, approved.unresolved))
        self.assertEqual("NOT_VALIDATED_RESEARCH_WATCH", approved.identity()["authority"])

    def test_identity_is_order_independent_and_reproduces(self) -> None:
        a, b = entry(), entry("BTCUSDT")
        first = ResearchWatchlistV1("owner-watch", (a, b), "owner", "2026-10-09")
        second = ResearchWatchlistV1("owner-watch", (b, a), "owner", "2026-10-09")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.identity(), ResearchWatchlistV1.from_identity(first.identity()).identity())
        tampered = {**first.identity(), "status": "DRAFT"}
        with self.assertRaisesRegex(ResearchWatchlistError, "does_not_reproduce"):
            ResearchWatchlistV1.from_identity(tampered)

    def test_malformed_repeated_or_oversized_lists_are_refused_not_truncated(self) -> None:
        one = entry()
        with self.assertRaisesRegex(ResearchWatchlistError, "repeats"):
            ResearchWatchlistV1("owner-watch", (one, WatchEntryV1(one.study_id, "b" * 64, one.trial_id, "SOLUSDT")))
        with self.assertRaisesRegex(ResearchWatchlistError, "too_large"):
            ResearchWatchlistV1("owner-watch", tuple(entry() for _ in range(MAX_WATCH_ENTRIES_V1 + 1)))
        with self.assertRaisesRegex(ResearchWatchlistError, "slug"):
            ResearchWatchlistV1("Owner Watch")
        with self.assertRaisesRegex(ResearchWatchlistError, "rerun_hash"):
            WatchEntryV1(uuid4(), "short", uuid4(), "SOLUSDT")
        with self.assertRaises(ValueError):
            ResearchWatchlistV1("owner-watch", (entry(),), "owner", "09/10/2026")


if __name__ == "__main__":
    unittest.main()
