"""Phase 3D.9A: the preregistration packet fails closed and gates the holdout.

The packet must never invent an owner decision, must name every gap, must hash
deterministically, and must refuse to authorize a holdout read while anything is
outstanding. Capacity is explicitly NOT a gap.
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from trade_platform.crypto_basis_mean_reversion_v1 import CryptoBasisMeanReversionDefinitionV1
from trade_platform.crypto_liquidity_capacity_v1 import LiquidityCapacityPolicyV1
from trade_platform.open_to_open_preregistration_v1 import (
    DRAFT_DISPOSITION,
    METHODOLOGY_VERSION,
    STATUS_AUTHORIZED,
    STATUS_DRAFT,
    UNRESOLVED_ADVERSE_EXIT_SHOCKS,
    UNRESOLVED_BOOTSTRAP,
    UNRESOLVED_COST_MODEL,
    UNRESOLVED_FEATURE_DECISION_TIMES,
    UNRESOLVED_MISSING_EXIT_STRESS,
    UNRESOLVED_MONTE_CARLO,
    UNRESOLVED_NEIGHBOR_STEPS,
    UNRESOLVED_NULL_CONTROL,
    UNRESOLVED_REAL_DATA_PROVENANCE,
    UNRESOLVED_STRATEGY_DEFINITION,
    UNRESOLVED_WALK_FORWARD_PROTOCOL,
    OpenToOpenPreregistrationV1Error,
    build_open_to_open_preregistration_v1,
    pre_holdout_upper_bound,
    require_authorized_for_holdout,
)
from trade_platform.open_to_open_validation_orchestration_v1 import (
    OpenToOpenNeighborStepsV1,
    OpenToOpenWalkForwardProtocolV1,
    derive_open_to_open_evaluation_span_v1,
)
from trade_platform.real_market_data_provenance_v1 import (
    DatasetLineageFactsV1,
    PersistedSourceFactsV1,
    canonical_bybit_source_contract_v1,
    evaluate_real_market_data_provenance_v1,
)
from trade_platform.research import CostModel

CONTRACT = canonical_bybit_source_contract_v1()
DATASET_ID = uuid4()
DATASET_HASH = "c" * 64
EVALUATION_START = datetime(2026, 4, 22, tzinfo=UTC)
CREATED_AT = datetime(2026, 9, 22, tzinfo=UTC)


def _source(**overrides: Any) -> PersistedSourceFactsV1:
    values: dict[str, Any] = {
        "source_id": CONTRACT.source_id, "provider": CONTRACT.provider,
        "dataset_name": CONTRACT.dataset_name,
        "provider_identifier_namespace": CONTRACT.provider_identifier_namespace,
        "provider_terms_version": CONTRACT.provider_terms_version,
        "authorization_reference": CONTRACT.authorization_reference,
        "asset_scope": CONTRACT.asset_scope, "observation_kinds": CONTRACT.observation_kinds,
    }
    values.update(overrides)
    return PersistedSourceFactsV1(**values)


def _facts(**overrides: Any) -> DatasetLineageFactsV1:
    values: dict[str, Any] = {
        "dataset_version_id": DATASET_ID, "found": True, "status": "SEALED",
        "version": "bybit-research-composite-v1:test", "normalization_version": "v1",
        "content_hash": DATASET_HASH, "source_id": CONTRACT.source_id,
        "valid_from": EVALUATION_START,
        "valid_until": datetime(2026, 9, 18, 23, 59, tzinfo=UTC),
        "created_at": datetime(2026, 9, 20, tzinfo=UTC), "source": _source(),
        "member_count": 10, "lineage_complete_member_count": 10,
        "instrument_ids": ("CRYPTO:BYBIT:BTCUSDT:PERP",),
        "member_count_by_kind": (("OHLCV", 10),),
    }
    values.update(overrides)
    return DatasetLineageFactsV1(**values)


class _Bar:
    """The two fields ``derive_open_to_open_evaluation_span_v1`` reads."""

    def __init__(self, open_at: datetime) -> None:
        self.bar_open_at = open_at
        self.bar_close_at = open_at + timedelta(minutes=1)


class _BarSeries:
    def __init__(self, first_open: datetime, last_open: datetime) -> None:
        self.bars = (_Bar(first_open), _Bar(last_open))

    def validate(self) -> None:
        return None


def _span(days: int = 150) -> Any:
    last_open = EVALUATION_START + timedelta(days=days) - timedelta(minutes=1)
    return derive_open_to_open_evaluation_span_v1(
        bar_series=_BarSeries(EVALUATION_START, last_open)  # type: ignore[arg-type]
    )


def _packet(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "dataset_version_id": DATASET_ID,
        "dataset_content_hash": DATASET_HASH,
        "evaluation_span": _span(),
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return build_open_to_open_preregistration_v1(**values)


def _authorized_inputs() -> dict[str, Any]:
    return {
        "market_data_provenance": evaluate_real_market_data_provenance_v1(_facts()),
        "baseline_definition": CryptoBasisMeanReversionDefinitionV1(
            basis_entry_threshold=Decimal("0.0006"),
            holding_horizon_bars=30,
            maximum_absolute_exposure=Decimal("0.5"),
        ),
        "cost_model": CostModel(
            Decimal("0"), Decimal("0.00055"), Decimal("0.00005")
        ),
        "cost_model_version": "owner-authorized-cost-model-v1",
        "protocol": OpenToOpenWalkForwardProtocolV1(
            initial_train_days=60, validation_days=15, test_days=15, step_days=15
        ),
        "neighbor_steps": OpenToOpenNeighborStepsV1(
            basis_threshold_step=Decimal("0.00005"),
            holding_horizon_step_bars=5,
            exposure_step=Decimal("0.1"),
        ),
        "bootstrap_seed": 11, "bootstrap_resamples": 2_000,
        "monte_carlo_seed": 12, "monte_carlo_simulations": 2_000,
        "null_seed": 13,
        "adverse_exit_shock_magnitudes": (Decimal("0.001"), Decimal("0.002")),
        "missing_exit_stress_bar_open_times": (datetime(2026, 5, 1, tzinfo=UTC),),
        "distinct_feature_decision_at_count": 216_000,
    }


class PreregistrationDraftTests(unittest.TestCase):
    def test_empty_packet_is_draft_and_names_every_owner_gap(self) -> None:
        packet = _packet()
        self.assertEqual(packet.status, STATUS_DRAFT)
        self.assertEqual(packet.disposition, DRAFT_DISPOSITION)
        self.assertFalse(packet.authorized_for_holdout)
        for reason in (
            UNRESOLVED_REAL_DATA_PROVENANCE, UNRESOLVED_STRATEGY_DEFINITION,
            UNRESOLVED_COST_MODEL, UNRESOLVED_WALK_FORWARD_PROTOCOL,
            UNRESOLVED_NEIGHBOR_STEPS, UNRESOLVED_BOOTSTRAP, UNRESOLVED_MONTE_CARLO,
            UNRESOLVED_NULL_CONTROL, UNRESOLVED_ADVERSE_EXIT_SHOCKS,
            UNRESOLVED_MISSING_EXIT_STRESS, UNRESOLVED_FEATURE_DECISION_TIMES,
        ):
            self.assertIn(reason, packet.unresolved_reasons)

    def test_nothing_is_defaulted_into_existence(self) -> None:
        packet = _packet()
        for field_name in (
            "baseline_definition_content_hash", "basis_entry_threshold",
            "holding_horizon_bars", "maximum_absolute_exposure",
            "cost_model_content_hash", "cost_model_version", "protocol_content_hash",
            "neighbor_steps_content_hash", "bootstrap_seed", "bootstrap_resamples",
            "monte_carlo_seed", "monte_carlo_simulations", "null_seed",
            "capacity_policy_content_hash",
        ):
            self.assertIsNone(getattr(packet, field_name), field_name)
        self.assertEqual(packet.adverse_exit_shock_magnitudes, ())
        self.assertEqual(packet.missing_exit_stress_bar_open_times, ())

    def test_span_is_bound_mechanically(self) -> None:
        packet = _packet()
        self.assertEqual(packet.evaluation_start, EVALUATION_START)
        self.assertEqual(packet.evaluation_end, datetime(2026, 9, 19, tzinfo=UTC))
        self.assertEqual(packet.holdout_start, datetime(2026, 8, 20, tzinfo=UTC))
        self.assertEqual(packet.pre_holdout_complete_days, 120)
        self.assertEqual(packet.holdout_complete_days, 30)
        self.assertEqual(pre_holdout_upper_bound(packet), packet.holdout_start)

    def test_absent_capacity_policy_is_not_an_unresolved_reason(self) -> None:
        self.assertNotIn(
            "MISSING_AUTHORIZED_CAPACITY_POLICY", _packet().unresolved_reasons
        )
        self.assertFalse(
            any("CAPACITY" in reason for reason in _packet().unresolved_reasons)
        )

    def test_supplied_capacity_policy_is_bound_without_changing_authorization(self) -> None:
        policy = LiquidityCapacityPolicyV1(
            policy_version="owner-policy-v1", lookback_complete_days=20,
            minimum_complete_days=5, maximum_participation=Decimal("0.01"),
            reduced_liquidity_multipliers=(Decimal("0.5"), Decimal("1")),
        )
        packet = _packet(**_authorized_inputs(), capacity_policy=policy)
        self.assertEqual(packet.capacity_policy_content_hash, policy.content_hash())
        self.assertEqual(packet.status, STATUS_AUTHORIZED)


class PreregistrationIdentityTests(unittest.TestCase):
    def test_identical_inputs_hash_identically(self) -> None:
        self.assertEqual(_packet().content_hash, _packet().content_hash)
        self.assertEqual(_packet().preregistration_id, _packet().preregistration_id)

    def test_every_owner_input_is_identity_significant(self) -> None:
        baseline = _packet(**_authorized_inputs())
        for override in (
            {"bootstrap_seed": 999}, {"monte_carlo_simulations": 1_000},
            {"null_seed": 999}, {"cost_model_version": "other-v1"},
            {"adverse_exit_shock_magnitudes": (Decimal("0.003"),)},
        ):
            inputs = _authorized_inputs()
            inputs.update(override)
            self.assertNotEqual(
                _packet(**inputs).content_hash, baseline.content_hash, override
            )

    def test_a_different_strategy_parameter_is_a_different_packet(self) -> None:
        inputs = _authorized_inputs()
        baseline = _packet(**inputs)
        inputs["baseline_definition"] = replace(
            inputs["baseline_definition"], holding_horizon_bars=31
        )
        self.assertNotEqual(_packet(**inputs).content_hash, baseline.content_hash)


class PreregistrationProvenanceTests(unittest.TestCase):
    def test_provenance_for_another_dataset_is_rejected(self) -> None:
        verdict = evaluate_real_market_data_provenance_v1(_facts(dataset_version_id=uuid4()))
        with self.assertRaises(OpenToOpenPreregistrationV1Error):
            _packet(market_data_provenance=verdict)

    def test_real_provenance_must_match_the_bound_dataset_content_hash(self) -> None:
        verdict = evaluate_real_market_data_provenance_v1(_facts())
        self.assertTrue(verdict.is_proven_real())
        with self.assertRaises(OpenToOpenPreregistrationV1Error):
            _packet(market_data_provenance=verdict, dataset_content_hash="d" * 64)

    def test_unproven_provenance_keeps_the_packet_draft(self) -> None:
        verdict = evaluate_real_market_data_provenance_v1(_facts(status="PENDING"))
        self.assertFalse(verdict.is_proven_real())
        packet = _packet(**{**_authorized_inputs(), "market_data_provenance": verdict})
        self.assertEqual(packet.status, STATUS_DRAFT)
        self.assertIn(UNRESOLVED_REAL_DATA_PROVENANCE, packet.unresolved_reasons)
        self.assertFalse(packet.provenance_proven_real)


class PreregistrationHoldoutGuardTests(unittest.TestCase):
    def test_draft_packet_cannot_open_the_holdout(self) -> None:
        with self.assertRaises(OpenToOpenPreregistrationV1Error) as caught:
            require_authorized_for_holdout(_packet())
        self.assertIn(UNRESOLVED_STRATEGY_DEFINITION, str(caught.exception))

    def test_fully_authorized_packet_passes_the_guard(self) -> None:
        packet = _packet(**_authorized_inputs())
        self.assertEqual(packet.status, STATUS_AUTHORIZED)
        self.assertEqual(packet.unresolved_reasons, ())
        self.assertIsNone(require_authorized_for_holdout(packet))

    def test_collapsed_feature_decision_times_block_a_vacuous_run(self) -> None:
        inputs = _authorized_inputs()
        inputs["distinct_feature_decision_at_count"] = 1
        packet = _packet(**inputs)
        self.assertIn(UNRESOLVED_FEATURE_DECISION_TIMES, packet.unresolved_reasons)
        with self.assertRaises(OpenToOpenPreregistrationV1Error):
            require_authorized_for_holdout(packet)

    def test_a_foreign_methodology_version_cannot_pass_the_guard(self) -> None:
        packet = _packet(**_authorized_inputs())
        forged = replace(packet, methodology_version="something-else-v9")
        self.assertNotEqual(forged.methodology_version, METHODOLOGY_VERSION)
        with self.assertRaises(OpenToOpenPreregistrationV1Error):
            require_authorized_for_holdout(forged)

    def test_short_span_without_usable_holdout_stays_draft(self) -> None:
        inputs = _authorized_inputs()
        # A protocol that still fits the shorter pre-holdout span, so the only
        # thing left to object to is the span itself.
        inputs["protocol"] = OpenToOpenWalkForwardProtocolV1(
            initial_train_days=30, validation_days=10, test_days=10, step_days=10
        )
        packet = _packet(**inputs, evaluation_span=_span(days=100))
        self.assertEqual(packet.pre_holdout_complete_days, 80)
        self.assertEqual(packet.unresolved_reasons, ("EVALUATION_SPAN_HOLDOUT_NOT_AVAILABLE",))
        self.assertEqual(packet.status, STATUS_DRAFT)


if __name__ == "__main__":
    unittest.main()
