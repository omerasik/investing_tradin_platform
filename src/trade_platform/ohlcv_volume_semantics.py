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
*rule* is authorized here from the full source/instrument contract
(:data:`BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS`) and bound to an explicit
:data:`BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION`.

**The full contract is bound, not just provider/dataset_name.** A synthetic
source that merely copies ``provider="bybit"`` and
``dataset_name="bybit_v5_public_market_linear"`` while using a different
provider-identifier namespace or asset scope is not the authorized Bybit
source. :func:`authorized_volume_semantics_rule` therefore matches on the full
:class:`OhlcvVolumeSemanticsSourceContext`, and :func:`resolve_ohlcv_volume_semantics`
additionally proves the resolved *instrument* -- venue, kind, settlement style
and settlement asset -- against the rule's own requirements through the
canonical crypto-instrument authority, never merely because the source claims
``provider=bybit``.

**Units are modelled, never converted.** :class:`BarVolumeUnit` is carried on
every semantics record, and nothing here multiplies ``volume`` by ``close`` to
synthesize a turnover, or divides a turnover by a price to reach a base-coin
volume. The provider publishes both figures independently and this module keeps
them independent -- a conversion would need a price this authority deliberately
does not consult and would manufacture a measurement no venue published.

**Contracts carry no asset.** ``BarVolumeUnit.CONTRACTS`` is a bare count with
no asset attached; ``BASE_ASSET``/``QUOTE_ASSET`` always name one. The asset
fields are therefore nullable, not merely optional strings, and every
constructor here enforces that a contract count never invents an asset and a
base/quote unit never omits one.

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

    #: A count of exchange contracts. Carries no asset -- a contract count is a
    #: bare number, not a quantity of a named coin.
    CONTRACTS = "CONTRACTS"
    #: A quantity of the instrument's base asset (e.g. BTC on BTCUSDT).
    BASE_ASSET = "BASE_ASSET"
    #: A notional quantity in the instrument's quote asset (e.g. USDT on BTCUSDT).
    QUOTE_ASSET = "QUOTE_ASSET"


#: Units whose meaning depends on naming the asset they are counted in.
#: ``CONTRACTS`` is deliberately excluded: a contract count carries no asset,
#: and every constructor in this module enforces that split both ways --
#: BASE_ASSET/QUOTE_ASSET always name an asset, CONTRACTS never does.
UNITS_REQUIRING_ASSET: Final = frozenset({BarVolumeUnit.BASE_ASSET, BarVolumeUnit.QUOTE_ASSET})


def _valid_asset_code(value: str) -> bool:
    return (
        _MIN_ASSET_CODE_LENGTH <= len(value) <= _MAX_ASSET_CODE_LENGTH
        and value.isalnum()
        and value == value.upper()
    )


def _validate_unit_asset_pair(unit: BarVolumeUnit, asset: str | None, label: str) -> None:
    """Shared asset-presence rule: BASE_ASSET/QUOTE_ASSET require one, CONTRACTS forbids one."""
    if unit in UNITS_REQUIRING_ASSET:
        if asset is None or not _valid_asset_code(asset):
            raise OhlcvVolumeSemanticsError(f"invalid_{label}_asset:{asset}")
    elif asset is not None:
        raise OhlcvVolumeSemanticsError(f"contract_count_cannot_declare_{label}_asset")


