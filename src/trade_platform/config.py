"""Configuration with a non-bypassable paper-only safety boundary."""

import os
from dataclasses import dataclass
from pathlib import Path


class LiveTradingForbiddenError(ValueError):
    """Raised whenever a configuration attempts to enable live execution."""


class SecretReferenceError(ValueError):
    """Raised when a required secret reference cannot be resolved safely."""


class EnvironmentSecretResolver:
    """Resolves only explicit ``env:NAME`` references without logging secret values."""

    def resolve_bytes(self, reference: str) -> bytes:
        if not reference.startswith("env:") or not reference[4:] or not reference[4:].replace("_", "").isalnum():
            raise SecretReferenceError("invalid_secret_reference")
        value = os.environ.get(reference[4:])
        if value is None or not value:
            raise SecretReferenceError("secret_reference_unavailable")
        return value.encode()


@dataclass(frozen=True, slots=True)
class PlatformConfig:
    environment: str = "local_research"
    live_trading_enabled: bool = False
    paper_trading_enabled: bool = True
    assessment_integrity_key_reference: str | None = None

    def __post_init__(self) -> None:
        if self.live_trading_enabled:
            raise LiveTradingForbiddenError(
                "Live trading is prohibited by this build; only internal paper simulation is available."
            )
        if not self.paper_trading_enabled:
            raise ValueError("Paper trading must remain enabled for the current platform mode.")

    def assessment_integrity_key(self, resolver: EnvironmentSecretResolver | None = None) -> bytes:
        if self.assessment_integrity_key_reference is None:
            raise SecretReferenceError("assessment_integrity_key_reference_required")
        return (resolver or EnvironmentSecretResolver()).resolve_bytes(self.assessment_integrity_key_reference)

    def create_assessment_store(self, database_path: str | Path = ":memory:", *, resolver: EnvironmentSecretResolver | None = None):
        """Build the only configuration-backed store suitable for paper submission."""
        from .pretrade_assessment import SQLitePreTradeAssessmentStore

        return SQLitePreTradeAssessmentStore(database_path, integrity_key=self.assessment_integrity_key(resolver))
