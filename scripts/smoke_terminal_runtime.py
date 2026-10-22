"""Read-only protected paper runtime smoke; never seeds evidence or starts workers.

Run in the intended local/staging Python environment, with its deployment-owned
configuration. The result checks runtime composition, not trading readiness.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID


class SmokeRefused(ValueError):
    """A required runtime assertion could not be demonstrated."""


def describe_clock_sample(app_before: datetime, db_at: datetime,
                          app_after: datetime) -> dict[str, Any]:
    """Describe one DB time sample within an application wall-clock bracket."""
    if any(value.tzinfo is None or value.utcoffset() is None
           for value in (app_before, db_at, app_after)):
        raise SmokeRefused("CLOCK_SAMPLE_REQUIRES_AWARE_TIMES")
    if app_after < app_before:
        raise SmokeRefused("CLOCK_SAMPLE_APP_TIME_REVERSED")
    lower = db_at - app_after
    upper = db_at - app_before
    lower_us = lower // timedelta(microseconds=1)
    upper_us = upper // timedelta(microseconds=1)
    relation = ("DB_AHEAD_OF_APP_BRACKET" if lower_us > 0 else
                "DB_BEHIND_APP_BRACKET" if upper_us < 0 else
                "OVERLAPS_APP_BRACKET")
    return {
        "app_before_utc": app_before.astimezone(UTC).isoformat(),
        "postgres_sample_utc": db_at.astimezone(UTC).isoformat(),
        "app_after_utc": app_after.astimezone(UTC).isoformat(),
        "app_bracket_width_us": upper_us - lower_us,
        "db_minus_app_interval_us": [lower_us, upper_us],
        "observed_relation": relation,
        "scope": "ONE_READ_ONLY_SAMPLE_NOT_CONTINUOUS_CLOCK_HEALTH",
    }


def _application_now() -> datetime:
    return datetime.now(UTC)


def sample_postgres_clock(app: Any) -> dict[str, Any]:
    """Use the canonical app's PostgreSQL connection; do not open another runtime."""
    authorities = getattr(app.state, "postgres_authorities", None)
    if authorities is None:
        raise SmokeRefused("POSTGRES_CLOCK_SOURCE_UNAVAILABLE")
    with authorities.database.transaction() as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        app_before = _application_now()
        cursor.execute("SELECT clock_timestamp()")
        row = cursor.fetchone()
        app_after = _application_now()
    if not isinstance(row, (tuple, list)) or len(row) != 1 or not isinstance(row[0], datetime):
        raise SmokeRefused("POSTGRES_CLOCK_SAMPLE_UNAVAILABLE")
    return describe_clock_sample(app_before, row[0], app_after)


def configuration(profile: str, *, require_proven_price: bool) -> dict[str, Any]:
    if os.environ.get("TRADE_PLATFORM_ENVIRONMENT") != "paper":
        raise SmokeRefused("SMOKE_REQUIRES_EXPLICIT_PAPER_ENVIRONMENT")
    for name in ("POSTGRES_DSN", "TRADE_PLATFORM_OPERATOR_TOKEN"):
        if not os.environ.get(name, "").strip():
            raise SmokeRefused(f"REQUIRED_CONFIGURATION_MISSING:{name}")
    modules = ("numpy", "duckdb", "pyarrow")
    available = {name: importlib.util.find_spec(name) is not None for name in modules}
    if profile == "analytical":
        for name in (*modules, "trade_platform.research_data_plane_v1",
                     "trade_platform.paper_evidence_production_v1"):
            try:
                importlib.import_module(name)
            except ImportError:
                raise SmokeRefused(f"ANALYTICAL_IMPORT_UNAVAILABLE:{name}") from None
    from trade_platform.terminal_paper_prices_v1 import terminal_paper_prices_from_environment_v1

    source = terminal_paper_prices_from_environment_v1()
    if require_proven_price:
        if profile != "analytical" or source is None:
            raise SmokeRefused("PROVEN_PRICE_REQUIRES_ANALYTICAL_PROFILE_AND_PINNED_POLICY")
        for name in ("TRADE_PLATFORM_RESEARCH_DATA_ROOT",
                     "TRADE_PLATFORM_UNIVERSE_CAPTURE_ARCHIVE_ROOT"):
            path = os.environ.get(name)
            if not path or not Path(path).is_dir():
                raise SmokeRefused(f"REQUIRED_EVIDENCE_ROOT_UNAVAILABLE:{name}")
    return {"profile": profile, "analytical_modules_present": available,
            "price_policy": "PINNED" if source else "NOT_CONFIGURED"}