@dataclass(frozen=True, slots=True)
class OhlcvVolumeSemantics:
    """The resolved, immutable canonical meaning of one OHLCV bar's volume/turnover.

    Frozen and validated on construction, so a malformed authority object cannot
    exist: it fails closed rather than silently recording nonsense. The two
    figures keep independent units and assets -- nothing here relates ``volume``
    to ``turnover`` arithmetically. ``volume_asset``/``turnover_asset`` are
    ``None`` exactly when their unit is ``CONTRACTS`` -- a bare count invents no
    asset -- and populated for ``BASE_ASSET``/``QUOTE_ASSET``.
    """

    volume_unit: BarVolumeUnit
    volume_asset: str | None
    turnover: Decimal
    turnover_unit: BarVolumeUnit
    turnover_asset: str | None
    semantic_version: str
    source_reference: str

    def __post_init__(self) -> None:
        _validate_unit_asset_pair(self.volume_unit, self.volume_asset, "volume")
        _validate_unit_asset_pair(self.turnover_unit, self.turnover_asset, "turnover")
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
        its historical hash is untouched. A ``None`` asset (the ``CONTRACTS``
        case) serializes deterministically as ``""``, never as Python's ``None``
        repr, so the tuple stays a plain ``tuple[str, ...]``.
        """
        return (
            OHLCV_VOLUME_SEMANTICS_CANONICAL_IDENTITY,
            self.volume_unit.value,
            self.volume_asset or "",
            str(self.turnover),
            self.turnover_unit.value,
            self.turnover_asset or "",
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
class OhlcvVolumeSemanticsSourceContext:
    """The full authorized source contract a volume-semantics rule binds against.

    Binding on ``provider``/``dataset_name`` alone would let a synthetic source
    that merely copies those two strings, while using a different
    provider-identifier namespace or asset scope, acquire semantics it was never
    authorized for. Every field here is compared against the rule's own
    expectation in :func:`authorized_volume_semantics_rule`.
    """

    provider: str
    dataset_name: str
    provider_identifier_namespace: str
    asset_scope: str


@dataclass(frozen=True, slots=True)
class OhlcvVolumeSemanticsRule:
    """An authorized mapping from a full source/instrument contract to volume semantics.

    The rule states *which* unit the provider's volume field and turnover field
    are in for its products; the *asset* each unit resolves to is taken from the
    resolved instrument, never from the provider payload. A rule exists only for
    a source whose contract this repository has actually authorized, and that
    authorization is checked at two levels: the *source* contract (provider,
    dataset, identifier namespace, asset scope -- see
    :class:`OhlcvVolumeSemanticsSourceContext`) and the *instrument* contract
    (venue, kind, settlement style -- proven in :func:`resolve_ohlcv_volume_semantics`
    against the canonical crypto-instrument authority, never assumed from the
    source alone).
    """

    provider: str
    dataset_name: str
    provider_identifier_namespace: str
    asset_scope: str
    semantic_version: str
    volume_unit: BarVolumeUnit
    turnover_unit: BarVolumeUnit
    source_reference: str
    #: The instrument-level contract this rule requires. Proven against the
    #: canonical crypto-instrument authority in :func:`resolve_ohlcv_volume_semantics`
    #: -- never assumed merely because a source declares ``provider=bybit``.
    required_venue: str
    required_kind: CryptoInstrumentKind
    required_settlement_style: SettlementStyle

    def source_context(self) -> OhlcvVolumeSemanticsSourceContext:
        return OhlcvVolumeSemanticsSourceContext(
            provider=self.provider,
            dataset_name=self.dataset_name,
            provider_identifier_namespace=self.provider_identifier_namespace,
            asset_scope=self.asset_scope,
        )


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

#: This rule's required source-contract fields, duplicated here as independent
#: frozen literals rather than imported from ``bybit_crypto_provider``/
#: ``bybit_instrument_onboarding``/``historical_market_data`` -- importing any of
#: those would create a circular import, since each of them (transitively)
#: imports this module via ``historical_market_data``. Each value must stay
#: equal to its counterpart:
#:   BYBIT_LINEAR_KLINE_REQUIRED_VENUE       == bybit_crypto_provider.BYBIT_EXCHANGE
#:   BYBIT_LINEAR_KLINE_REQUIRED_NAMESPACE   == bybit_crypto_provider.BYBIT_V5_SYMBOL_NAMESPACE
#:   BYBIT_LINEAR_KLINE_REQUIRED_ASSET_SCOPE == historical_market_data.AssetScope.CRYPTO.value
BYBIT_LINEAR_KLINE_REQUIRED_VENUE: Final = "BYBIT"
BYBIT_LINEAR_KLINE_REQUIRED_NAMESPACE: Final = "bybit_v5_symbol"
BYBIT_LINEAR_KLINE_REQUIRED_ASSET_SCOPE: Final = "CRYPTO"

#: Bybit V5 Get Kline on USDT/USDC linear contracts publishes ``list[5]`` as the
#: volume in BASE coin and ``list[6]`` as the turnover in QUOTE coin, each
#: independently. For a linear perpetual the base asset is the traded coin and
#: the quote asset is the settlement/notional coin (BTC and USDT for BTCUSDT).
#: This rule intentionally binds the full source AND instrument contract, not
#: just ``provider``/``dataset_name``: see the module and class docstrings.
BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS: Final = OhlcvVolumeSemanticsRule(
    provider="bybit",
    dataset_name="bybit_v5_public_market_linear",
    provider_identifier_namespace=BYBIT_LINEAR_KLINE_REQUIRED_NAMESPACE,
    asset_scope=BYBIT_LINEAR_KLINE_REQUIRED_ASSET_SCOPE,
    semantic_version=BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_VERSION,
    volume_unit=BarVolumeUnit.BASE_ASSET,
    turnover_unit=BarVolumeUnit.QUOTE_ASSET,
    source_reference=BYBIT_LINEAR_KLINE_VOLUME_SEMANTIC_SOURCE_REFERENCE,
    required_venue=BYBIT_LINEAR_KLINE_REQUIRED_VENUE,
    required_kind=CryptoInstrumentKind.PERPETUAL,
    required_settlement_style=SettlementStyle.LINEAR,
)

#: Every authorized rule, keyed by the source contract identity the pipeline
#: knows: the source's ``provider`` and ``dataset_name``. A source outside this
#: registry receives no semantics -- its OHLCV stays unitless legacy evidence.
#: This is only the first-pass index; :func:`authorized_volume_semantics_rule`
#: still checks every remaining context field before returning a match.
_AUTHORIZED_RULES: Final[dict[tuple[str, str], OhlcvVolumeSemanticsRule]] = {
    (rule.provider, rule.dataset_name): rule
    for rule in (BYBIT_V5_LINEAR_KLINE_VOLUME_SEMANTICS,)
}


def authorized_volume_semantics_rule(
    context: OhlcvVolumeSemanticsSourceContext,
) -> OhlcvVolumeSemanticsRule | None:
    """The authorized volume-semantics rule for a full source contract, or ``None``.

    ``None`` is the legacy answer: this source's OHLCV carries no typed unit
    authority and must never be assigned one. Matching requires every field of
    ``context`` to agree with the rule -- provider, dataset_name,
    provider_identifier_namespace AND asset_scope -- not merely the first two.
    A synthetic source that copies ``provider``/``dataset_name`` while using a
    different namespace or scope is exactly as unauthorized as a source with a
    different provider outright.
    """
    rule = _AUTHORIZED_RULES.get((context.provider, context.dataset_name))
    if rule is None:
        return None
    if context.provider_identifier_namespace != rule.provider_identifier_namespace:
        return None
    if context.asset_scope != rule.asset_scope:
        return None
    return rule


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
    venue: str,
    base_asset: str,
    quote_asset: str,
    settlement_asset: str | None,
    settlement_style: SettlementStyle | None,
    kind: CryptoInstrumentKind,
) -> tuple[str, ...]:
    """Judge a resolved semantics object against its rule and instrument, fail-closed.

    Returns the deduplicated issues, empty when the semantics are coherent. This
    is the single authority for what "coherent" means, so both the resolver and
    any caller wishing to re-check an authority object share one rule set.
    Every instrument-level check is against the rule's OWN requirement
    (``required_venue``/``required_kind``/``required_settlement_style``), never a
    hardcoded literal -- so a future rule for another Bybit linear instrument
    reuses this exact function unchanged.
    """
    issues: list[str] = []
    if venue != rule.required_venue:
        issues.append(f"instrument_venue_mismatch:{venue}")
    if kind is not rule.required_kind:
        issues.append(f"volume_semantics_requires_{rule.required_kind.value.lower()}:{kind.value}")
    if settlement_style is not rule.required_settlement_style:
        issues.append(
            f"volume_semantics_requires_{rule.required_settlement_style.value.lower()}_settlement"
        )
    # A linear contract settles in the quote asset by definition (the same
    # invariant CryptoInstrumentSpecification itself enforces at construction);
    # re-proving it here means this authority never trusts a specification that
    # somehow bypassed that check.
    if rule.required_settlement_style is SettlementStyle.LINEAR and settlement_asset != quote_asset:
        issues.append("settlement_asset_must_equal_quote_asset_for_linear_contract")
    if semantics.volume_unit is not rule.volume_unit:
        issues.append(f"unsupported_volume_unit:{semantics.volume_unit.value}")
    if semantics.turnover_unit is not rule.turnover_unit:
        issues.append(f"unsupported_turnover_unit:{semantics.turnover_unit.value}")
    if rule.volume_unit in UNITS_REQUIRING_ASSET and semantics.volume_asset != base_asset:
        issues.append("volume_asset_mismatch")
    if rule.turnover_unit in UNITS_REQUIRING_ASSET and semantics.turnover_asset != quote_asset:
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
    venue: str,
    base_asset: str,
    quote_asset: str,
    settlement_asset: str | None,
    settlement_style: SettlementStyle | None,
    kind: CryptoInstrumentKind,
    provider_turnover: object,
) -> tuple[OhlcvVolumeSemantics | None, tuple[str, ...]]:
    """Resolve canonical volume semantics for an authorized OHLCV bar, fail-closed.

    The provider *evidence* consumed is exactly the turnover the adapter already
    preserved; the *authority* is the rule plus the resolved instrument --
    ``venue``/``kind``/``settlement_style``/``settlement_asset`` here all come
    from the canonical crypto-instrument authority, never from the provider
    payload or from the source's own claimed identity. Nothing is converted,
    repaired or synthesized: a missing or invalid turnover, or an instrument
    that is not this rule's required kind of contract, returns ``None`` with the
    issues that explain why, and the pipeline rejects the observation.
    """
    issues: list[str] = []
    turnover = _parse_turnover(provider_turnover, issues)
    volume_asset = _asset_for_unit(rule.volume_unit, base_asset=base_asset, quote_asset=quote_asset)
    turnover_asset = _asset_for_unit(
        rule.turnover_unit, base_asset=base_asset, quote_asset=quote_asset
    )
    # A unit that requires an asset (BASE_ASSET/QUOTE_ASSET) must resolve to
    # one; a bare CONTRACTS unit legitimately resolves to None and that is not
    # an error -- see UNITS_REQUIRING_ASSET.
    if rule.volume_unit in UNITS_REQUIRING_ASSET and volume_asset is None:
        issues.append(f"volume_unit_requires_asset:{rule.volume_unit.value}")
    if rule.turnover_unit in UNITS_REQUIRING_ASSET and turnover_asset is None:
        issues.append(f"turnover_unit_requires_asset:{rule.turnover_unit.value}")
    if turnover is None or issues:
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
        return None, (str(error),)
    coherence = volume_semantics_issues(
        semantics,
        rule=rule,
        venue=venue,
        base_asset=base_asset,
        quote_asset=quote_asset,
        settlement_asset=settlement_asset,
        settlement_style=settlement_style,
        kind=kind,
    )
    if coherence:
        return None, coherence
    return semantics, ()
