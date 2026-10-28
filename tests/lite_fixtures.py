"""Offline synthetic minute-grid fixtures, never served as real Lite evidence."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tests.test_bybit_public_archive_v1 import DAY_START, FakeArchive, _csv, _gz
from trade_platform.public_archive_research_bars_v1 import acquire_and_derive_day_v1
from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
from trade_platform.terminal_lite_evidence_v1 import LITE_SYMBOLS


def seed_lite_days(
    root: Path, first: date, days: int, *, omitted_minute: int | None = None
) -> None:
    store = ResearchFrameStoreV1(root / "source")
    for offset in range(days):
        day = first + timedelta(days=offset)
        shift = int(datetime.combine(day, datetime.min.time(), tzinfo=UTC).timestamp()) - DAY_START
        text = _csv(
            [
                (shift + minute * 60 + 0.5, "100.0", "0.001")
                for minute in range(1440)
                if minute != omitted_minute
            ]
        )
        for symbol in LITE_SYMBOLS:
            fetch = FakeArchive(_gz(text.replace(",BTCUSDT,", f",{symbol},")))
            acquire_and_derive_day_v1(
                root / "archive", symbol, day, store=store, evict=True, fetch=fetch
            )
