"""Module 3G.1e / 3G.1e.1: source-backed pilot instrument identity -- US equities/ETFs only.

This module is deliberately separate from ``professional_instruments.mvp_instrument_universe()``.
That function is an explicitly-labeled fixture ("these records grant no data or trading
authority") used by unrelated demo/integration tests; nothing here replaces, mutates, or
depends on it. This module is the other side of the same authority: real,
individually-researched ``ProfessionalInstrument``/``SymbolMapping``/``IdentifierMapping``
records for the 16-symbol pilot universe approved for the Databento historical pilot,
registered through the exact same ``PostgresProfessionalInstrumentMaster`` used by every
other instrument in this platform -- there is no second instrument master, no parallel
resolution path, and no schema change.

Every date is sourced from a specific, citable reference -- an SEC filing/press release,
an exchange notice, or a fund issuer's own published inception date. Tiingo is NOT used
as a source anywhere in this module (Module 3G.1e.1 correction): Tiingo Starter data may
not feed durable storage per its Terms of Use Section 1.6(a), and this instrument master
is durable storage. Where a fact could not be established to primary-source standard, it
is recorded with an explicit, honest confidence caveat rather than silently treated as
equivalent to an SEC-filing-backed date. No date here reuses the fixture's placeholder
``date(2000, 1, 1)`` listing date.

Module 3G.1e.1 correction (real vs. system-knowledge time): ``ProfessionalInstrument
.registered_at`` and every ``SymbolMapping``/``IdentifierMapping.ingested_at`` in this
module are set to ``onboarded_at`` -- the real wall-clock moment this source-backed
record actually entered the platform -- never to the fact's own historical
``listing_date``/``valid_from``/``effective_at``. Backdating ingestion time would
fabricate system knowledge history (claiming this platform "knew" something in 1980,
2012, or 2022) and break auditability. Historical point-in-time queries against this
backfilled data must go through a resolver that takes ``effective_at`` and ``known_at``
as two separate parameters -- ``resolve_identifier_point_in_time()`` (pre-existing) and
``resolve_symbol_point_in_time()`` (added by this correction, mirroring it exactly for
symbol mappings) -- never through ``resolve_symbol()``/``get_as_of()`` with a historical
``as_of``, which conflates the two dimensions into one timestamp and therefore can only
ever answer "what is the current (as of ``onboarded_at`` or later) state," not "what was
true in the world on some past date."

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
    #: venue. Single-entry for instruments whose pilot-relevant ticker never changed.
    symbol_history: tuple[tuple[str, date, date | None, str], ...]
    #: None if the instrument is still active; otherwise (effective_date, source_reference, reason).
    delisting: tuple[date, str, str] | None
    listing_source_reference: str


def _instrument_id(symbol: str) -> str:
    return f"PILOT:{symbol}"


PILOT_INSTRUMENTS: tuple[PilotInstrumentSpec, ...] = (
    PilotInstrumentSpec(
        _instrument_id("AAPL"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "AAPL", date(1980, 12, 12), RepresentationKind.DIRECT, None,
        (("AAPL", date(1980, 12, 12), None, "well-established public record (Apple Computer, Inc. IPO, Dec 12 1980); pre-dates SEC EDGAR full-text search (mandatory filing began 1996), so no primary EDGAR filing is cited"),),
        None, "well-established IPO date; lower source confidence than SEC-filing-backed entries below, flagged explicitly (pre-EDGAR era)",
    ),
    PilotInstrumentSpec(
        _instrument_id("MSFT"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "MSFT", date(1986, 3, 13), RepresentationKind.DIRECT, None,
        (("MSFT", date(1986, 3, 13), None, "well-established public record (Microsoft Corp. IPO, Mar 13 1986); pre-dates SEC EDGAR full-text search"),),
        None, "well-established IPO date; lower source confidence than SEC-filing-backed entries below, flagged explicitly (pre-EDGAR era)",
    ),
    PilotInstrumentSpec(
        _instrument_id("AMZN"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "AMZN", date(1997, 5, 15), RepresentationKind.DIRECT, None,
        (("AMZN", date(1997, 5, 15), None, "well-established public record (Amazon.com, Inc. IPO, May 15 1997); not independently re-verified via a primary SEC filing in this module"),),
        None, "well-established IPO date; not independently re-verified via a primary filing in this module",
    ),
    PilotInstrumentSpec(
        _instrument_id("GOOGL"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "GOOGL", date(2004, 8, 19), RepresentationKind.DIRECT, None,
        (
            ("GOOG", date(2004, 8, 19), date(2014, 4, 3), "Google, Inc. IPO priced 2004-08-18, began trading 2004-08-19 on Nasdaq under ticker GOOG (Class A common stock, the only publicly traded class at the time); corroborated by contemporaneous coverage (CNN Business, 2004-08-19) and Alphabet's own SEC Form 10-K filings referencing this history"),
            ("GOOGL", date(2014, 4, 3), None, "2014 Alphabet/Google Class C stock dividend and reclassification, effective 2014-04-03: the ORIGINAL Class A voting shares (continuously listed since 2004-08-19) received the new ticker GOOGL; a newly-created, separate, non-voting Class C security received the vacated ticker GOOG. GOOGL is the continuous Class A lineage; GOOG today refers to a different, newly-created class and is deliberately NOT modeled in this pilot universe"),
        ),
        None, "CORRECTED in Module 3G.1e.1: GOOGL is modeled as the continuous Class A security since its real 2004-08-19 IPO (not the 2014-04-03 reclassification date used in the original 3G.1e module, which incorrectly treated the ticker rename as the instrument's origin). Class A kept its voting rights and simply changed ticker from GOOG to GOOGL in 2014; the newly-created, economically distinct Class C security took over the vacated GOOG ticker and is not modeled here",
    ),
    PilotInstrumentSpec(
        _instrument_id("NVDA"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "NVDA", date(1999, 1, 22), RepresentationKind.DIRECT, None,
        (("NVDA", date(1999, 1, 22), None, "SEC EDGAR CIK 0001045810 (NVIDIA Corp/CA): Form S-1 filed 1998-03-06, Form 424B4 prospectus filed ahead of the IPO closing in January 1999; exact first-trading-day of 1999-01-22 is convergent well-established public record, not independently confirmed word-for-word against the prospectus's own stated pricing date in this pass"),),
        None, "SEC-filing-adjacent (CIK confirmed on EDGAR) but the exact day is sourced from convergent secondary record, not a verbatim primary-document date match",
    ),
    PilotInstrumentSpec(
        _instrument_id("META"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "META", date(2012, 5, 18), RepresentationKind.DIRECT, None,
        (
            ("FB", date(2012, 5, 18), date(2022, 6, 9), "SEC EDGAR CIK 1326801 (Facebook, Inc. / Meta Platforms, Inc.): Facebook, Inc. IPO, first trading day 2012-05-18 on Nasdaq under ticker FB"),
            ("META", date(2022, 6, 9), None, "SEC 8-K, Meta Platforms Inc., filed 2022-05-31: https://www.sec.gov/Archives/edgar/data/1326801/000132680122000070/may312022-exhibit991.htm -- ticker changed to META effective 2022-06-09"),
        ),
        None, "same legal entity/security throughout; only the ticker symbol changed on 2022-06-09 (Facebook, Inc. -> Meta Platforms, Inc. rebrand). Listing date is the real 2012-05-18 IPO date, CIK 1326801 confirmed on SEC EDGAR",
    ),
    PilotInstrumentSpec(
        _instrument_id("TSLA"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "NASDAQ", "XNAS",
        "TSLA", date(2010, 6, 29), RepresentationKind.DIRECT, None,
        (("TSLA", date(2010, 6, 29), None, "well-established public record (Tesla, Inc. IPO, Jun 29 2010); not independently re-verified via a primary SEC filing in this module"),),
        None, "well-established IPO date; not independently re-verified via a primary filing in this module",
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
        (("SPY", date(1993, 1, 29), None, "State Street Global Advisors / SPDR S&P 500 ETF Trust fund documents (issuer-published inception date, Jan 29 1993)"),),
        None, "well-established, issuer-published fund-inception date",
    ),
    PilotInstrumentSpec(
        _instrument_id("QQQ"), AssetClass.ETF, InstrumentType.ETF, "NASDAQ", "XNAS",
        "QQQ", date(1999, 3, 10), RepresentationKind.DIRECT, None,
        (("QQQ", date(2015, 1, 1), None, "DELIBERATELY SCOPED, not a real event date: QQQ launched 1999-03-10 on AMEX (real fund inception, recorded as this instrument's listing_date), moved its listing to Nasdaq in 2004, and traded as ticker 'QQQQ' from 2004 to 2011 before reverting to 'QQQ'. This module does not model that pre-2020 venue/ticker lineage -- 2015-01-01 is an artificial scope boundary chosen only to comfortably precede the approved 2020-2026 pilot window with margin, NOT a claimed real ticker-change date. The real 2011 QQQQ->QQQ revert date was not independently confirmed to the day in this module."),),
        None, "Fund inception (1999-03-10, AMEX) is the real listing_date; the CURRENT XNAS/QQQ symbol mapping is intentionally scoped to the pilot window per owner instruction (Module 3G.1e.1 correction) rather than fabricating a precise pre-2020 venue-history date this module did not verify",
    ),
    PilotInstrumentSpec(
        _instrument_id("GLD"), AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
        "GLD", date(2004, 11, 18), RepresentationKind.ETF_PROXY, "GOLD; GLD is not XAUUSD spot or GC futures",
        (("GLD", date(2004, 11, 18), None, "issuer-published fund-inception date (SPDR Gold Shares, Nov 18 2004)"),),
        None, "well-established, issuer-published fund-inception date",
    ),
    PilotInstrumentSpec(
        _instrument_id("VTI"), AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
        "VTI", date(2001, 5, 24), RepresentationKind.DIRECT, None,
        (("VTI", date(2001, 5, 24), None, "issuer-published fund-inception date (Vanguard Total Stock Market ETF, May 24 2001)"),),
        None, "well-established, issuer-published fund-inception date",
    ),
    PilotInstrumentSpec(
        _instrument_id("IVV"), AssetClass.ETF, InstrumentType.ETF, "NYSE Arca", "ARCX",
        "IVV", date(2000, 5, 15), RepresentationKind.DIRECT, None,
        (("IVV", date(2000, 5, 15), None, "issuer-published fund-inception date (iShares Core S&P 500 ETF, May 15 2000)"),),
        None, "well-established, issuer-published fund-inception date",
    ),
    PilotInstrumentSpec(
        _instrument_id("TWTR"), AssetClass.EQUITY, InstrumentType.COMMON_STOCK, "New York Stock Exchange", "XNYS",
        "TWTR", date(2013, 11, 7), RepresentationKind.DIRECT, None,
        (("TWTR", date(2013, 11, 7), date(2022, 10, 28), "SEC EDGAR CIK 1418091 (Twitter, Inc.): Form S-1 filed 2013-10-03, IPO first trading day 2013-11-07 on NYSE under ticker TWTR; SEC 8-K filed 2022 (https://www.sec.gov/Archives/edgar/data/1418091/000119312522272772/d411753d8k.htm) confirms Twitter notified NYSE of merger consummation on 2022-10-27 and requested delisting effective 2022-10-28, the last real trading day"),),
        (date(2022, 10, 28), "SEC 8-K, Twitter Inc./X Corp merger consummation notice to NYSE, filed 2022 (see symbol_history source above): merger closed and trading ceased effective 2022-10-28. Note: the FORMAL SEC Form 25 deregistration (a distinct administrative step, 10 days after filing per NYSE Rule 12d2-2) took effect 2022-11-08 -- that later date is not used here, since 2022-10-28 is the economically meaningful last-trading-day this platform's lifecycle model cares about", "acquired_and_taken_private"),
        "IPO and delisting dates both confirmed against SEC EDGAR primary filings (CIK 1418091) -- TWTR is registered ACTIVE at its real IPO and then delisted via a real lifecycle event at its real 2022-10-28 last-trading-day; it must never resolve as a currently-active instrument for any as_of after that",
    ),
)


def _as_datetime(value: date) -> datetime:
    return datetime.combine(value, time(0), tzinfo=UTC)


def pilot_instrument(spec: PilotInstrumentSpec, onboarded_at: datetime) -> ProfessionalInstrument:
    """Build the ``ProfessionalInstrument`` row. Registration itself always records
    ``LifecycleStatus.ACTIVE`` (required by ``PostgresProfessionalInstrumentMaster.register``);
    a real historical delisting, if any, is applied afterward via a separate ``.delist()``
    call using ``spec.delisting`` -- exactly mirroring how a real delisting is actually
    discovered after the fact, not asserted at registration time.

    ``registered_at`` is ``onboarded_at`` -- the real wall-clock time this record actually
    entered the platform -- NEVER ``spec.listing_date``. See the module docstring: backdating
    ``registered_at`` to a historical fact date would fabricate system knowledge history.
    Historical point-in-time queries against this record must use
    ``resolve_identifier_point_in_time()``/``resolve_symbol_point_in_time()``, which take
    ``effective_at`` and ``known_at`` as two separate parameters for exactly this reason.
    """
    return ProfessionalInstrument(
        instrument_id=spec.instrument_id, asset_class=spec.asset_class, instrument_type=spec.instrument_type,
        exchange_name=spec.exchange_name, venue=spec.venue, mic=spec.venue, canonical_symbol=spec.canonical_symbol,
        listing_date=spec.listing_date, base_currency="USD", quote_currency="USD", settlement_currency="USD",
        contract_multiplier=Decimal(1), contract_size=Decimal(1), tick_size=Decimal("0.01"), lot_size=Decimal(1),
        price_precision=2, quantity_precision=0, trading_timezone="America/New_York",
        market_session_type=SessionType.US_EQUITY, representation_kind=spec.representation_kind,
        registered_at=onboarded_at, lifecycle_status=LifecycleStatus.ACTIVE,
        underlying_reference=spec.underlying_reference,
    )


def pilot_symbol_mappings(onboarded_at: datetime) -> tuple[SymbolMapping, ...]:
    """One row per historically-distinct ticker each pilot instrument has traded under.

    ``ingested_at`` is ``onboarded_at`` for every row -- never ``valid_from``. This
    platform did not learn any of these mappings on the date they became historically
    true; it learned them all today, from public record, during this onboarding. A live
    discovery pipeline elsewhere in this codebase (one that learns about a rename as it
    happens) would correctly use its own real wall-clock time here too -- the point is
    that ``ingested_at`` always means "when did WE find this out," which for a backfill
    is uniformly the backfill's own run time, not the historical fact's own date.
    """
    return tuple(
        SymbolMapping(
            instrument_id=spec.instrument_id, venue=spec.venue, symbol=symbol,
            valid_from=_as_datetime(valid_from),
            valid_until=None if valid_until is None else _as_datetime(valid_until),
            ingested_at=onboarded_at, source_reference=source,
        )
        for spec in PILOT_INSTRUMENTS
        for symbol, valid_from, valid_until, source in spec.symbol_history
    )


def pilot_databento_identifier_mappings(onboarded_at: datetime) -> tuple[IdentifierMapping, ...]:
    """Proposed (not yet applied to any real Databento call) mapping from Databento's
    ``raw_symbol`` -- which for EQUS.SUMMARY/EQUS.MINI is the plain ticker -- to these
    canonical instruments, using the same time-bounded windows as the real ticker history
    above, and the same ``ingested_at = onboarded_at`` convention documented on
    ``pilot_symbol_mappings()``. This is pure data; nothing here contacts Databento.
    """
    return tuple(
        IdentifierMapping(
            instrument_id=spec.instrument_id, source_kind=IdentifierSourceKind.PROVIDER,
            namespace=DATABENTO_RAW_SYMBOL_NAMESPACE, value=symbol,
            valid_from=_as_datetime(valid_from),
            valid_until=None if valid_until is None else _as_datetime(valid_until),
            ingested_at=onboarded_at, source_reference=source,
        )
        for spec in PILOT_INSTRUMENTS
        for symbol, valid_from, valid_until, source in spec.symbol_history
    )


def pilot_delistings() -> tuple[tuple[str, date, str, str], ...]:
    """(instrument_id, effective_date, reason, source_reference) for every pilot
    instrument with a real, evidenced delisting. Applied via
    ``PostgresProfessionalInstrumentMaster.delist(instrument_id, effective_at, ingested_at,
    reason)`` where ``effective_at`` is derived from this tuple's real ``effective_date``
    and ``ingested_at`` must be the caller's own ``onboarded_at`` -- never
    ``effective_date`` itself.
    """
    return tuple(
        (spec.instrument_id, spec.delisting[0], spec.delisting[2], spec.delisting[1])
        for spec in PILOT_INSTRUMENTS if spec.delisting is not None
    )
