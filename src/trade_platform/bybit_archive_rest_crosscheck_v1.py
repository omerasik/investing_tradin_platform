"""Phase R3B -- value cross-check of archive-reconstructed 1m bars against Bybit's REST klines.

``RESEARCH_ONLY``. Public market data only: the one network call is an
unauthenticated GET of ``https://api.bybit.com/v5/market/kline`` (``category=linear``,
``interval=1``). No credential, no account, no order path.

Why
---
The roadmap's R3B acceptance asks that value differences between the bars the
platform reconstructs from the free trade archive and the venue's own REST
klines be *reported, never bridged*. This module measures them and nothing
else: it writes no frame, changes no bar, and never substitutes one source's
value for the other's.

Two independent publications of the same venue
----------------------------------------------
Both sides are provider-published and untrusted. The archive side is re-derived
here from the verified daily file (:func:`verify_archive_file_v1`, strict
parse, :func:`build_archive_bars_v1`), so the comparison binds to file content,
not to a catalog row. The REST side is parsed strictly (``retCode`` 0, the
requested category and symbol, seven string fields, minute-aligned starts inside
the requested window, no repeat, a finite and internally consistent OHLC); its
raw pages are kept byte for byte with their SHA-256 so the report can be
re-derived offline.

What the report says
--------------------
Per UTC day and in total, for two archive variants -- every published trade,
and every trade except the ones flagged ``RPI`` -- because whether the REST
kline includes RPI trades is undocumented and is exactly what a measurement
should show rather than assume:

* minutes present on both sides, only in the archive, only in REST with zero
  volume (a REST convention for a quiet minute, not a disagreement), and only in
  REST with volume;
* per-field disagreement counts for open, high, low, close and volume (exact
  ``Decimal`` equality; volume is quantized to the archive frame's scale first);
* turnover as exact, within the REST value's own printed resolution (one unit
  of its last printed digit, because its rounding direction is undocumented), or
  beyond it -- a resolution derived from the published text, not a tolerance
  chosen here;
* the first disagreeing minutes as timestamps only, and how many of the
  disagreeing minutes contain RPI trades;
* how many open/high/low disagreements equal a *prior-trade open* convention
  (the kline opens at the last trade price before its minute and its high and
  low stretch to that open). This is a diagnosis of the publisher's convention,
  never a reconciliation: the raw disagreement counts stay as measured. On the
  first real sample (BTCUSDT 2026-09-21..23) close, volume and turnover agreed
  in every minute and every open/high/low disagreement but the sample's first
  minute matched this convention -- REST opens are therefore not the first
  trade of the minute, and the archive bars keep their own first-trade open.

No price, return or P&L is reported. The identity excludes retrieval instants,
so the same files and pages always give the same ``content_hash``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from .bybit_public_archive_v1 import (
    VOLUME_QUANTUM_V1,
    ArchiveFileManifestV1,
    ArchiveTradeV1,
    BybitPublicArchiveError,
    HttpResponseV1,
    build_archive_bars_v1,
    bybit_public_trade_archive_contract_v1,
    iter_archive_trades_v1,
    verify_archive_file_v1,
)

REST_KLINE_URL_PREFIX_V1: Final = "https://api.bybit.com/v5/market/kline?category=linear&"
REST_KLINE_ENDPOINT_V1: Final = (
    "GET https://api.bybit.com/v5/market/kline?category=linear&symbol={SYMBOL}&interval=1"
    "&start={ms}&end={ms}&limit=1000"
)
CROSSCHECK_SEMANTIC_VERSION_V1: Final = "bybit-archive-vs-rest-kline-1m-crosscheck-1.0.0"
#: Two pages per UTC day, each well inside the endpoint's 1000-row limit.
_PAGE_MINUTES: Final = 720
_MS_PER_MINUTE: Final = 60_000
_SAMPLE_LIMIT: Final = 10
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
VARIANTS_V1: Final = ("all_trades", "excluding_rpi")

#: ``(url, headers) -> response``. Injected so every rule is testable offline.
RestFetchV1 = Callable[[str, Mapping[str, str]], HttpResponseV1]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode())


def _day_start_ms(day: date) -> int:
    return (datetime(day.year, day.month, day.day, tzinfo=UTC) - _EPOCH) // timedelta(milliseconds=1)


def _iso_minute(ms: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ms)).strftime("%Y-%m-%dT%H:%MZ")


def rest_kline_url_v1(symbol: str, start_ms: int, end_ms: int) -> str:
    if not symbol.isalnum() or not symbol.isupper():
        raise BybitPublicArchiveError("rest_kline_symbol_malformed")
    return f"{REST_KLINE_URL_PREFIX_V1}symbol={symbol}&interval=1&start={start_ms}&end={end_ms}&limit=1000"


def urllib_rest_kline_fetch_v1(url: str, headers: Mapping[str, str]) -> HttpResponseV1:
    """The one network call: an HTTPS GET of the public linear kline endpoint only."""
    import urllib.error
    import urllib.request

    if not url.startswith(REST_KLINE_URL_PREFIX_V1):
        raise BybitPublicArchiveError("fetch_outside_the_public_kline_endpoint")
    request = urllib.request.Request(url, headers=dict(headers))  # nosec B310 - fixed https host
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310 - https only
            return HttpResponseV1(response.status, {k.lower(): v for k, v in response.headers.items()},
                                  response.read())
    except urllib.error.HTTPError as error:
        return HttpResponseV1(error.code, {k.lower(): v for k, v in error.headers.items()}, b"")


# ---------------------------------------------------------------------------
# REST side: strict parse and checkpointed acquisition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestKlineV1:
    minute_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal


def _decimal(text: object, name: str, *, positive: bool) -> Decimal:
    if not isinstance(text, str):
        raise BybitPublicArchiveError(f"rest_kline_{name}_not_text")
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise BybitPublicArchiveError(f"rest_kline_{name}_malformed") from error
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise BybitPublicArchiveError(f"rest_kline_{name}_out_of_domain")
    return value


def parse_rest_kline_page_v1(body: bytes, *, symbol: str, start_ms: int, end_ms: int) -> list[RestKlineV1]:
    """Strict parse of one REST page. Raises on the first defect; returns ascending minutes."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BybitPublicArchiveError("rest_kline_body_not_json") from error
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        raise BybitPublicArchiveError("rest_kline_ret_code_not_zero")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("category") != "linear" or result.get("symbol") != symbol:
        raise BybitPublicArchiveError("rest_kline_result_is_not_the_requested_series")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise BybitPublicArchiveError("rest_kline_list_missing")
    klines: dict[int, RestKlineV1] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 7:
            raise BybitPublicArchiveError("rest_kline_row_width_mismatch")
        start_text = row[0]
        if not isinstance(start_text, str) or not start_text.isdigit():
            raise BybitPublicArchiveError("rest_kline_start_malformed")
        minute = int(start_text)
        if minute % _MS_PER_MINUTE or not start_ms <= minute <= end_ms:
            raise BybitPublicArchiveError("rest_kline_start_outside_the_requested_window")
        if minute in klines:
            raise BybitPublicArchiveError("rest_kline_minute_repeated")
        kline = RestKlineV1(
            minute_ms=minute,
            open=_decimal(row[1], "open", positive=True),
            high=_decimal(row[2], "high", positive=True),
            low=_decimal(row[3], "low", positive=True),
            close=_decimal(row[4], "close", positive=True),
            volume=_decimal(row[5], "volume", positive=False),
            turnover=_decimal(row[6], "turnover", positive=False),
        )
        if not kline.low <= min(kline.open, kline.close) <= max(kline.open, kline.close) <= kline.high:
            raise BybitPublicArchiveError("rest_kline_ohlc_inconsistent")
        klines[minute] = kline
    return [klines[minute] for minute in sorted(klines)]


