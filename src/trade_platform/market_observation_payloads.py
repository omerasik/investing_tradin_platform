"""Shared typed-payload machinery, and the one cross-asset open-interest authority.

Module 3I.2 (roadmap NEXT-03 phase 2) extracted this from
``futures_market_observations`` so that open interest has exactly **one**
semantic authority across asset classes rather than a futures pipeline and a
parallel crypto one. Nothing about the meaning of an open-interest observation
changed; only where the code lives, and the physical table it is stored in.

**The physical table was renamed; the canonical payload identity was not.**
3I.1 stored open interest in ``futures_open_interest_observations``, a name that
would have become a lie the moment a crypto perpetual's open interest was
written to it. 3I.2 renames the physical table to ``open_interest_observations``
(migration ``20260907_0044``) while freezing
:data:`OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY` at the old string.

That constant is a **serialization token, not a table name**. It is the first
element of :meth:`OpenInterestPayload.canonical_tuple` and the value of the
``canonical_payload_table`` marker stored in
``historical_normalized_observations.normalized_value``, both of which feed a
sealed dataset's SHA-256 content hash. Freezing it is what guarantees the
property that mattered most about this rename: **generalizing a physical
storage name must not change the identity of already-sealed historical
evidence.** A dataset sealed under 3I.1 reproduces bit-identically under 3I.2,
and a futures and a crypto open-interest record are serialized by one shared
rule rather than two divergent ones. The token must never be edited again --
doing so would silently invalidate every sealed dataset containing open
interest.

**Units are modelled, never converted.** An open-interest figure is meaningless
without saying what it counts. :class:`OpenInterestUnit` is carried on every
record and no code anywhere multiplies a contract count by a multiplier, or a
base-asset quantity by a mark or index price, to reach another unit. Such a
conversion needs a price this record does not carry, and inventing one would
manufacture a measurement no venue ever published. Cross-unit comparison is a
derived research feature, not an ingestion concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

#: Physical storage table. 3I.2 renamed it from ``futures_open_interest_observations``
#: because the semantics were always cross-asset and only the name was futures-specific.
OPEN_INTEREST_PAYLOAD_TABLE: Final = "open_interest_observations"

#: Frozen canonical serialization token for open-interest payload schema V1.
#: Deliberately NOT equal to :data:`OPEN_INTEREST_PAYLOAD_TABLE` -- see the module
#: docstring. Changing this value would change the content hash of every sealed
#: dataset that contains an open-interest observation, including datasets sealed
#: before this module existed.
OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY: Final = "futures_open_interest_observations"


class ObservationPayloadError(ValueError):
    """Raised only for programmer error; provider data problems become issues."""


class OpenInterestUnit(StrEnum):
    #: A count of exchange contracts -- the canonical futures representation, and
    #: valid on crypto venues that publish a contract count.
    CONTRACTS = "CONTRACTS"
    #: A quantity of the base asset (e.g. BTC on a BTC-margined venue).
    BASE_ASSET = "BASE_ASSET"
    #: A notional quantity in the quote asset (e.g. USDT notional).
    QUOTE_NOTIONAL = "QUOTE_NOTIONAL"


#: Units whose meaning depends on naming the asset they are counted in.
UNITS_REQUIRING_ASSET: Final = frozenset(
    {OpenInterestUnit.BASE_ASSET, OpenInterestUnit.QUOTE_NOTIONAL}
)

#: A futures exchange publishes open interest as a contract count. The other
#: units are refused for a futures instrument rather than silently converted --
#: that conversion needs a contract multiplier and a price, neither of which an
#: open-interest record carries.
FUTURES_SUPPORTED_OPEN_INTEREST_UNITS: Final = frozenset({OpenInterestUnit.CONTRACTS})

#: Crypto venues genuinely publish all three: a contract count on contract-count
#: products, a base-asset quantity on coin-denominated products, and a quote
#: notional on notional-quoted products. Which one a given record may declare is
#: additionally bound to the instrument's own base/quote assets by
#: ``crypto_market_observations.validate_crypto_open_interest``.
CRYPTO_SUPPORTED_OPEN_INTEREST_UNITS: Final = frozenset(OpenInterestUnit)


def canonical_payload_marker(identity: str) -> dict[str, object]:
    """The non-financial marker stored in ``normalized_value`` for a typed kind.

    ``identity`` is a canonical payload identity token. For every kind except
    open interest it equals the physical table name; for open interest it is the
    frozen V1 token above, so that sealed datasets keep their identity across the
    3I.2 table rename.
    """
    return {"canonical_payload_table": identity}


def decimal_field(payload: dict[str, object], key: str, issues: list[str]) -> Decimal | None:
    raw = payload.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        issues.append(f"missing_{key}")
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        issues.append(f"invalid_{key}")
        return None
    if not value.is_finite():
        issues.append(f"invalid_{key}")
        return None
    return value


def text_field(payload: dict[str, object], key: str, issues: list[str]) -> str:
    value = str(payload.get(key, "")).strip()
    if not value:
        issues.append(f"missing_{key}")
    return value


def date_field(payload: dict[str, object], key: str, issues: list[str]) -> date | None:
    raw = payload.get(key)
    if raw is None:
        issues.append(f"missing_{key}")
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        issues.append(f"invalid_{key}")
        return None


def instant_field(payload: dict[str, object], key: str, issues: list[str]) -> datetime | None:
    raw = payload.get(key)
    if raw is None:
        issues.append(f"missing_{key}")
        return None
    if isinstance(raw, datetime):
        value = raw
    else:
        try:
            value = datetime.fromisoformat(str(raw))
        except ValueError:
            issues.append(f"invalid_{key}")
            return None
    if value.tzinfo is None or value.utcoffset() is None:
        issues.append(f"naive_{key}")
        return None
    return value


@dataclass(frozen=True, slots=True)
class OpenInterestPayload:
    """One open-interest observation, always carrying its explicit unit.

    Asset-class neutral: the same dataclass, the same parser and the same
    canonical serialization describe a COMEX gold contract count and a crypto
    perpetual's base-asset open interest. What differs is which units the
    instrument's own semantics permit, which the caller supplies.
    """

    open_interest: Decimal
    unit: OpenInterestUnit
    observed_at: datetime
    unit_asset: str | None = None

    def canonical_tuple(self) -> tuple[str, ...]:
        """Stable serialization contributed to a sealed dataset's content hash.

        The leading element is the frozen V1 identity token, never the physical
        table name, so the 3I.2 rename left every 3I.1 dataset hash untouched.
        """
        return (
            OPEN_INTEREST_CANONICAL_PAYLOAD_IDENTITY,
            str(self.open_interest),
            self.unit.value,
            self.observed_at.isoformat(),
            self.unit_asset or "",
        )

    def as_normalized_projection(self) -> dict[str, object]:
        return {
            "open_interest": str(self.open_interest),
            "unit": self.unit.value,
            "observed_at": self.observed_at.isoformat(),
            "unit_asset": self.unit_asset,
        }


def parse_open_interest_payload(
    payload: dict[str, object], *, supported_units: frozenset[OpenInterestUnit]
) -> tuple[OpenInterestPayload | None, tuple[str, ...]]:
    """Parse without repairing, and without ever converting between units."""
    issues: list[str] = []
    value = decimal_field(payload, "open_interest", issues)
    observed_at = instant_field(payload, "observed_at", issues)
    unit_raw = str(payload.get("unit", "")).strip().upper()
    unit_asset = str(payload.get("unit_asset", "")).strip() or None

    if value is not None and value < 0:
        issues.append("negative_open_interest")
    if not unit_raw:
        issues.append("missing_open_interest_unit")
        unit = None
    else:
        try:
            unit = OpenInterestUnit(unit_raw)
        except ValueError:
            issues.append("unsupported_open_interest_unit")
            unit = None
    if unit is not None:
        if unit not in supported_units:
            issues.append("unsupported_open_interest_unit")
        if unit in UNITS_REQUIRING_ASSET and not unit_asset:
            issues.append("open_interest_unit_requires_asset")
        if unit is OpenInterestUnit.CONTRACTS and unit_asset:
            issues.append("contract_count_cannot_declare_unit_asset")
    # Explicit narrowing rather than `assert`, which is stripped under -O.
    if issues or value is None or observed_at is None or unit is None:
        return None, tuple(dict.fromkeys(issues)) or ("invalid_open_interest_payload",)
    return (
        OpenInterestPayload(
            open_interest=value, unit=unit, observed_at=observed_at, unit_asset=unit_asset
        ),
        (),
    )
