"""Module 3G.1c: pilot-readiness / activation gates for the disabled Databento adapter.

This module makes **no network call, ever**, and activates nothing. Its only job is
to prove -- deterministically and fail-closed -- that a fully-specified pilot
configuration would reach the point immediately before the first real network
request, or to say exactly which required gate is missing. Module 3G.1d (a
separately, explicitly owner-authorized module) is the only place a real Databento
request may ever be made, and only after every gate this module checks passes AND
the owner has given per-run authorization for that specific run.

No second authority is introduced here: this reuses the existing
``AuthorizedHistoricalSource`` (historical_market_data.py), ``ProviderConfiguration``
(data_providers.py), ``EnvironmentSecretResolver`` (config.py) and the real
``DatabentoHistoricalAdapter``/``preflight()``/``plan_chunks()``
(databento_provider.py) -- it only adds the pilot-specific policy gates (bounded
universe/window, cost ceiling, per-run approval, operator attestation) that do not
already exist anywhere else, and wires them together in one fail-closed sequence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from .data_providers import ProviderConfiguration
from .databento_provider import (
    DATABENTO_MAXIMUM_SYMBOLS_PER_REQUEST,
    SUPPORTED_SCHEMAS,
    DatabentoHistoricalAdapter,
    DatabentoHttpTransport,
    plan_chunks,
)
from .historical_market_data import AuthorizedHistoricalSource

#: Databento datasets this module's readiness checks accept, one per approved
#: schema (see docs/MODULE_3G1C_PILOT_READINESS_AND_ACTIVATION_GATES.md). A
#: mismatch (e.g. requesting "ohlcv-1m" against "EQUS.SUMMARY") is rejected --
#: EQUS.SUMMARY is documented as an EOD/summary product, EQUS.MINI as the
#: minute-resolution consolidated product.
EXPECTED_DATASET_FOR_SCHEMA = {"ohlcv-1d": "EQUS.SUMMARY", "ohlcv-1m": "EQUS.MINI"}

#: This module's own bound on pilot size -- deliberately tighter than Databento's
#: documented 2,000-symbols-per-request cap (DATABENTO_MAXIMUM_SYMBOLS_PER_REQUEST).
#: A "bounded pilot" per the owner's approved scope means 15-30 instruments; this
#: is the pilot-policy ceiling, not the provider's technical ceiling.
MAXIMUM_PILOT_SYMBOLS = 30

#: Daily is the primary, multi-year-eventually research slice; minute is a bounded
#: secondary integration slice with no long-history-intraday claim (owner decision,
#: Module 3G.1 OWNER DECISION block, item 7).
MAXIMUM_WINDOW_FOR_SCHEMA = {"ohlcv-1d": timedelta(days=365 * 20), "ohlcv-1m": timedelta(days=90)}

_APPROVED_ASSET_SCOPE = "US_EQUITIES_ETFS"


class PilotReadinessError(ValueError):
    pass


class ActivationChecklistCode(StrEnum):
    """The exact A-O operator checklist from the Module 3G.1c owner instructions.

    Each code represents one item a human must have actually done or reviewed --
    code cannot verify a contract was read or that retention rights were confirmed
    in writing. What code CAN and DOES enforce is that every one of these has been
    explicitly, individually attested by the operator before a pilot may proceed;
    none defaults to true, and nothing here is inferred from the others.
    """

    ACCOUNT_EXISTS = "ACCOUNT_EXISTS"  # A
    LICENSE_REVIEWED = "LICENSE_REVIEWED"  # B
    RETENTION_RIGHTS_CONFIRMED = "RETENTION_RIGHTS_CONFIRMED"  # C
    NON_DISPLAY_RIGHTS_CONFIRMED = "NON_DISPLAY_RIGHTS_CONFIRMED"  # D
    API_KEY_CREATED = "API_KEY_CREATED"  # E  # pragma: allowlist secret
    SECRET_STORED_EXTERNALLY = "SECRET_STORED_EXTERNALLY"  # F  # nosec B105  # pragma: allowlist secret
    SOURCE_REGISTRATION_REVIEWED = "SOURCE_REGISTRATION_REVIEWED"  # G
    PILOT_UNIVERSE_REVIEWED = "PILOT_UNIVERSE_REVIEWED"  # H
    DATE_RANGES_RESOLUTIONS_REVIEWED = "DATE_RANGES_RESOLUTIONS_REVIEWED"  # I
    COST_ESTIMATE_OBTAINED = "COST_ESTIMATE_OBTAINED"  # J
    COST_WITHIN_CEILING = "COST_WITHIN_CEILING"  # K
    RAW_UNADJUSTED_CONFIRMED = "RAW_UNADJUSTED_CONFIRMED"  # L
    CORPORATE_ACTION_LIMITATIONS_ACKNOWLEDGED = "CORPORATE_ACTION_LIMITATIONS_ACKNOWLEDGED"  # M
    PER_RUN_AUTHORIZATION_RECEIVED = "PER_RUN_AUTHORIZATION_RECEIVED"  # N
    RESTORE_RECOVERY_PLAN_CONFIRMED = "RESTORE_RECOVERY_PLAN_CONFIRMED"  # O


@dataclass(frozen=True, slots=True)
class ActivationAttestation:
    """The operator's explicit record of which checklist items are actually done.

    ``confirmed`` must be populated one code at a time by the operator (or an
    operator-driven runbook step) -- there is no "confirm all" shortcut and no
    default of true. See ``docs/MODULE_3G1C_PILOT_READINESS_AND_ACTIVATION_GATES.md``
    for what each code requires the operator to have actually done.
    """

    confirmed: frozenset[ActivationChecklistCode] = field(default_factory=frozenset)

    def missing(self) -> tuple[ActivationChecklistCode, ...]:
        return tuple(code for code in ActivationChecklistCode if code not in self.confirmed)

    def is_complete(self) -> bool:
        return not self.missing()


@dataclass(frozen=True, slots=True)
class DatabentoPilotConfiguration:
    """One leg (e.g. "daily" or "minute") of a bounded Databento pilot."""

    source: AuthorizedHistoricalSource
    provider_configuration: ProviderConfiguration
    symbols: tuple[str, ...]
    dataset: str
    schema: str
    start: date
    end: date
    chunk_size: timedelta
    cost_ceiling_usd: Decimal
    estimated_cost_usd: Decimal | None
    execution_approved: bool
    attestation: ActivationAttestation

    def configuration_identity(self) -> str:
        """Deterministic identity over every non-secret field -- never the API key,
        never a resolved secret value, never anything from ``provider_configuration``
        beyond its ``provider``/``base_url``/``terms_accepted`` (never
        ``secret_reference``, which is a reference name and not itself sensitive, but
        is deliberately excluded anyway to keep this hash purely about *what data is
        being requested*, not *how it's authenticated*).
        """
        canonical = json.dumps(
            {
                "provider": self.provider_configuration.provider,
                "terms_accepted": self.provider_configuration.terms_accepted,
                "source_id": str(self.source.source_id),
                "source_provider": self.source.provider,
                "source_asset_scope": self.source.asset_scope,
                "symbols": sorted(self.symbols),
                "dataset": self.dataset,
                "schema": self.schema,
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "chunk_days": self.chunk_size.days,
                "cost_ceiling_usd": str(self.cost_ceiling_usd),
            },
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PilotReadinessReport:
    ready: bool
    configuration_identity: str
    chunk_plan: tuple[tuple[date, date], ...]
    secret_reference: str


def assess_pilot_readiness(
    config: DatabentoPilotConfiguration, *, transport: DatabentoHttpTransport | None = None,
) -> PilotReadinessReport:
    """Fail-closed sequential gate. Raises ``PilotReadinessError`` (or a
    ``ProviderConfigurationError``/``ProviderError`` propagated from the real
    adapter's own ``preflight()``) on the FIRST missing/invalid gate. Returns a
    report only when every gate -- including the real adapter's own pre-network
    validation -- has passed. Never makes a network call.
    """
    if not config.attestation.is_complete():
        missing = ",".join(code.value for code in config.attestation.missing())
        raise PilotReadinessError(f"activation_checklist_incomplete:{missing}")

    if config.source.provider != "databento":
        raise PilotReadinessError("pilot_source_provider_must_be_databento")
    if config.source.asset_scope != _APPROVED_ASSET_SCOPE:
        raise PilotReadinessError("pilot_source_asset_scope_not_approved")
    config.source.validate()

    if config.provider_configuration.provider != "databento":
        raise PilotReadinessError("pilot_provider_configuration_must_be_databento")
    if not config.provider_configuration.terms_accepted:
        raise PilotReadinessError("pilot_provider_terms_not_accepted")

    if not config.symbols or len(config.symbols) > MAXIMUM_PILOT_SYMBOLS:
        raise PilotReadinessError("pilot_universe_out_of_bounds")
    if len(config.symbols) > DATABENTO_MAXIMUM_SYMBOLS_PER_REQUEST:
        raise PilotReadinessError("pilot_universe_exceeds_provider_request_limit")
    if len(set(config.symbols)) != len(config.symbols):
        raise PilotReadinessError("pilot_universe_contains_duplicate_symbols")

    if config.schema not in SUPPORTED_SCHEMAS:
        raise PilotReadinessError("pilot_schema_not_supported")
    if config.dataset != EXPECTED_DATASET_FOR_SCHEMA[config.schema]:
        raise PilotReadinessError("pilot_dataset_schema_mismatch")

    if config.end <= config.start:
        raise PilotReadinessError("pilot_date_range_invalid")
    if config.end - config.start > MAXIMUM_WINDOW_FOR_SCHEMA[config.schema]:
        raise PilotReadinessError("pilot_date_range_exceeds_bounded_window")

    if config.estimated_cost_usd is None:
        raise PilotReadinessError("pilot_cost_estimate_missing")
    if config.estimated_cost_usd < 0:
        raise PilotReadinessError("pilot_cost_estimate_invalid")
    if config.estimated_cost_usd > config.cost_ceiling_usd:
        raise PilotReadinessError("pilot_cost_exceeds_approved_ceiling")

    if not config.execution_approved:
        raise PilotReadinessError("pilot_execution_not_approved")

    # Reuse the REAL adapter's own pre-network validation rather than
    # re-implementing it -- proves this exact configuration would actually be
    # accepted by fetch_raw_page's real prefix, not by a parallel approximation
    # of it that could silently drift from the real adapter's behavior.
    adapter = DatabentoHistoricalAdapter(
        config.provider_configuration, transport=transport, chunk_size=config.chunk_size,
    )
    scope: dict[str, object] = {
        "dataset": config.dataset, "schema": config.schema, "symbols": list(config.symbols),
        "start": config.start.isoformat(), "end": config.end.isoformat(),
    }
    adapter.preflight(scope, None)

    chunk_plan = plan_chunks(config.start, config.end, config.chunk_size)
    return PilotReadinessReport(
        ready=True, configuration_identity=config.configuration_identity(),
        chunk_plan=chunk_plan, secret_reference=config.provider_configuration.secret_reference or "",
    )
