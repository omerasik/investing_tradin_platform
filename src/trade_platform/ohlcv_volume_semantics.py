"""Module 3B.2 -- canonical typed authority for OHLCV volume/turnover semantics.

A bare canonical OHLCV bar exposes ``open/high/low/close/volume``, but its
``volume`` is a unitless :class:`~decimal.Decimal`: the number ``12.5`` says
nothing about whether it counts contracts, base coin or quote notional. This
module supplies the missing authority. It does **not** guess: it maps an
*authorized provider contract* to explicit, versioned volume/turnover semantics,
and a normalized OHLCV observation that carries those semantics snapshots the
resolved meaning so a later provider-contract change can never retroactively
reinterpret already-sealed historical evidence.

**Evidence versus authority.** The Bybit adapter already preserves the provider
figures -- ``volume`` and ``provider_turnover`` -- verbatim in the raw payload.
Those are *evidence*. They are not, by themselves, canonical semantics: a raw
number in a JSON blob does not establish what unit it is in. The semantic
*rule* is authorized here from the source/provider contract
(:data:`BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS`) and bound to an explicit
:data:`BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION`.

**Units are modelled, never converted.** :class:`BarVolumeUnit` is carried on
every semantics record, and nothing here multiplies ``volume`` by ``close`` to
synthesize a turnover, or divides a turnover by a price to reach a base-coin
volume. The provider publishes both figures independently and this module keeps
them independent -- a conversion would need a price this authority deliberately
does not consult and would manufacture a measurement no venue published.

**Legacy stays legacy.** A source with no authorized rule receives no semantics
at all; its OHLCV rows remain valid unitless evidence, exactly as before. This
authority is additive: it never backfills a guessed unit onto historical data.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from .crypto_instruments import CryptoInstrumentKind, SettlementStyle

#: Physical storage table for the volume-semantics sidecar. It is bound 1:1 to a
#: normalized OHLCV observation and, unlike the typed-payload kinds, is optional:
#: a normalized OHLCV row may exist without one (legacy/unitless evidence).
OHLCV_VOLUME_SEMANTICS_TABLE: Final = "historical_ohlcv_volume_semantics"

#: Frozen canonical serialization token for the volume-semantics payload schema
#: V1. It is the leading element of :meth:`OhlcvVolumeSemantics.canonical_tuple`
#: and, like every other canonical payload identity in this pipeline, feeds a
#: sealed dataset's SHA-256 content hash. It must never be edited: doing so would
#: change the content hash of every sealed dataset that carries typed volume
#: semantics.
OHLCV_VOLUME_SEMANTICS_CANONICAL_IDENTITY: Final = "historical_ohlcv_volume_semantics_v1"

#: Maximum length of an asset code, matching the ``VARCHAR(12)`` columns the
#: crypto authorities already use (e.g. ``crypto_funding_observations``).
_MAX_ASSET_CODE_LENGTH: Final = 12
_MIN_ASSET_CODE_LENGTH: Final = 2


class OhlcvVolumeSemanticsError(ValueError):
    """Raised only for a structurally invalid semantics object (programmer error).

    Provider-data problems -- a missing or negative turnover, an instrument that
    is not an eligible linear perpetual -- become fail-closed *issues* returned
    by :func:`resolve_ohlcv_volume_semantics`, never this exception, so they flow
    through the pipeline's normal quality-rejection path.
    """


class BarVolumeUnit(StrEnum):
    """What a bar's volume (or turnover) figure counts. A unit, never a value."""

    #: A count of exchange contracts.
    CONTRACTS = "CONTRACTS"
    #: A quantity of the instrument's base asset (e.g. BTC on BTCUSDT).
    BASE_ASSET = "BASE_ASSET"
    #: A notional quantity in the instrument's quote asset (e.g. USDT on BTCUSDT).
    QUOTE_ASSET = "QUOTE_ASSET"


#: Units whose meaning depends on naming the asset they are counted in. Every
#: unit this phase's authority actually emits is one of these -- a contract count
#: carries no asset -- so the sidecar always names both its assets.
UNITS_REQUIRING_ASSET: Final = frozenset({BarVolumeUnit.BASE_ASSET, BarVolumeUnit.QUOTE_ASSET})


def _valid_asset_code(value: str) -> bool:
    return (
        _MIN_ASSET_CODE_LENGTH <= len(value) <= _MAX_ASSET_CODE_LENGTH
        and value.isalnum()
        and value == value.upper()
    )