@dataclass(frozen=True, slots=True)
class RestKlineDayV1:
    symbol: str
    utc_day: str
    pages: tuple[tuple[str, str], ...]  # (url, sha256 of the exact body)
    klines: tuple[RestKlineV1, ...]


def _page_windows(day: date) -> list[tuple[int, int]]:
    start = _day_start_ms(day)
    return [
        (start + offset * _MS_PER_MINUTE, start + (offset + _PAGE_MINUTES - 1) * _MS_PER_MINUTE)
        for offset in range(0, 1440, _PAGE_MINUTES)
    ]


def acquire_rest_klines_day_v1(
    root: Path,
    symbol: str,
    day: date,
    *,
    fetch: RestFetchV1 = urllib_rest_kline_fetch_v1,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RestKlineDayV1:
    """Fetch (or re-read) one closed UTC day of REST 1m klines, keeping every raw page.

    Checkpointed: a stored page is re-parsed, never refetched. A page is stored
    only after it parses strictly, so a failure leaves nothing behind. An open
    day is refused: its last klines are not final.
    """
    if datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1) > now():
        raise BybitPublicArchiveError("rest_kline_day_not_closed")
    directory = root / "rest-kline" / "v1" / f"symbol={symbol}"
    pages: list[tuple[str, str]] = []
    klines: list[RestKlineV1] = []
    for index, (start_ms, end_ms) in enumerate(_page_windows(day)):
        url = rest_kline_url_v1(symbol, start_ms, end_ms)
        path = directory / f"{symbol}{day.isoformat()}.page{index}.json"
        if path.exists():
            body = path.read_bytes()
            parsed = parse_rest_kline_page_v1(body, symbol=symbol, start_ms=start_ms, end_ms=end_ms)
        else:
            response = fetch(url, {"Accept": "application/json"})
            if response.status != 200:
                raise BybitPublicArchiveError(f"rest_kline_http_status:{response.status}")
            body = response.body
            parsed = parse_rest_kline_page_v1(body, symbol=symbol, start_ms=start_ms, end_ms=end_ms)
            directory.mkdir(parents=True, exist_ok=True)
            staged = path.with_suffix(".tmp")
            staged.write_bytes(body)
            os.replace(staged, path)
        pages.append((url, _sha256_bytes(body)))
        klines.extend(parsed)
    return RestKlineDayV1(symbol=symbol, utc_day=day.isoformat(), pages=tuple(pages), klines=tuple(klines))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _print_resolution(value: Decimal) -> Decimal:
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):  # pragma: no cover - finite values only reach here
        raise BybitPublicArchiveError("rest_kline_value_not_finite")
    return Decimal(1).scaleb(min(exponent, 0))


