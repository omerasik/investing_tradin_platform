"""Immutable, content-addressed policy documents for auditable pre-trade governance."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .domain import RiskPolicy
from .portfolio_risk import PortfolioRiskPolicy, StressScenario


class PolicyRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    policy_type: str
    version: str
    payload: dict[str, object]
    approved_by: str
    approved_at: datetime

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class SQLitePolicyRegistry:
    def __init__(self, database_path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database_path), check_same_thread=False)
        self._connection.execute("CREATE TABLE IF NOT EXISTS policy_documents (policy_type TEXT NOT NULL, version TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL, PRIMARY KEY(policy_type, version))")
        self._connection.commit()

    def append(self, document: PolicyDocument) -> None:
        if document.policy_type not in {"risk", "portfolio"} or not document.version.strip() or not document.approved_by.strip() or not document.payload or document.approved_at.tzinfo is None or document.approved_at.utcoffset() is None:
            raise PolicyRegistryError("invalid_policy_document")
        try:
            self._connection.execute("INSERT INTO policy_documents VALUES (?, ?, ?, ?, ?, ?)", (document.policy_type, document.version, json.dumps(document.payload, sort_keys=True), document.digest, document.approved_by, document.approved_at.isoformat()))
        except sqlite3.IntegrityError as error:
            raise PolicyRegistryError("duplicate_policy_version") from error
        self._connection.commit()

    def get(self, policy_type: str, version: str) -> PolicyDocument:
        row = self._connection.execute("SELECT policy_type, version, payload_json, digest, approved_by, approved_at FROM policy_documents WHERE policy_type = ? AND version = ?", (policy_type, version)).fetchone()
        if row is None:
            raise PolicyRegistryError("unknown_policy_version")
        document = PolicyDocument(row[0], row[1], json.loads(row[2]), row[4], datetime.fromisoformat(row[5]))
        if document.digest != row[3]:
            raise PolicyRegistryError("policy_document_digest_mismatch")
        return document

    def resolve_risk_policy(self, version: str) -> RiskPolicy:
        document = self.get("risk", version)
        allowed = {"minimum_data_quality", "maximum_spread_fraction", "maximum_order_notional", "maximum_position_notional", "maximum_daily_order_notional", "maximum_event_risk", "maximum_expected_slippage_fraction", "max_market_age_seconds"}
        if set(document.payload) - allowed:
            raise PolicyRegistryError("invalid_risk_policy_payload")
        try:
            values = {key: int(value) if key == "max_market_age_seconds" else Decimal(str(value)) for key, value in document.payload.items()}
            return RiskPolicy(**values)
        except (TypeError, ValueError, ArithmeticError) as error:
            raise PolicyRegistryError("invalid_risk_policy_payload") from error

    def resolve_portfolio_policy(self, version: str) -> PortfolioRiskPolicy:
        """Decode a reviewed portfolio-policy document without caller supplied limits."""
        document = self.get("portfolio", version)
        required = {"maximum_gross_notional", "maximum_single_weight", "maximum_scenario_loss"}
        decimal_fields = {
            "maximum_gross_notional", "maximum_single_weight", "maximum_scenario_loss",
            "maximum_net_notional", "maximum_var_loss", "maximum_cvar_loss",
            "maximum_beta_notional", "maximum_duration_notional", "maximum_drawdown_loss",
        }
        integer_fields = {"minimum_historical_observations", "return_history_window_observations", "maximum_return_history_age_seconds"}
        mapping_fields = {"maximum_group_notionals", "maximum_factor_notionals"}
        allowed = decimal_fields | integer_fields | mapping_fields | {"stress_scenarios"}
        if required - set(document.payload) or set(document.payload) - allowed:
            raise PolicyRegistryError("invalid_portfolio_policy_payload")
        try:
            values: dict[str, object] = {}
            for field in decimal_fields & set(document.payload):
                value = document.payload[field]
                if value is None and field not in required:
                    values[field] = None
                elif value is None:
                    raise TypeError("missing_required_limit")
                else:
                    values[field] = Decimal(str(value))
            for field in integer_fields & set(document.payload):
                values[field] = int(document.payload[field])
            for field in mapping_fields & set(document.payload):
                mapping = document.payload[field]
                if not isinstance(mapping, dict) or any(not isinstance(name, str) or not name.strip() for name in mapping):
                    raise TypeError("invalid_exposure_mapping")
                values[field] = {name: Decimal(str(limit)) for name, limit in mapping.items()}
            if any(field in values for field in ("maximum_var_loss", "maximum_cvar_loss", "maximum_drawdown_loss")) and not {"minimum_historical_observations", "return_history_window_observations", "maximum_return_history_age_seconds"} <= set(values):
                raise TypeError("missing_historical_return_requirements")
            return PortfolioRiskPolicy(**values)
        except (TypeError, ValueError, ArithmeticError) as error:
            raise PolicyRegistryError("invalid_portfolio_policy_payload") from error

    def resolve_portfolio_scenarios(self, version: str) -> tuple[StressScenario, ...]:
        """Resolve the non-empty, reviewed stress suite bound to a portfolio policy version."""
        document = self.get("portfolio", version)
        payload = document.payload.get("stress_scenarios")
        if not isinstance(payload, dict) or not payload:
            raise PolicyRegistryError("portfolio_stress_scenarios_unavailable")
        try:
            scenarios = tuple(
                StressScenario(name, {shock_key: Decimal(str(shock)) for shock_key, shock in shocks.items()})
                for name, shocks in sorted(payload.items())
            )
            if any(not scenario.name.strip() or not scenario.shocks or any(not key.strip() or not value.is_finite() for key, value in scenario.shocks.items()) for scenario in scenarios):
                raise TypeError("invalid_scenario")
            return scenarios
        except (AttributeError, TypeError, ValueError, ArithmeticError) as error:
            raise PolicyRegistryError("invalid_portfolio_stress_scenarios") from error

    def close(self) -> None:
        self._connection.close()