def probe(app: Any, *, token: str, study_id: UUID | None, trial_id: UUID | None,
          require_proven_price: bool) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from trade_platform.research_terminal_read_model_v1 import (
        CandidatePaperEvidenceView,
        TerminalOverview,
    )

    path = "/operator-dashboard/research-terminal/overview"
    with TestClient(app, raise_server_exceptions=False) as client:
        if client.get("/health/live").status_code != 200:
            raise SmokeRefused("LIVENESS_FAILED")
        ready = client.get("/health/ready")
        if ready.status_code != 200 or ready.json() != {
            "status": "ready", "environment": "paper",
            "paper_trading_enabled": True, "live_trading_enabled": False,
        }:
            raise SmokeRefused("PROTECTED_POSTGRES_READINESS_FAILED")
        if client.get(path).status_code != 401:
            raise SmokeRefused("UNAUTHENTICATED_EVIDENCE_READ_NOT_REJECTED")
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get(path, headers=headers)
        if response.status_code != 200:
            raise SmokeRefused(f"AUTHENTICATED_OVERVIEW_FAILED:HTTP_{response.status_code}")
        overview = TerminalOverview.model_validate(response.json())
        result: dict[str, Any] = {
            "database": "CONNECTED_VIA_PROTECTED_READINESS_AND_TERMINAL_QUERY",
            "authenticated_evidence_read": "PASS", "studies": overview.studies,
            "cycle_id": overview.cycle.cycle_id,
            "owner_gates": [gate.model_dump() for gate in overview.owner_gates],
            "proven_price": "NOT_ASSESSED", "research_validation": "NOT_ASSESSED",
        }
        if study_id is not None and trial_id is not None:
            path = (f"/operator-dashboard/research-terminal/candidates/{study_id}/"
                    f"{trial_id}/paper-evidence")
            response = client.get(path, headers=headers)
            if response.status_code != 200:
                raise SmokeRefused(f"CANDIDATE_EVIDENCE_FAILED:HTTP_{response.status_code}")
            view = CandidatePaperEvidenceView.model_validate(response.json())
            if view.study_id != study_id or view.trial_id != trial_id:
                raise SmokeRefused("CANDIDATE_EVIDENCE_IDENTITY_MISMATCH")
            unrealized = view.economics.gross_unrealized
            result["proven_price"] = unrealized.status if unrealized else "NOT_LOADED"
            result["price_reasons"] = unrealized.reasons if unrealized else []
            if require_proven_price and (unrealized is None or unrealized.status != "AVAILABLE"):
                raise SmokeRefused("PROVEN_PRICE_EVIDENCE_UNAVAILABLE")
        elif require_proven_price:
            raise SmokeRefused("PROVEN_PRICE_REQUIRES_EXISTING_STUDY_AND_TRIAL")
        result["application_postgres_clock"] = sample_postgres_clock(app)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("thin", "analytical"), required=True)
    parser.add_argument("--study-id", type=UUID)
    parser.add_argument("--trial-id", type=UUID)
    parser.add_argument("--require-proven-price", action="store_true")
    args = parser.parse_args()
    if bool(args.study_id) != bool(args.trial_id):
        parser.error("--study-id and --trial-id must be supplied together")
    try:
        result = configuration(args.profile, require_proven_price=args.require_proven_price)
        # Import the actual ASGI target only after explicit paper/config preflight.
        # Its startup and shutdown own the PostgreSQL connection; no alternative
        # composition, migrations, fixture seed or write endpoint is invoked here.
        from trade_platform.runtime_app import app

        result.update(probe(app, token=os.environ["TRADE_PLATFORM_OPERATOR_TOKEN"],
                            study_id=args.study_id, trial_id=args.trial_id,
                            require_proven_price=args.require_proven_price))
    except Exception as error:  # noqa: BLE001 - CLI boundary must redact third-party secrets
        # A DSN/token or evidence payload may appear in third-party exceptions.
        # Only our bounded refusal codes are safe for an operator-facing result.
        reason = str(error) if isinstance(error, SmokeRefused) else type(error).__name__
        print(json.dumps({"status": "FAIL", "reason": reason}))
        return 1
    print(json.dumps({"status": "PASS", "scope": "RUNTIME_SMOKE_ONLY", **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