def _empty_counts() -> dict[str, Any]:
    return {
        "minutes_compared": 0,
        "minutes_consistent": 0,
        "archive_only_minutes": 0,
        "rest_only_zero_volume_minutes": 0,
        "rest_only_minutes_with_volume": 0,
        "field_mismatches": {"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},
        "turnover": {"exact": 0, "within_rest_print_resolution": 0, "beyond_rest_print_resolution": 0},
        "inconsistent_minutes_with_rpi_trades": 0,
        "prior_trade_open_convention": {
            "open_mismatches_matching_it": 0,
            "high_mismatches_matching_it": 0,
            "low_mismatches_matching_it": 0,
        },
    }


def compare_day_v1(
    trades: Sequence[ArchiveTradeV1],
    klines: Sequence[RestKlineV1],
    *,
    variant: str,
    prior_close: Decimal | None = None,
) -> dict[str, Any]:
    """Counts for one day and one archive variant. Reads values; changes nothing.

    ``prior_trade_open_convention`` is diagnosis, not reconciliation: it counts how many of
    the raw open/high/low disagreements equal what a kline would show if it
    opened at the last archive trade price before its minute (``prior_close``
    carries that price across a contiguous day boundary) and stretched its high
    and low to that open. The raw disagreement counts are never reduced by it.
    """
    if variant not in VARIANTS_V1:
        raise BybitPublicArchiveError("crosscheck_variant_unknown")
    selected = list(trades) if variant == "all_trades" else [t for t in trades if not t.rpi]
    rpi_minutes = {t.trade_ts_micros // 60_000_000 * _MS_PER_MINUTE for t in trades if t.rpi}
    bars: dict[int, tuple[object, ...]] = {}
    for built in build_archive_bars_v1(selected):
        start = built[0]
        if not isinstance(start, datetime):  # pragma: no cover - build_archive_bars_v1 writes datetimes
            raise BybitPublicArchiveError("archive_bar_start_malformed")
        bars[(start - _EPOCH) // timedelta(milliseconds=1)] = built
    rest = {kline.minute_ms: kline for kline in klines}
    counts = _empty_counts()
    convention = counts["prior_trade_open_convention"]
    inconsistent: list[int] = []
    last_price = prior_close
    for minute in sorted(bars.keys() | rest.keys()):
        bar, kline = bars.get(minute), rest.get(minute)
        previous_price = last_price
        if bar is not None:
            last_price = Decimal(str(bar[6]))
        if kline is None:
            counts["archive_only_minutes"] += 1
            inconsistent.append(minute)
            continue
        if bar is None:
            key = "rest_only_zero_volume_minutes" if kline.volume == 0 else "rest_only_minutes_with_volume"
            counts[key] += 1
            if kline.volume != 0:
                inconsistent.append(minute)
            continue
        counts["minutes_compared"] += 1
        archive_values = {"open": bar[3], "high": bar[4], "low": bar[5], "close": bar[6],
                          "volume": Decimal(str(bar[7])).quantize(VOLUME_QUANTUM_V1, ROUND_HALF_EVEN)}
        rest_values = {"open": kline.open, "high": kline.high, "low": kline.low, "close": kline.close,
                       "volume": kline.volume}
        conventional = (
            None if previous_price is None else {
                "open": previous_price,
                "high": max(Decimal(str(bar[4])), previous_price),
                "low": min(Decimal(str(bar[5])), previous_price),
            }
        )
        consistent = True
        for field_name, archive_value in archive_values.items():
            if archive_value != rest_values[field_name]:
                counts["field_mismatches"][field_name] += 1
                consistent = False
                if conventional is not None and conventional.get(field_name) == rest_values[field_name]:
                    convention[f"{field_name}_mismatches_matching_it"] += 1
        turnover_gap = abs(Decimal(str(bar[8])) - kline.turnover)
        if turnover_gap == 0:
            counts["turnover"]["exact"] += 1
        elif turnover_gap < _print_resolution(kline.turnover):
            counts["turnover"]["within_rest_print_resolution"] += 1
        else:
            counts["turnover"]["beyond_rest_print_resolution"] += 1
            consistent = False
        if consistent:
            counts["minutes_consistent"] += 1
        else:
            inconsistent.append(minute)
    counts["inconsistent_minutes_with_rpi_trades"] = sum(1 for minute in inconsistent if minute in rpi_minutes)
    counts["inconsistent_minutes_sample"] = [_iso_minute(minute) for minute in inconsistent[:_SAMPLE_LIMIT]]
    return counts


def _add(total: dict[str, Any], part: Mapping[str, Any]) -> None:
    for key, value in part.items():
        if isinstance(value, int):
            total[key] += value
        elif isinstance(value, dict):
            _add(total[key], value)


@dataclass(frozen=True, slots=True)
class CrosscheckReportV1:
    identity: Mapping[str, Any]
    content_hash: str


def build_crosscheck_report_v1(
    archive_root: Path,
    manifests: Sequence[ArchiveFileManifestV1],
    rest_days: Sequence[RestKlineDayV1],
) -> CrosscheckReportV1:
    """Re-verify each archive file, rebuild its bars and compare them with that day's REST klines."""
    if not manifests:
        raise BybitPublicArchiveError("crosscheck_requires_files")
    ordered = sorted(manifests, key=lambda item: item.utc_day)
    rest_by_day = {item.utc_day: item for item in rest_days}
    symbols = {item.symbol for item in ordered} | {item.symbol for item in rest_days}
    if len(symbols) != 1:
        raise BybitPublicArchiveError("crosscheck_is_one_symbol")
    if len(rest_by_day) != len(rest_days) or set(rest_by_day) != {item.utc_day for item in ordered} \
            or len(ordered) != len(rest_by_day):
        raise BybitPublicArchiveError("crosscheck_days_do_not_pair")
    symbol = symbols.pop()
    totals = {variant: _empty_counts() for variant in VARIANTS_V1}
    days = []
    carried: dict[str, Decimal | None] = dict.fromkeys(VARIANTS_V1)
    previous_day: date | None = None
    for manifest in ordered:
        day = date.fromisoformat(manifest.utc_day)
        if previous_day is None or (day - previous_day).days != 1:
            carried = dict.fromkeys(VARIANTS_V1)  # nothing is carried across a missing day
        previous_day = day
        path = verify_archive_file_v1(archive_root, manifest)
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            trades = list(iter_archive_trades_v1(handle, symbol=symbol, day=day))
        rest = rest_by_day[manifest.utc_day]
        variants = {
            variant: compare_day_v1(trades, rest.klines, variant=variant, prior_close=carried[variant])
            for variant in VARIANTS_V1
        }
        carried = {
            "all_trades": trades[-1].price if trades else carried["all_trades"],
            "excluding_rpi": next((t.price for t in reversed(trades) if not t.rpi), carried["excluding_rpi"]),
        }
        for variant, counts in variants.items():
            _add(totals[variant], counts)
        days.append({
            "utc_day": manifest.utc_day,
            "archive_file_sha256": manifest.sha256,
            "archive_trade_count": len(trades),
            "archive_rpi_trade_count": sum(1 for t in trades if t.rpi),
            "rest_pages": [{"url": url, "sha256": digest} for url, digest in rest.pages],
            "rest_kline_count": len(rest.klines),
            "variants": variants,
        })
    identity = {
        "schema_version": "archive-rest-kline-crosscheck-report-v1",
        "semantic_version": CROSSCHECK_SEMANTIC_VERSION_V1,
        "archive_source_contract_content_hash": bybit_public_trade_archive_contract_v1().content_hash(),
        "rest_endpoint": REST_KLINE_ENDPOINT_V1,
        "symbol": symbol,
        "bridged": False,
        "days": days,
        "totals": totals,
    }
    return CrosscheckReportV1(identity=identity, content_hash=_sha256_json(identity))


def write_crosscheck_report_v1(root: Path, report: CrosscheckReportV1) -> Path:
    days = report.identity["days"]
    directory = root / "crosscheck" / "v1" / f"symbol={report.identity['symbol']}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{days[0]['utc_day']}_{days[-1]['utc_day']}.{report.content_hash[:16]}.json"
    staged = path.with_suffix(".tmp")
    staged.write_text(
        json.dumps({"content_hash": report.content_hash, "identity": report.identity}, sort_keys=True, indent=1),
        encoding="utf-8",
    )
    os.replace(staged, path)
    return path


__all__ = [
    "CROSSCHECK_SEMANTIC_VERSION_V1",
    "REST_KLINE_URL_PREFIX_V1",
    "VARIANTS_V1",
    "CrosscheckReportV1",
    "RestKlineDayV1",
    "RestKlineV1",
    "acquire_rest_klines_day_v1",
    "build_crosscheck_report_v1",
    "compare_day_v1",
    "parse_rest_kline_page_v1",
    "rest_kline_url_v1",
    "urllib_rest_kline_fetch_v1",
    "write_crosscheck_report_v1",
]
