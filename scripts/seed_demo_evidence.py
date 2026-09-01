"""Seed one deterministic, PostgreSQL-only Module 1B engineering scenario.

This command is explicit and local-only.  It has no provider, broker, or other
network path.  Every identity is UUID5 derived from ``DEMO_SEED_VERSION`` and
every persisted claim is labelled synthetic engineering evidence.

The existing repositories cover the professional instrument and feature
authorities.  A few legacy, immutable evidence tables (the core research
experiment, legacy-paper lifecycle bridge, and cross-engine link tables) have
no public construction repository.  Those narrow bridges are inserted here
with the same foreign-key, hash, lifecycle, and immutability constraints that
their authorities enforce; this is deliberately documented rather than a new
runtime write API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid5

from trade_platform.feature_authority import (
    FeatureDefinitionVersion,
    FeatureFamily,
    FeatureMaterialization,
    FeatureQualityStatus,
    PostgresFeatureAuthority,
)
from trade_platform.persistence import PostgresDatabase
from trade_platform.professional_instruments import (
    IdentifierMapping,
    IdentifierSourceKind,
    PostgresProfessionalInstrumentMaster,
    SymbolMapping,
    mvp_instrument_universe,
)

DEMO_SEED_VERSION = "module1b-demo-evidence-v1"
DEMO_NAMESPACE = UUID("2472e4e8-c287-5298-a8b4-7a5d9fb78cae")
DEMO_AT = datetime(2026, 9, 1, 12, tzinfo=UTC)
DEMO_SOURCE = "SYNTHETIC_DEMO_ENGINEERING_EVIDENCE"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def stable_id(name: str) -> UUID:
    return uuid5(DEMO_NAMESPACE, f"{DEMO_SEED_VERSION}:{name}")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, default=str, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _local_postgres_dsn(dsn: str) -> None:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgresql", "postgres"} or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError("demo_seed_requires_local_postgresql_dsn")


def _insert(cursor: Any, statement: str, params: tuple[object, ...]) -> None:
    """Idempotent inserts rely on immutable natural/UUID identities.

    A conflict is intentionally not overwritten.  PostgreSQL's immutable
    triggers preserve evidence and a conflicting caller must investigate rather
    than erase a prior evidence record.
    """
    cursor.execute(statement, params)


def _seed_instruments(database: PostgresDatabase) -> dict[str, UUID]:
    master = PostgresProfessionalInstrumentMaster(database)
    base = mvp_instrument_universe(DEMO_AT)
    variants = (
        ("DEMO:XNAS:DEMO_EQ_A", "DEMO_EQ_A", "Demo Equity A"),
        ("DEMO:XNAS:DEMO_EQ_B", "DEMO_EQ_B", "Demo Equity B"),
        ("DEMO:ARCX:DEMO_ETF", "DEMO_ETF", "Demo Reference ETF"),
    )
    core: dict[str, UUID] = {}
    for index, (instrument_id, symbol, _name) in enumerate(variants):
        template = base[1] if symbol.endswith("ETF") else base[0]
        professional = replace(template, instrument_id=instrument_id, canonical_symbol=symbol, registered_at=DEMO_AT)
        symbol_mapping_id = stable_id(f"symbol-mapping:{symbol}")
        identifier_mapping_id = stable_id(f"identifier-mapping:{symbol}")
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT canonical_symbol,venue FROM professional_instruments WHERE instrument_id=%s",
                (instrument_id,),
            )
            existing_instrument = cursor.fetchone()
            cursor.execute(
                "SELECT instrument_id,venue,symbol FROM professional_symbol_mappings WHERE mapping_id=%s",
                (symbol_mapping_id,),
            )
            existing_symbol = cursor.fetchone()
            cursor.execute(
                "SELECT instrument_id,namespace,identifier_value FROM professional_identifier_mappings WHERE mapping_id=%s",
                (identifier_mapping_id,),
            )
            existing_identifier = cursor.fetchone()
        if existing_instrument is None:
            master.register(professional)
        elif tuple(map(str, existing_instrument)) != (symbol, professional.venue):
            raise RuntimeError("demo_seed_conflicting_instrument_identity")
        mapping = SymbolMapping(instrument_id, professional.venue, symbol, DEMO_AT, None, DEMO_AT, f"demo://{DEMO_SEED_VERSION}/instrument/{symbol}", symbol_mapping_id)
        if existing_symbol is None:
            master.add_symbol_mapping(mapping)
        elif tuple(map(str, existing_symbol)) != (instrument_id, professional.venue, symbol):
            raise RuntimeError("demo_seed_conflicting_symbol_mapping")
        identifier = IdentifierMapping(instrument_id, IdentifierSourceKind.PROVIDER, "DEMO:INSTRUMENT", symbol, DEMO_AT, None, DEMO_AT, f"demo://{DEMO_SEED_VERSION}/identifier/{symbol}", identifier_mapping_id)
        if existing_identifier is None:
            master.add_identifier_mapping(identifier)
        elif tuple(map(str, existing_identifier)) != (instrument_id, "DEMO:INSTRUMENT", symbol):
            raise RuntimeError("demo_seed_conflicting_identifier_mapping")
        core[symbol] = stable_id(f"core-instrument:{symbol}")

    exchange_id = stable_id("core-exchange")
    with database.transaction() as connection, connection.cursor() as cursor:
        _insert(cursor, "INSERT INTO exchanges(exchange_id,mic,name,timezone,created_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (exchange_id) DO NOTHING", (exchange_id, "XDEM", "Demo Exchange", "UTC", DEMO_AT))
        for instrument_id, symbol, _name in variants:
            asset_class = "ETF" if symbol.endswith("ETF") else "EQUITY"
            venue = "ARCX" if symbol == "DEMO_ETF" else "XNAS"
            _insert(cursor, "INSERT INTO runtime_instruments(instrument_id,symbol,venue,asset_class,quote_currency,tick_size,lot_size) VALUES (%s,%s,%s,%s,'USD',0.01,1) ON CONFLICT (instrument_id) DO NOTHING", (instrument_id, symbol, venue, asset_class))
            _insert(cursor, "INSERT INTO instruments(instrument_id,exchange_id,canonical_symbol,asset_class,currency,tick_size,lot_size,active_from,created_at) VALUES (%s,%s,%s,%s,'USD',0.01,1,%s,%s) ON CONFLICT (instrument_id) DO NOTHING", (core[symbol], exchange_id, symbol, asset_class, DEMO_AT, DEMO_AT))
    return core


def _seed_research_chain(database: PostgresDatabase, core: dict[str, UUID]) -> dict[str, UUID]:
    ids = {name: stable_id(name) for name in (
        "dataset", "dataset-version", "historical-source", "historical-dataset", "health", "strategy", "strategy-version", "experiment", "validation-package", "scorecard", "regime-model", "regime-run", "regime-observation", "regime-candidate", "portfolio-policy", "portfolio-run", "portfolio-sleeve", "portfolio-covariance", "portfolio-target", "portfolio-constraint", "signal", "blocked-signal", "risk-policy", "risk-policy-version", "paper-intent", "blocked-intent", "paper-reconciliation", "reconciled-account",
    )}
    dataset_hash = digest({"source": DEMO_SOURCE, "version": DEMO_SEED_VERSION, "kind": "OHLCV"})
    historical_members: list[tuple[UUID, UUID, str, datetime, dict[str, str]]] = []
    for symbol, instrument in (("DEMO_EQ_A", "DEMO:XNAS:DEMO_EQ_A"), ("DEMO_EQ_B", "DEMO:XNAS:DEMO_EQ_B"), ("DEMO_ETF", "DEMO:ARCX:DEMO_ETF")):
        for day in range(3):
            event_at = DEMO_AT - timedelta(days=3 - day)
            payload = {"interval": "1d", "open": str(100 + day), "high": str(102 + day), "low": str(99 + day), "close": str(101 + day), "volume": str(1000 + day)}
            historical_members.append((stable_id(f"raw:{symbol}:{day}"), stable_id(f"normalized:{symbol}:{day}"), instrument, event_at, payload))
    feature_ids: list[UUID] = []
    authority = PostgresFeatureAuthority(database)
    for name, family in (("demo_return", FeatureFamily.PRICE_RETURNS), ("demo_trend", FeatureFamily.TREND), ("demo_momentum", FeatureFamily.MOMENTUM), ("demo_volatility", FeatureFamily.VOLATILITY)):
        definition = FeatureDefinitionVersion(name, family, "1.0.0", "demo-engineering", f"{DEMO_SOURCE}: transparent {name} feature.", ("OHLCV",), ("close", "event_at", "knowledge_at"), "1d", "PIT: event, effective and knowledge timestamps must precede decision time.", 1, {"demo": True}, "reject", "reject", "reject_future_knowledge", None, None, "fraction", DEMO_SEED_VERSION, DEMO_AT, feature_id=stable_id(f"feature:{name}"))
        with database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT name,semantic_version,calculation_version FROM feature_definition_versions WHERE feature_id=%s", (definition.feature_id,))
            existing_definition = cursor.fetchone()
        if existing_definition is None:
            authority.register(definition)
        elif tuple(map(str, existing_definition)) != (name, "1.0.0", DEMO_SEED_VERSION):
            raise RuntimeError("demo_seed_conflicting_feature_definition")
        feature_ids.append(definition.feature_id)
        materialization = replace(FeatureMaterialization.create(feature_id=definition.feature_id, instrument_id="DEMO:XNAS:DEMO_EQ_A", dataset_version=DEMO_SEED_VERSION, event_at=DEMO_AT - timedelta(days=1), effective_at=DEMO_AT - timedelta(days=1), knowledge_at=DEMO_AT - timedelta(hours=12), computed_at=DEMO_AT - timedelta(hours=11), source_observation_manifest=(f"demo:{dataset_hash}",), value=Decimal("0.01"), quality_status=FeatureQualityStatus.VALIDATED), materialization_id=stable_id(f"feature-materialization:{name}"))
        authority.materialize(materialization)

    strategy_contract = {"required_datasets": [DEMO_SEED_VERSION], "evidence_classification": DEMO_SOURCE, "execution_authority": False}
    with database.transaction() as connection, connection.cursor() as cursor:
        _insert(cursor, "INSERT INTO datasets(dataset_id,name,provider,terms_version,created_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (dataset_id) DO NOTHING", (ids["dataset"], "Module 1B synthetic OHLCV", DEMO_SOURCE, DEMO_SEED_VERSION, DEMO_AT))
        _insert(cursor, "INSERT INTO dataset_versions(dataset_version_id,dataset_id,version,content_hash,valid_from,valid_to,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (dataset_version_id) DO NOTHING", (ids["dataset-version"], ids["dataset"], DEMO_SEED_VERSION, dataset_hash, DEMO_AT - timedelta(days=10), DEMO_AT, DEMO_AT))
        _insert(cursor, "INSERT INTO historical_data_sources VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (source_id) DO NOTHING", (ids["historical-source"], DEMO_SOURCE, "module1b-demo-ohlcv", "DEMO:INSTRUMENT", DEMO_SEED_VERSION, f"demo://{DEMO_SEED_VERSION}/terms", DEMO_AT, "US_EQUITIES_ETFS", DEMO_AT))
        for raw_id, normalized_id, instrument_id, event_at, payload in historical_members:
            payload_hash = digest(payload)
            symbol = instrument_id.rsplit(":", 1)[1]
            _insert(cursor, "INSERT INTO historical_raw_observations VALUES (%s,%s,'OHLCV',%s,%s,%s,%s,%s,%s,'RAW',0,%s,%s::jsonb,%s) ON CONFLICT (raw_observation_id) DO NOTHING", (raw_id, ids["historical-source"], symbol, symbol, "ARCX" if symbol == "DEMO_ETF" else "XNAS", event_at, event_at, event_at + timedelta(hours=1), f"demo://{DEMO_SEED_VERSION}/bar/{symbol}/{event_at.date()}", json.dumps(payload), payload_hash))
            _insert(cursor, "INSERT INTO historical_normalized_observations VALUES (%s,%s,%s,'demo-normalizer-v1',%s::jsonb,'VALIDATED','[]'::jsonb,%s) ON CONFLICT (normalized_observation_id) DO NOTHING", (normalized_id, raw_id, instrument_id, json.dumps(payload), event_at + timedelta(hours=2)))
        _insert(cursor, "INSERT INTO historical_dataset_versions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'SEALED') ON CONFLICT (dataset_version_id) DO NOTHING", (ids["historical-dataset"], ids["historical-source"], DEMO_SEED_VERSION, "demo-normalizer-v1", dataset_hash, DEMO_AT - timedelta(days=10), DEMO_AT, DEMO_AT))
        for _raw_id, normalized_id, _instrument_id, _event_at, _payload in historical_members:
            _insert(cursor, "INSERT INTO historical_dataset_members VALUES (%s,%s) ON CONFLICT DO NOTHING", (ids["historical-dataset"], normalized_id))
        _insert(cursor, "INSERT INTO data_health_assessments VALUES (%s,%s,'GLOBAL','*',%s,%s,%s,%s,'INFO',FALSE,%s,%s::jsonb) ON CONFLICT (assessment_id) DO NOTHING", (ids["health"], ids["historical-dataset"], DEMO_SEED_VERSION, DEMO_AT, DEMO_AT - timedelta(days=10), DEMO_AT, digest({"health": DEMO_SOURCE}), json.dumps({"classification": DEMO_SOURCE, "state": "HEALTHY"})))
        _insert(cursor, "INSERT INTO strategy_definitions VALUES (%s,'TREND','Trend V2 synthetic research hypothesis; no live authority.',%s) ON CONFLICT (strategy_id) DO NOTHING", (ids["strategy"], DEMO_AT))
        _insert(cursor, "INSERT INTO strategy_versions VALUES (%s,%s,'trend-v2',%s::jsonb,'demo-cost-model-v1','demo-capacity-v1',%s::jsonb,%s) ON CONFLICT (strategy_version_id) DO NOTHING", (ids["strategy-version"], ids["strategy"], json.dumps([f"{name}:1.0.0" for name in ("demo_return", "demo_trend", "demo_momentum", "demo_volatility")]), json.dumps(strategy_contract), DEMO_AT))
        _insert(cursor, "INSERT INTO research_experiments VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s) ON CONFLICT (experiment_id) DO NOTHING", (ids["experiment"], ids["strategy-version"], ids["dataset-version"], json.dumps({"scenario": DEMO_SEED_VERSION}), json.dumps({"total_return": "0.03", "classification": DEMO_SOURCE}), digest({"experiment": DEMO_SEED_VERSION}), DEMO_AT))
        _insert(cursor, "INSERT INTO validation_packages(package_id,strategy_version_id,dataset_version_id,cost_model_version,content_hash,status,created_at,limitations,feature_versions,integrity_status) VALUES (%s,%s,%s,'demo-cost-model-v1',%s,'REVIEW_REQUIRED_OR_BLOCKED',%s,%s::jsonb,%s::jsonb,'LEGACY_UNVERIFIABLE') ON CONFLICT (package_id) DO NOTHING", (ids["validation-package"], ids["strategy-version"], ids["dataset-version"], digest({"validation": DEMO_SEED_VERSION}), DEMO_AT, json.dumps(["Synthetic engineering evidence only."]), json.dumps(["demo_return:1.0.0", "demo_trend:1.0.0", "demo_momentum:1.0.0", "demo_volatility:1.0.0"])))
        _insert(cursor, "INSERT INTO regime_model_versions VALUES (%s,%s,'cycle-204-demo','demo-regime-v1','RESEARCH_ONLY',%s,%s::jsonb,%s::jsonb,%s,%s) ON CONFLICT (model_version_id) DO NOTHING", (ids["regime-model"], stable_id("regime-model"), DEMO_AT, json.dumps([str(value) for value in feature_ids]), json.dumps({"classification": DEMO_SOURCE}), DEMO_AT, digest({"regime-model": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO regime_runs VALUES (%s,%s,%s,%s,%s,'DEMO:XNAS:DEMO_EQ_A',%s,'REVIEW_REQUIRED',%s::jsonb,%s::jsonb,%s) ON CONFLICT (run_id) DO NOTHING", (ids["regime-run"], ids["regime-model"], ids["historical-dataset"], DEMO_SEED_VERSION, dataset_hash, DEMO_AT, json.dumps(["Synthetic regime classification."]), json.dumps({"classification": DEMO_SOURCE}), digest({"regime-run": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO regime_observations VALUES (%s,%s,'RULE_BASED','trend','MEASURED','UPTREND',%s::jsonb,0.2,%s::jsonb,%s) ON CONFLICT (observation_id) DO NOTHING", (ids["regime-observation"], DEMO_AT, json.dumps({"UPTREND": "0.8", "DOWNTREND": "0.2"}), json.dumps([]), digest({"regime-observation": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO regime_run_observations VALUES (%s,%s,0) ON CONFLICT DO NOTHING", (ids["regime-run"], ids["regime-observation"]))
        _insert(cursor, "INSERT INTO regime_risk_adjustment_candidates VALUES (%s,%s,%s,1,0.75,1,'REDUCE','REVIEW_REQUIRED',%s::jsonb,FALSE,%s,%s) ON CONFLICT (candidate_id) DO NOTHING", (ids["regime-candidate"], ids["regime-run"], ids["strategy-version"], json.dumps(["Synthetic volatility reduction"]), DEMO_AT, digest({"regime-candidate": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO portfolio_construction_policy_versions VALUES (%s,%s,'cycle-205-demo','demo-portfolio-v1','RESEARCH_ONLY',%s::jsonb,FALSE,%s,%s) ON CONFLICT (policy_version_id) DO NOTHING", (ids["portfolio-policy"], stable_id("portfolio-policy"), json.dumps({"target_volatility": "0.1", "classification": DEMO_SOURCE}), DEMO_AT, digest({"portfolio-policy": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO portfolio_construction_runs VALUES (%s,%s,%s,%s,%s,'REVIEW_REQUIRED',100000,%s::jsonb,%s::jsonb,%s) ON CONFLICT (run_id) DO NOTHING", (ids["portfolio-run"], ids["portfolio-policy"], ids["regime-run"], digest({"regime-run": DEMO_SEED_VERSION}), DEMO_AT, json.dumps(["Synthetic review-only construction"]), json.dumps({"classification": DEMO_SOURCE}), digest({"portfolio-run": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO portfolio_covariance_estimates VALUES (%s,%s,%s,%s,%s,'demo-covariance-v1',3,%s,%s::jsonb,%s::jsonb,0.1,0.2,%s) ON CONFLICT (covariance_id) DO NOTHING", (ids["portfolio-covariance"], ids["portfolio-run"], ids["historical-dataset"], DEMO_SEED_VERSION, dataset_hash, DEMO_AT, json.dumps([["1"]]), json.dumps([["1"]]), digest({"portfolio-covariance": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO portfolio_target_candidates VALUES (%s,%s,'REVIEW_REQUIRED',FALSE,0.55,0.45,0.45,0.08,0.1,%s,%s) ON CONFLICT (candidate_id) DO NOTHING", (ids["portfolio-target"], ids["portfolio-run"], DEMO_AT, digest({"portfolio-target": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO portfolio_constraint_evaluations VALUES (%s,%s,'max_single_sleeve','REDUCED',0.6,0.45,%s::jsonb,%s) ON CONFLICT (constraint_id) DO NOTHING", (ids["portfolio-constraint"], ids["portfolio-run"], json.dumps(["reduced_to_review_limit"]), digest({"portfolio-constraint": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO portfolio_risk_gate_evidence VALUES (%s,TRUE,45000,45000,-5000,%s::jsonb,%s::jsonb,FALSE,%s) ON CONFLICT (run_id) DO NOTHING", (ids["portfolio-run"], json.dumps(["review_only"]), json.dumps({"classification": DEMO_SOURCE}), digest({"portfolio-risk": DEMO_SEED_VERSION})))
    return ids


def _seed_scorecard(database: PostgresDatabase, ids: dict[str, UUID]) -> None:
    from trade_platform.strategy_scorecard_v2 import (
        EvidenceState,
        MetricFamily,
        MetricObservation,
        PostgresStrategyScorecardStore,
        ScorecardStatus,
        ScoreComponentV2,
        StrategyScorecardV2,
    )

    scorecard = StrategyScorecardV2("cycle-201-demo", ids["strategy"], "trend-v2", ids["experiment"], DEMO_SEED_VERSION, ("demo_return:1.0.0", "demo_trend:1.0.0", "demo_momentum:1.0.0", "demo_volatility:1.0.0"), "demo-cost-model-v1", DEMO_AT, DEMO_AT - timedelta(hours=1), ScorecardStatus.REVIEW_REQUIRED, ("Synthetic engineering evidence only.",), (MetricObservation(MetricFamily.PERFORMANCE, "total_return", EvidenceState.MEASURED, Decimal("0.03"), "fraction"),), (ScoreComponentV2("complexity_penalty", "demo-v1", Decimal("1"), "Transparent synthetic diagnostic."),), "HEALTHY", (ids["health"],), {"classification": digest({"classification": DEMO_SOURCE})})
    if scorecard.scorecard_id != ids["scorecard"]:
        # Keep all cross-domain UUIDs deterministic while retaining the scorecard's
        # existing content-addressed identity invariant.
        ids["scorecard"] = scorecard.scorecard_id
    PostgresStrategyScorecardStore(database).publish(scorecard)


def _seed_investment_paper_news_sre(database: PostgresDatabase, core: dict[str, UUID], ids: dict[str, UUID]) -> None:
    thesis_id, investment_policy = stable_id("investment-thesis"), stable_id("investment-policy")
    news_source, news_initial, news_retraction = stable_id("news-source"), stable_id("news-initial"), stable_id("news-retraction")
    service, slo, alert_policy, alert, incident = stable_id("sre-service"), stable_id("sre-slo"), stable_id("sre-alert-policy"), stable_id("sre-alert"), stable_id("sre-incident")
    with database.transaction() as connection, connection.cursor() as cursor:
        _insert(cursor, "INSERT INTO portfolio_sleeve_inputs VALUES (%s,%s,0,%s,%s,%s,%s,'trend-v2',%s::jsonb,%s) ON CONFLICT (sleeve_input_id) DO NOTHING", (ids["portfolio-sleeve"], ids["portfolio-run"], ids["strategy-version"], ids["scorecard"], ids["validation-package"], ids["regime-candidate"], json.dumps({"requested_allocation": "0.6", "review_allocation": "0.45", "adjustment_reasons": ["synthetic capacity cap"]}), digest({"portfolio-sleeve": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO portfolio_target_sleeve_weights VALUES (%s,%s,0.45,45000,0.1,0.1,%s::jsonb) ON CONFLICT DO NOTHING", (ids["portfolio-target"], ids["portfolio-sleeve"], json.dumps(["capacity_reduction"])))
        _insert(cursor, "INSERT INTO accounts VALUES ('demo-investment','INVESTMENT','USD',%s) ON CONFLICT (account_id) DO NOTHING", (DEMO_AT,))
        _insert(cursor, "INSERT INTO accounts VALUES ('demo-paper','PAPER','USD',%s) ON CONFLICT (account_id) DO NOTHING", (DEMO_AT,))
        _insert(cursor, "INSERT INTO investment_theses(thesis_id,instrument_id,version,status,created_at,thesis_schema_version,pit_instrument_id,knowledge_cutoff,contract,content_hash) VALUES (%s,%s,'v1','REVIEW_REQUIRED',%s,'demo-v1','DEMO:XNAS:DEMO_EQ_A',%s,%s::jsonb,%s) ON CONFLICT (thesis_id) DO NOTHING", (thesis_id, core["DEMO_EQ_A"], DEMO_AT, DEMO_AT, json.dumps({"quality": "synthetic", "bear": "demand falls", "base": "steady growth", "bull": "adoption", "catalysts": ["fictional launch"], "risks": ["fictional competition"], "invalidation": ["synthetic revenue decline"], "classification": DEMO_SOURCE}), digest({"thesis": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO investment_reviews VALUES (%s,%s,'demo-reviewer','REVIEW_REQUIRED',%s,%s::jsonb) ON CONFLICT (review_id) DO NOTHING", (stable_id("investment-review"), thesis_id, DEMO_AT, json.dumps({"classification": DEMO_SOURCE})))
        _insert(cursor, "INSERT INTO investment_policy_versions VALUES (%s,%s,'v1','demo-investment','LONG_TERM_INVESTMENT','USD',100000,0.4,0.1,0.3,'demo-committee',%s,%s::jsonb,%s) ON CONFLICT (policy_version_id) DO NOTHING", (investment_policy, stable_id("investment-policy-root"), DEMO_AT, json.dumps(["Synthetic review-only policy."]), digest({"investment-policy": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO investment_rebalance_candidates VALUES (%s,%s,'demo-investment',%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,0.1,'REVIEW_REQUIRED',%s::jsonb,%s::jsonb,FALSE,%s) ON CONFLICT (candidate_id) DO NOTHING", (stable_id("investment-candidate"), investment_policy, DEMO_AT, digest({"holdings": DEMO_SEED_VERSION}), json.dumps([]), json.dumps({"DEMO:XNAS:DEMO_EQ_A": "0.2"}), json.dumps({"DEMO:XNAS:DEMO_EQ_A": "0.3"}), json.dumps(["synthetic review"]), json.dumps(["No execution authority"]), digest({"investment-candidate": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO signals VALUES (%s,%s,%s,'RESEARCH_ONLY',%s,%s,%s::jsonb) ON CONFLICT (signal_id) DO NOTHING", (ids["signal"], ids["strategy-version"], core["DEMO_EQ_A"], DEMO_AT, DEMO_AT + timedelta(days=1), json.dumps({"classification": DEMO_SOURCE})))
        _insert(cursor, "INSERT INTO signals VALUES (%s,%s,%s,'BLOCKED',%s,%s,%s::jsonb) ON CONFLICT (signal_id) DO NOTHING", (ids["blocked-signal"], ids["strategy-version"], core["DEMO_EQ_B"], DEMO_AT, DEMO_AT + timedelta(days=1), json.dumps({"classification": DEMO_SOURCE})))
        signal_payload = {"direction": "BUY", "strength": "0.8", "confidence": "0.7", "data_quality_score": "1", "explanation": "Synthetic review signal", "contradicting_evidence": ["synthetic risk only"]}
        _insert(cursor, "INSERT INTO runtime_signal_proposals VALUES (%s,'DEMO:XNAS:DEMO_EQ_A','trend-v2',%s,%s,%s::jsonb) ON CONFLICT (signal_id) DO NOTHING", (ids["signal"], DEMO_AT - timedelta(hours=2), DEMO_AT + timedelta(days=1), json.dumps(signal_payload)))
        _insert(cursor, "INSERT INTO runtime_signal_validations VALUES (%s,%s,'VALIDATED','[\"data\",\"risk\"]'::jsonb,'[]'::jsonb,%s) ON CONFLICT (assessment_id) DO NOTHING", (stable_id("signal-validation"), ids["signal"], DEMO_AT - timedelta(hours=1)))
        _insert(cursor, "INSERT INTO runtime_signal_lifecycle_events (event_id,signal_id,from_status,to_status,actor,reason,evidence_references,occurred_at) VALUES (%s,%s,'CANDIDATE','VALIDATED','signal_validation','all_validation_stages_passed',%s::jsonb,%s) ON CONFLICT (event_id) DO NOTHING", (stable_id("signal-event"), ids["signal"], json.dumps([f"signal-validation:{stable_id('signal-validation')}"]), DEMO_AT - timedelta(hours=1)))
        _insert(cursor, "INSERT INTO risk_policies VALUES (%s,'demo-paper-risk',%s) ON CONFLICT (risk_policy_id) DO NOTHING", (ids["risk-policy"], DEMO_AT))
        _insert(cursor, "INSERT INTO risk_policy_versions VALUES (%s,%s,'v1',%s::jsonb,%s,%s) ON CONFLICT (risk_policy_version_id) DO NOTHING", (ids["risk-policy-version"], ids["risk-policy"], json.dumps({"classification": DEMO_SOURCE}), digest({"risk-policy": DEMO_SEED_VERSION}), DEMO_AT))
        _insert(cursor, "INSERT INTO paper_order_intents VALUES (%s,%s,'demo-paper',%s,'BUY',10,100,'PROPOSED',%s) ON CONFLICT (intent_id) DO NOTHING", (ids["paper-intent"], ids["signal"], core["DEMO_EQ_A"], DEMO_AT))
        _insert(cursor, "INSERT INTO risk_decisions VALUES (%s,%s,%s,TRUE,%s::jsonb,%s) ON CONFLICT (risk_decision_id) DO NOTHING", (stable_id("risk-approved"), ids["paper-intent"], ids["risk-policy-version"], json.dumps(["paper-only approved"]), DEMO_AT))
        _insert(cursor, "INSERT INTO risk_decisions VALUES (%s,%s,%s,FALSE,%s::jsonb,%s) ON CONFLICT (risk_decision_id) DO NOTHING", (stable_id("risk-blocked"), ids["blocked-intent"], ids["risk-policy-version"], json.dumps(["synthetic blocked risk decision"]), DEMO_AT))
        for sequence, event, payload in ((0, "ORDER_CREATED", {"status": "PROPOSED"}), (1, "ORDER_STATUS_CHANGED", {"to": "ACKNOWLEDGED"}), (2, "FILL_INGESTED", {"quantity": "4", "price": "100"}), (3, "FILL_INGESTED", {"quantity": "6", "price": "101"})):
            _insert(cursor, "INSERT INTO oms_events(oms_event_id,intent_id,event_type,occurred_at,payload) VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (oms_event_id) DO NOTHING", (stable_id(f"oms-event:{sequence}"), ids["paper-intent"], event, DEMO_AT + timedelta(minutes=sequence), json.dumps(payload)))
        _insert(cursor, "INSERT INTO fills VALUES (%s,'demo-fill-1',%s,%s,4,100) ON CONFLICT (fill_id) DO NOTHING", (stable_id("fill-1"), ids["paper-intent"], DEMO_AT + timedelta(minutes=2)))
        _insert(cursor, "INSERT INTO fills VALUES (%s,'demo-fill-2',%s,%s,6,101) ON CONFLICT (fill_id) DO NOTHING", (stable_id("fill-2"), ids["paper-intent"], DEMO_AT + timedelta(minutes=3)))
        _insert(cursor, "INSERT INTO reconciliations VALUES (%s,'demo-paper',%s,%s,TRUE,'[]'::jsonb) ON CONFLICT (reconciliation_id) DO NOTHING", (ids["paper-reconciliation"], DEMO_SOURCE, DEMO_AT + timedelta(minutes=4)))
        _insert(cursor, "INSERT INTO reconciled_account_evidence VALUES (%s,%s,'demo-paper',%s,%s,'USD',100000,100000,%s::jsonb,%s::jsonb,0,0,TRUE,%s) ON CONFLICT (evidence_id) DO NOTHING", (ids["reconciled-account"], ids["paper-reconciliation"], DEMO_SOURCE, DEMO_AT + timedelta(minutes=4), json.dumps([]), json.dumps([str(stable_id("fill-1")), str(stable_id("fill-2"))]), DEMO_AT + timedelta(minutes=4)))
        _insert(cursor, "INSERT INTO news_source_policy_versions VALUES (%s,%s,%s,'v1',%s,%s,'APPROVED',FALSE,TRUE,%s::jsonb,0.5,'CONFIGURATION_ONLY',FALSE,%s,%s) ON CONFLICT (source_policy_version_id) DO NOTHING", (news_source, stable_id("news-source-root"), DEMO_SOURCE, DEMO_SEED_VERSION, f"demo://{DEMO_SEED_VERSION}/news-terms", json.dumps(["en"]), DEMO_AT, digest({"news-source": DEMO_SEED_VERSION})))
        for document_id, revision, kind, headline, supersedes in ((news_initial, 0, "INITIAL", "Demo issuer publishes fictional guidance", None), (news_retraction, 1, "RETRACTION", "Demo issuer retracts fictional guidance", news_initial)):
            _insert(cursor, "INSERT INTO news_document_revisions VALUES (%s,%s,%s,'demo-guidance',%s,%s,%s,%s,'en',%s,%s,%s,%s,%s,%s,'OFFICIAL','[]'::jsonb,%s::jsonb,%s) ON CONFLICT (document_revision_id) DO NOTHING", (document_id, news_source, f"demo-guidance-{revision}", revision, kind, supersedes, f"demo://{DEMO_SEED_VERSION}/news/{revision}", headline, DEMO_AT, DEMO_AT + timedelta(minutes=revision), DEMO_AT + timedelta(minutes=revision), digest({"fingerprint": revision}), digest({"raw": revision}), json.dumps({"classification": DEMO_SOURCE}), digest({"news-document": revision})))
            extraction = stable_id(f"news-extraction:{revision}")
            link = stable_id(f"news-link:{revision}")
            assessment = stable_id(f"news-assessment:{revision}")
            _insert(cursor, "INSERT INTO news_document_entity_links VALUES (%s,%s,'DEMO:XNAS:DEMO_EQ_A','REVIEWED',1,FALSE,%s::jsonb,%s) ON CONFLICT (entity_link_id) DO NOTHING", (link, document_id, json.dumps({"classification": DEMO_SOURCE}), digest({"news-link": revision})))
            _insert(cursor, "INSERT INTO news_event_extractions VALUES (%s,%s,'GUIDANCE','demo-v1','demo-model-v1',0.5,0.5,0.5,'DAYS',%s,'[]'::jsonb,%s::jsonb,%s) ON CONFLICT (extraction_id) DO NOTHING", (extraction, document_id, DEMO_AT + timedelta(minutes=revision), json.dumps(["Synthetic extraction"]), digest({"news-extraction": revision})))
            state = "WITHDRAWN" if kind == "RETRACTION" else "REVIEW_REQUIRED"
            _insert(cursor, "INSERT INTO news_event_assessments VALUES (%s,%s,%s,%s,%s,%s,0.5,%s::jsonb,%s::jsonb,%s) ON CONFLICT (assessment_id) DO NOTHING", (assessment, document_id, extraction, link, DEMO_AT + timedelta(minutes=revision), state, json.dumps(["Synthetic evidence"]), json.dumps({"classification": DEMO_SOURCE}), digest({"news-assessment": revision})))
        _insert(cursor, "INSERT INTO news_event_lineage VALUES (%s,%s,'RETRACTS') ON CONFLICT DO NOTHING", (news_initial, news_retraction))
        _insert(cursor, "INSERT INTO sre_service_versions VALUES (%s,%s,'trade-platform-demo','module1b-demo','0000000000000000000000000000000000000000','LOCAL','EVIDENCE_ONLY',%s::jsonb,%s,%s) ON CONFLICT (service_version_id) DO NOTHING", (service, stable_id("sre-service-root"), json.dumps(["postgres"]), DEMO_AT, digest({"sre-service": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO sre_slo_policy_versions VALUES (%s,%s,'postgres-availability','healthy/total',0.99,3600,1,'CANDIDATE_ONLY',FALSE,%s,%s) ON CONFLICT (slo_policy_version_id) DO NOTHING", (slo, service, DEMO_AT, digest({"slo": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO sre_dependency_probes VALUES (%s,%s,'postgres','DEPENDENCY','HEALTHY',%s,5,NULL,'00000000000000000000000000000000',%s::jsonb,%s) ON CONFLICT (probe_id) DO NOTHING", (stable_id("sre-probe"), service, DEMO_AT, json.dumps({"classification": DEMO_SOURCE}), digest({"probe": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO sre_sli_windows VALUES (%s,%s,%s,%s,99,100,0.99,1,'MEASURED','ENGINEERING_EVIDENCE_ONLY',%s) ON CONFLICT (sli_window_id) DO NOTHING", (stable_id("sre-window"), slo, DEMO_AT - timedelta(hours=1), DEMO_AT, digest({"sli": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO sre_alert_policy_versions VALUES (%s,%s,'POSTGRES_RECOVERED','WARNING','demo-oncall','demo://runbook', 'demo',60,%s::jsonb,'EVIDENCE_ONLY',%s,%s) ON CONFLICT (alert_policy_version_id) DO NOTHING", (alert_policy, service, json.dumps({"classification": DEMO_SOURCE}), DEMO_AT, digest({"alert-policy": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO sre_alerts VALUES (%s,%s,%s,'postgres:demo',%s,'demo://incident','00000000000000000000000000000000',%s) ON CONFLICT (alert_id) DO NOTHING", (alert, alert_policy, digest({"alert": DEMO_SEED_VERSION}), DEMO_AT, digest({"alert-record": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO sre_alert_events VALUES (%s,%s,0,'RESOLVED','demo-oncall',%s,%s::jsonb,%s) ON CONFLICT (alert_event_id) DO NOTHING", (stable_id("sre-alert-event"), alert, DEMO_AT, json.dumps({"recovery": "synthetic"}), digest({"alert-event": DEMO_SEED_VERSION})))
        _insert(cursor, "INSERT INTO sre_incidents VALUES (%s,%s,'WARNING','demo-oncall','demo://runbook',%s,%s,'demo://post-incident','RESOLVED',%s::jsonb,%s) ON CONFLICT (incident_id) DO NOTHING", (incident, alert, DEMO_AT - timedelta(minutes=2), DEMO_AT, json.dumps({"classification": DEMO_SOURCE, "recovery": "complete"}), digest({"incident": DEMO_SEED_VERSION})))


def _claim_scenario(database: PostgresDatabase) -> None:
    """Fail early when this versioned deterministic scenario conflicts."""
    fingerprint = digest({"version": DEMO_SEED_VERSION, "source": DEMO_SOURCE, "scenario": "cross-domain-demo"})
    ledger_id = stable_id("scenario-ledger")
    with database.transaction() as connection, connection.cursor() as cursor:
        _insert(cursor, "INSERT INTO audit_events VALUES (%s,'demo-seeder','demo.scenario.seeded',%s,'demo_scenario',%s,%s::jsonb,%s) ON CONFLICT (audit_event_id) DO NOTHING", (ledger_id, DEMO_AT, DEMO_SEED_VERSION, json.dumps({"classification": DEMO_SOURCE, "fingerprint": fingerprint}), fingerprint))
        cursor.execute("SELECT content_hash FROM audit_events WHERE audit_event_id=%s", (ledger_id,))
        row = cursor.fetchone()
        if row is None or str(row[0]) != fingerprint:
            raise RuntimeError("demo_seed_conflicting_scenario_identity")


def seed_demo_evidence(dsn: str) -> dict[str, str]:
    _local_postgres_dsn(dsn)
    database = PostgresDatabase(dsn)
    try:
        _claim_scenario(database)
        core = _seed_instruments(database)
        ids = _seed_research_chain(database, core)
        _seed_scorecard(database, ids)
        _seed_investment_paper_news_sre(database, core, ids)
        return {"seed_version": DEMO_SEED_VERSION, "classification": DEMO_SOURCE, "strategy_id": str(ids["strategy"]), "experiment_id": str(ids["experiment"]), "scorecard_id": str(ids["scorecard"])}
    finally:
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed deterministic local PostgreSQL demo evidence.")
    parser.add_argument("--postgres-dsn", default=os.environ.get("POSTGRES_DSN"), required=os.environ.get("POSTGRES_DSN") is None)
    args = parser.parse_args()
    print(json.dumps(seed_demo_evidence(args.postgres_dsn), sort_keys=True))


if __name__ == "__main__":
    main()
