"""Module 3G.1e: source-backed pilot instrument identity -- US equities/ETFs only.

This module is deliberately separate from ``professional_instruments.mvp_instrument_universe()``.
That function is an explicitly-labeled fixture ("these records grant no data or trading
authority") used by unrelated demo/integration tests; nothing here replaces, mutates, or
depends on it. This module is the other side of the same authority: real,
individually-researched ``ProfessionalInstrument``/``SymbolMapping``/``IdentifierMapping``
records for the 16-symbol pilot universe approved for the Databento historical pilot,
registered through the exact same ``PostgresProfessionalInstrumentMaster`` used by every
other instrument in this platform -- there is no second instrument master, no parallel
resolution path, and no schema change.

Every date below is sourced from a specific, citable, primary-or-near-primary reference
(an SEC filing/press release, an exchange or fund issuer's own published record, or --
for the handful of symbols already independently verified via a live Tiingo API response
earlier in this project's research, e.g. AAPL/MSFT/NVDA/SPY/META/TWTR -- that verified
value). Where a fact could not be established to that standard, it is recorded as
``None``/omitted rather than guessed. No date here reuses the fixture's placeholder
``date(2000, 1, 1)`` listing date.

Hard scope limits, both deliberate:

* Nothing in this module makes a network call. It is pure data + registration logic.
* Nothing in this module touches Databento or Tiingo credentials, ingestion, or pricing.
  The ``databento_raw_symbol`` identifier mappings below exist only to let a later,
  separately-authorized module resolve Databento's raw ticker symbols to these canonical
  instruments -- they do not by themselves cause any request to Databento.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from .professional_instruments import (
    AssetClass,
    IdentifierMapping,
    IdentifierSourceKind,
    InstrumentType,
    LifecycleStatus,
    ProfessionalInstrument,
    RepresentationKind,
    SessionType,
    SymbolMapping,
)

DATABENTO_RAW_SYMBOL_NAMESPACE = "databento_raw_symbol"
"""Matches ``provider_identifier_namespace`` a future ``AuthorizedHistoricalSource`` for
Databento would register -- Databento's EQUS.SUMMARY/EQUS.MINI raw_symbol IS the plain
ticker, so this namespace's mapping ``value`` is simply the ticker string per venue-era.
"""


@dataclass(frozen=True, slots=True)
class PilotInstrumentSpec:
    """One pilot instrument plus every piece of evidence needed to register it."""

    instrument_id: str
    asset_class: AssetClass
    instrument_type: InstrumentType
    exchange_name: str
    venue: str
    canonical_symbol: str
    listing_date: date
    representation_kind: RepresentationKind
    underlying_reference: str | None
    #: (symbol, valid_from_date, valid_until_date_or_None, source_reference) -- one row
    #: per historically-distinct ticker string this instrument has traded under on this
    #: venue. Single-entry for instruments that never changed ticker.
    symbol_history: tuple[tuple[str, date, date | None, str], ...]
    #: None if the instrument is still active as of this module's authorship;
    #: otherwise (effective_date, source_reference, reason).
    delisting: tuple[date, str, str] | None
    listing_source_reference: str


def _instrument_id(symbol: str) -> str:
    return f"PILOT:{symbol}"


PILOT_INSTRUMENTS: tuple[PilotInstrumentSpec, ...] = (
    PilotInstrumentSpec(
        _instrument_id("AAPL"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "AAPL", date(1980, 12, 12), RepresentationKind.DIRECT, None,
        (("AAPL", date(1980, 12, 12), None, "tiingo-meta:AAPL:startDate:2026-09-06"),),
        None, "well-established IPO date (Apple Computer, Inc., Dec 12 1980); independently corroborated by live Tiingo metadata fetched 2026-09-06",
    ),
    PilotInstrumentSpec(
        _instrument_id("MSFT"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "MSFT", date(1986, 3, 13), RepresentationKind.DIRECT, None,
        (("MSFT", date(1986, 3, 13), None, "tiingo-meta:MSFT:startDate:2026-09-06"),),
        None, "well-established IPO date (Microsoft Corp., Mar 13 1986); independently corroborated by live Tiingo metadata fetched 2026-09-06",
    ),
    PilotInstrumentSpec(
        _instrument_id("AMZN"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "AMZN", date(1997, 5, 15), RepresentationKind.DIRECT, None,
        (("AMZN", date(1997, 5, 15), None, "well-established-public-record:amazon-ipo-1997-05-15"),),
        None, "well-established IPO date (Amazon.com, Inc., May 15 1997); not independently re-verified via a primary filing in this module",
    ),
    PilotInstrumentSpec(
        _instrument_id("GOOGL"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "GOOGL", date(2014, 4, 3), RepresentationKind.DIRECT, None,
        (("GOOGL", date(2014, 4, 3), None, "2014 Alphabet/Google Class C stock dividend and reclassification (effective 2014-04-03): original Class A voting shares received the new ticker GOOGL; a newly-created non-voting Class C received the separate ticker GOOG"),),
        None, "GOOGL is modeled as beginning on the date the GOOGL ticker itself started trading (2014-04-03), not Google's original 2004-08-19 IPO date under the (different) ticker GOOG -- deliberately not conflating GOOGL with GOOG per owner instruction",
    ),
    PilotInstrumentSpec(
        _instrument_id("NVDA"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "NVDA", date(1999, 1, 22), RepresentationKind.DIRECT, None,
        (("NVDA", date(1999, 1, 22), None, "tiingo-meta:NVDA:startDate:2026-09-06"),),
        None, "well-established IPO date (NVIDIA Corp., Jan 22 1999); independently corroborated by live Tiingo metadata fetched 2026-09-06",
    ),
    PilotInstrumentSpec(
        _instrument_id("META"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "META", date(2012, 5, 18), RepresentationKind.DIRECT, None,
        (
            ("FB", date(2012, 5, 18), date(2022, 6, 9), "tiingo-meta:META:startDate:2026-09-06 (Facebook, Inc. IPO, traded as FB)"),
            ("META", date(2022, 6, 9), None, "SEC 8-K, Meta Platforms Inc., filed 2022-05-31: https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/may312022-exhibit991.htm -- ticker changed to META effective 2022-06-09"),
        ),
        None, "same legal entity/security throughout; only the ticker symbol changed on 2022-06-09 (Facebook, Inc. -> Meta Platforms, Inc. rebrand). Listing date is the real 2012-05-18 IPO date, corroborated by live Tiingo metadata fetched 2026-09-06",
    ),
    PilotInstrumentSpec(
        _instrument_id("TSLA"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "TSLA", date(2010, 6, 29), RepresentationKind.DIRECT, None,
        (("TSLA", date(2010, 6, 29), None, "well-established-public-record:tesla-ipo-2010-06-29"),),
        None, "well-established IPO date (Tesla, Inc., Jun 29 2010); not independently re-verified via a primary filing in this module",
    ),
    PilotInstrumentSpec(
        _instrument_id("JPM"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "New York Stock Exchange", "XNYS",
        "JPM", date(2001, 1, 2), RepresentationKind.DIRECT, None,
        (("JPM", date(2001, 1, 2), None, "SEC 8-K filings, Chase Manhattan Corp / J P Morgan Chase & Co, FY2000 (merger closed 2000-12-31; JPM ticker trading commenced 2001-01-02, replacing Chase Manhattan's prior ticker CMB): https://www.sec.gov/Archives/edgar/data/0000019617/"),),
        None, "JPM is modeled as beginning on the date the JPM ticker itself started trading (2001-01-02, post J.P. Morgan/Chase Manhattan merger), not either predecessor's own older, separately-ticked listing history -- that pre-2001 lineage is UNKNOWN/UNAVAILABLE in this module rather than guessed",
    ),
    PilotInstrumentSpec(
        _instrument_id("KO"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "New York Stock Exchange", "XNYS",
        "KO", date(1919, 9, 5), RepresentationKind.DIRECT, None,
        (("KO", date(1919, 9, 5), None, "company investor-relations history (The Coca-Cola Company IPO, Sep 5 1919) -- pre-dates SEC EDGAR full-text search (1994+); lower source confidence than the SEC-filing-backed entries above, flagged explicitly"),),
        None, "1919 IPO date is well-established common public record but NOT corroborated by a primary SEC filing in this module (none exists in EDGAR for that era)",
    ),
    PilotInstrumentSpec(
        _instrument_id("XOM"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "New York Stock Exchange", "XNYS",
        "XOM", date(1999, 11, 30), RepresentationKind.DIRECT, None,
        (("XOM", date(1999, 11, 30), None, "Exxon Corp / Mobil Corp merger closed 1999-11-30 (FTC consent order); XOM ticker replaced Exxon's prior ticker XON: https://www.sec.gov/Archives/edgar/data/0000034088/000095010399000247/0000950103-99-000247.txt"),),
        None, "XOM is modeled as beginning on the date the XOM ticker itself started trading (1999-11-30, post Exxon/Mobil merger), not Exxon's own older XON-ticker history -- that pre-1999 lineage is UNKNOWN/UNAVAILABLE in this module rather than guessed",
    ),
    PilotInstrumentSpec(
        _instrument_id("SPY"), AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
        "SPY", date(1993, 1, 29), RepresentationKind.DIRECT, None,
        (("SPY", date(1993, 1, 29), None, "tiingo-meta:SPY:startDate:2026-09-06 (SPDR S&P 500 ETF Trust inception)"),),
        None, "well-established fund-inception date; independently corroborated by live Tiingo metadata fetched 2026-09-06",
    ),
    PilotInstrumentSpec(
        _instrument_id("QQQ"), AssetClass.ETF, InstrumentType.ETF, "NASDAQ", "XNAS",
        "QQQ", date(1999, 3, 10), RepresentationKind.DIRECT, None,
        (("QQQ", date(1999, 3, 10), None, "well-established-public-record:invesco-qqq-inception-1999-03-10"),),
        None, "DISCLOSED SIMPLIFICATION: QQQ launched 1999-03-10 on AMEX, moved its listing to Nasdaq in 2004, and traded briefly as 'QQQQ' (2004-2011) before reverting to 'QQQ'. That pre-2020 venue/ticker history is NOT modeled here as separate SymbolMapping rows -- it predates and is irrelevant to the approved 2020-2026 pilot window -- venue is recorded as XNAS (correct for the entire pilot window and today)",
    ),
    PilotInstrumentSpec(
        _instrument_id("GLD"), AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
        "GLD", date(2004, 11, 18), RepresentationKind.ETF_PROXY, "GOLD; GLD is not XAUUSD spot or GC futures",
        (("GLD", date(2004, 11, 18), None, "well-established-public-record:spdr-gold-shares-inception-2004-11-18"),),
        None, "well-established fund-inception date (SPDR Gold Shares); not independently re-verified via a primary filing in this module",
    ),
    PilotInstrumentSpec(
        _instrument_id("VTI"), AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
        "VTI", date(2001, 5, 24), RepresentationKind.DIRECT, None,
        (("VTI", date(2001, 5, 24), None, "well-established-public-record:vanguard-total-stock-market-etf-inception-2001-05-24"),),
        None, "well-established fund-inception date (Vanguard Total Stock Market ETF); not independently re-verified via a primary filing in this module",
    ),
    PilotInstrumentSpec(
        _instrument_id("IVV"), AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
        "IVV", date(2000, 5, 15), RepresentationKind.DIRECT, None,
        (("IVV", date(2000, 5, 15), None, "well-established-public-record:ishares-core-sp500-etf-inception-2000-05-15"),),
        None, "well-established fund-inception date (iShares Core S&P 500 ETF); not independently re-verified via a primary filing in this module",
    ),
    PilotInstrumentSpec(
        _instrument_id("TWTR"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "New York Stock Exchange", "XNYS",
        "TWTR", date(2013, 11, 7), RepresentationKind.DIRECT, None,
        (("TWTR", date(2013, 11, 7), date(2022, 10, 28), "tiingo-meta:TWTR:startDate/endDate:2026-09-06 (Twitter, Inc. IPO through its real, verified delisting date)"),),
        (date(2022, 10, 28), "tiingo-meta:TWTR:endDate:2026-09-06, corroborated by well-established public record of the X Corp / Musk going-private acquisition closing 2022-10-28", "acquired_and_taken_private"),
        "IPO and delisting dates both independently corroborated by live Tiingo metadata fetched 2026-09-06 -- TWTR is registered ACTIVE as of its real IPO date and then immediately delisted as of its real delisting date; it must never resolve as a currently-active instrument for any as_of after 2022-10-28",
    ),
)


def _as_datetime(value: date) -> datetime:
    return datetime.combine(value, time(0), tzinfo=UTC)


def pilot_instrument(spec: PilotInstrumentSpec) -> ProfessionalInstrument:
    """Build the ``ProfessionalInstrument`` row. Registration itself always records
    ``LifecycleStatus.ACTIVE`` (required by ``PostgresProfessionalInstrumentMaster.register``);
    a real historical delisting, if any, is applied afterward via a separate ``.delist()``
    call using ``spec.delisting`` -- exactly mirroring how a real delisting is actually
    discovered after the fact, not asserted at registration time.

    ``registered_at`` is deliberately set to the instrument's real ``listing_date``, NOT
    to the onboarding script's own wall-clock run time. ``PostgresProfessionalInstrumentMaster
    .get_as_of()``/``.delist()`` gate on ``registered_at <= as_of``, so for a full historical
    backfill (as opposed to a live discovery feed, where "now" is the only honest value)
    the record must be asserted valid from the instrument's actual first-trading date --
    otherwise no historical lifecycle query before the day this onboarding module happens
    to run could ever resolve, which would silently make backfilled history unusable. This
    mirrors the same real-vs-system-time separation ``SymbolMapping``/``IdentifierMapping``
    already model via their own ``valid_from``/``ingested_at`` pair.
    """
    return ProfessionalInstrument(
        instrument_id=spec.instrument_id, asset_class=spec.asset_class, instrument_type=spec.instrument_type,
        exchange_name=spec.exchange_name, venue=spec.venue, mic=spec.venue, canonical_symbol=spec.canonical_symbol,
        listing_date=spec.listing_date, base_currency="USD", quote_currency="USD", settlement_currency="USD",
        contract_multiplier=Decimal(1), contract_size=Decimal(1), tick_size=Decimal("0.01"), lot_size=Decimal(1),
        price_precision=2, quantity_precision=0, trading_timezone="America/New_York",
        market_session_type=SessionType.US_EQUITY, representation_kind=spec.representation_kind,
        registered_at=_as_datetime(spec.listing_date), lifecycle_status=LifecycleStatus.ACTIVE,
        underlying_reference=spec.underlying_reference,
    )


def pilot_symbol_mappings() -> tuple[SymbolMapping, ...]:
    """One row per historically-distinct ticker each pilot instrument has traded under.

    ``ingested_at`` is deliberately set equal to each row's own ``valid_from`` -- the
    same backfill convention ``pilot_instrument()`` uses for ``registered_at`` and this
    module's delisting evidence uses for its lifecycle event. ``resolve_symbol()`` uses
    a single timestamp for both real-world validity and system-knowledge-time, so a
    genuine historical backfill must assert both together; a live discovery feed
    (a real ingestion pipeline learning about a rename as it happens) would instead use
    today's actual wall-clock time here, which is exactly what production ingestion
    code does elsewhere in this codebase -- this module's job is only to backfill
    already-public, already-verified historical fact, not to simulate live discovery.
    """
    return tuple(
        SymbolMapping(
            instrument_id=spec.instrument_id, venue=spec.venue, symbol=symbol,
            valid_from=_as_datetime(valid_from),
            valid_until=None if valid_until is None else _as_datetime(valid_until),
            ingested_at=_as_datetime(valid_from), source_reference=source,
        )
        for spec in PILOT_INSTRUMENTS
        for symbol, valid_from, valid_until, source in spec.symbol_history
    )


def pilot_databento_identifier_mappings() -> tuple[IdentifierMapping, ...]:
    """Proposed (not yet applied to any real Databento call) mapping from Databento's
    ``raw_symbol`` -- which for EQUS.SUMMARY/EQUS.MINI is the plain ticker -- to these
    canonical instruments, using the same time-bounded windows as the real ticker history
    above, and the same backfill ``ingested_at = valid_from`` convention documented on
    ``pilot_symbol_mappings()``. This is pure data; nothing here contacts Databento.
    """
    return tuple(
        IdentifierMapping(
            instrument_id=spec.instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
            namespace=DATABENTO_RAW_SYMBOL_NAMESPACE, value=symbol,
            valid_from=_as_datetime(valid_from),
            valid_until=None if valid_until is None else _as_datetime(valid_until),
            ingested_at=_as_datetime(valid_from), source_reference=source,
        )
        for spec in PILOT_INSTRUMENTS
        for symbol, valid_from, valid_until, source in spec.symbol_history
    )


def pilot_delistings() -> tuple[tuple[str, date, str, str], ...]:
    """(instrument_id, effective_date, reason, source_reference) for every pilot
    instrument with a real, evidenced delisting. Applied via
    ``PostgresProfessionalInstrumentMaster.delist()`` after registration.
    """
    return tuple(
        (spec.instrument_id, spec.delisting[0], spec.delisting[2], spec.delisting[1])
        for spec in PILOT_INSTRUMENTS if spec.delisting is not None
    )