@dataclass(frozen=True, slots=True)
class OhlcvVolumeSemantics:
    """The resolved, immutable canonical meaning of one OHLCV bar's volume/turnover.

    Frozen and validated on construction, so a malformed authority object cannot
    exist: it fails closed rather than silently recording nonsense. The two
    figures keep independent units and assets -- nothing here relates ``volume``
    to ``turnover`` arithmetically.
    """

    volume_unit: BarVolumeUnit
    volume_asset: str
    turnover: Decimal
    turnover_unit: BarVolumeUnit
    turnover_asset: str
    semantic_version: str
    source_reference: str

    def __post_init__(self) -> None:
        if self.volume_unit in UNITS_REQUIRING_ASSET and not _valid_asset_code(self.volume_asset):
            raise OhlcvVolumeSemanticsError(f"invalid_volume_asset:{self.volume_asset}")
        if self.turnover_unit in UNITS_REQUIRING_ASSET and not _valid_asset_code(
            self.turnover_asset
        ):
            raise OhlcvVolumeSemanticsError(f"invalid_turnover_asset:{self.turnover_asset}")
        if self.volume_unit not in UNITS_REQUIRING_ASSET and self.volume_asset:
            raise OhlcvVolumeSemanticsError("contract_count_cannot_declare_volume_asset")
        if self.turnover_unit not in UNITS_REQUIRING_ASSET and self.turnover_asset:
            raise OhlcvVolumeSemanticsError("contract_count_cannot_declare_turnover_asset")
        if not self.turnover.is_finite():
            raise OhlcvVolumeSemanticsError("non_finite_turnover")
        if self.turnover < 0:
            raise OhlcvVolumeSemanticsError("negative_turnover")
        if not self.semantic_version.strip():
            raise OhlcvVolumeSemanticsError("invalid_semantic_version")
        if not self.source_reference.strip():
            raise OhlcvVolumeSemanticsError("invalid_source_reference")

    def canonical_tuple(self) -> tuple[str, ...]:
        """Stable serialization contributed to a sealed dataset's content hash.

        The leading element is the frozen V1 identity token. Changing any of the
        volume unit/asset, the turnover figure, the turnover unit/asset, the
        semantic version, or the semantic source identity changes this tuple and
        therefore the content hash of any *new* dataset carrying it -- while a
        legacy OHLCV row without a sidecar contributes this tuple not at all, so
        its historical hash is untouched.
        """
        return (
            OHLCV_VOLUME_SEMANTICS_CANONICAL_IDENTITY,
            self.volume_unit.value,
            self.volume_asset,
            str(self.turnover),
            self.turnover_unit.value,
            self.turnover_asset,
            self.semantic_version,
            self.source_reference,
        )

    def as_projection(self) -> dict[str, object]:
        """The typed semantics surfaced to research readers, none of it parsed
        from raw provider JSON."""
        return {
            "volume_unit": self.volume_unit.value,
            "volume_asset": self.volume_asset,
            "turnover": str(self.turnover),
            "turnover_unit": self.turnover_unit.value,
            "turnover_asset": self.turnover_asset,
            "semantic_version": self.semantic_version,
            "source_reference": self.source_reference,
        }


@dataclass(frozen=True, slots=True)
class OhlcvVolumeSemanticsRule:
    """An authorized mapping from a provider/dataset contract to volume semantics.

    The rule states *which* unit the provider's volume field and turnover field
    are in for its products; the *asset* each unit resolves to is taken from the
    resolved instrument, never from the provider payload. A rule exists only for
    a source whose contract this repository has actually authorized.
    """

    provider: str
    dataset_name: str
    semantic_version: str
    volume_unit: BarVolumeUnit
    turnover_unit: BarVolumeUnit
    source_reference: str


#: The explicit semantic version bound to the current Bybit V5 linear kline rule.
BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION: Final = (
    "bybit-v5-linear-kline-volume-semantics-v1"
)

#: The stable source identity that distinguishes this semantic authority, folded
#: into the canonical tuple so two rows resolved under different authorities can
#: never share evidence identity even with identical financial numbers.
BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_SOURCE_REFERENCE: Final = (
    "bybit:bybit_v5_public_market_linear:kline"
)

#: Bybit V5 Get Kline on USDT/USDC linear contracts publishes ``list[5]`` as the
#: volume in BASE coin and ``list[6]`` as the turnover in QUOTE coin, each
#: independently. For a linear perpetual the base asset is the traded coin and
#: the quote asset is the settlement/notional coin (BTC and USDT for BTCUSDT).
BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS: Final = OhlcvVolumeSemanticsRule(
    provider="bybit",
    dataset_name="bybit_v5_public_market_linear",
    semantic_version=BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION,
    volume_unit=BarVolumeUnit.BASE_ASSET,
    turnover_unit=BarVolumeUnit.QUOTE_ASSET,
    source_reference=BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_SOURCE_REFERENCE,
)

#: Every authorized rule, keyed by the source contract identity the pipeline
#: knows: the source's ``provider`` and ``dataset_name``. A source outside this
#: registry receives no semantics -- its OHLCV stays unitless legacy evidence.
_AUTHORIZED_RULES: Final[dict[tuple[str, str], OhlcvVolumeSemanticsRule]] = {
    (rule.provider, rule.dataset_name): rule
    for rule in (BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS,)
}


def authorized_volume_semantics_rule(
    provider: str, dataset_name: str
) -> OhlcvVolumeSemanticsRule | None:
    """The authorized volume-semantics rule for a source contract, or ``None``.

    ``None`` is the legacy answer: this source's OHLCV carries no typed unit
    authority and must never be assigned one.
    """
    return _AUTHORIZED_RULES.get((provider, dataset_name))


def _asset_for_unit(unit: BarVolumeUnit, *, base_asset: str, quote_asset: str) -> str | None:
    if unit is BarVolumeUnit.BASE_ASSET:
        return base_asset
    if unit is BarVolumeUnit.QUOTE_ASSET:
        return quote_asset
    return None


def volume_semantics_issues(
    semantics: OhlcvVolumeSemantics,
    *,
    rule: OhlcvVolumeSemanticsRule,
    base_asset: str,
    quote_asset: str,
    settlement_style: SettlementStyle | None,
    kind: CryptoInstrumentKind,
) -> tuple[str, ...]:
    """Judge a resolved semantics object against its rule and instrument, fail-closed.

    Returns the deduplicated issues, empty when the semantics are coherent. This
    is the single authority for what "coherent" means, so both the resolver and
    any caller wishing to re-check an authority object share one rule set.
    """
    issues: list[str] = []
    if kind is not CryptoInstrumentKind.PERPETUAL:
        issues.append(f"volume_semantics_requires_perpetual:{kind.value}")
    if settlement_style is not SettlementStyle.LINEAR:
        issues.append("volume_semantics_requires_linear_settlement")
    if semantics.volume_unit is not rule.volume_unit:
        issues.append(f"unsupported_volume_unit:{semantics.volume_unit.value}")
    if semantics.turnover_unit is not rule.turnover_unit:
        issues.append(f"unsupported_turnover_unit:{semantics.turnover_unit.value}")
    if semantics.volume_asset != base_asset:
        issues.append("volume_asset_mismatch")
    if semantics.turnover_asset != quote_asset:
        issues.append("turnover_asset_mismatch")
    if semantics.semantic_version != rule.semantic_version:
        issues.append("semantic_version_mismatch")
    if semantics.source_reference != rule.source_reference:
        issues.append("semantic_source_reference_mismatch")
    return tuple(dict.fromkeys(issues))


def _parse_turnover(raw: object, issues: list[str]) -> Decimal | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        issues.append("missing_provider_turnover")
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        issues.append("invalid_provider_turnover")
        return None
    if not value.is_finite():
        issues.append("invalid_provider_turnover")
        return None
    if value < 0:
        issues.append("negative_provider_turnover")
        return None
    return value


def resolve_ohlcv_volume_semantics(
    rule: OhlcvVolumeSemanticsRule,
    *,
    base_asset: str,
    quote_asset: str,
    settlement_asset: str | None,
    settlement_style: SettlementStyle | None,
    kind: CryptoInstrumentKind,
    provider_turnover: object,
) -> tuple[OhlcvVolumeSemantics | None, tuple[str, ...]]:
    """Resolve canonical volume semantics for an authorized OHLCV bar, fail-closed.

    The provider *evidence* consumed is exactly the turnover the adapter already
    preserved; the *authority* is the rule plus the resolved instrument. Nothing
    is converted, repaired or synthesized: a missing or invalid turnover, or an
    instrument that is not an eligible linear perpetual, returns ``None`` with
    the issues that explain why, and the pipeline rejects the observation.
    """
    issues: list[str] = []
    turnover = _parse_turnover(provider_turnover, issues)
    volume_asset = _asset_for_unit(rule.volume_unit, base_asset=base_asset, quote_asset=quote_asset)
    turnover_asset = _asset_for_unit(
        rule.turnover_unit, base_asset=base_asset, quote_asset=quote_asset
    )
    if volume_asset is None:
        issues.append(f"volume_unit_requires_asset:{rule.volume_unit.value}")
    if turnover_asset is None:
        issues.append(f"turnover_unit_requires_asset:{rule.turnover_unit.value}")
    if turnover is None or volume_asset is None or turnover_asset is None:
        return None, tuple(dict.fromkeys(issues)) or ("invalid_volume_semantics",)
    try:
        semantics = OhlcvVolumeSemantics(
            volume_unit=rule.volume_unit,
            volume_asset=volume_asset,
            turnover=turnover,
            turnover_unit=rule.turnover_unit,
            turnover_asset=turnover_asset,
            semantic_version=rule.semantic_version,
            source_reference=rule.source_reference,
        )
    except OhlcvVolumeSemanticsError as error:
        issues.append(str(error))
        return None, tuple(dict.fromkeys(issues))
    coherence = volume_semantics_issues(
        semantics,
        rule=rule,
        base_asset=base_asset,
        quote_asset=quote_asset,
        settlement_style=settlement_style,
        kind=kind,
    )
    if coherence:
        return None, coherence
    return semantics, ()
