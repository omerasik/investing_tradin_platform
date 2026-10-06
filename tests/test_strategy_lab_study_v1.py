from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from trade_platform.evidence_tier_authority_v1 import EvidenceTierV1
from trade_platform.strategy_lab_study_v1 import (
    COST_POLICY_UNSET_V1,
    NUMERIC_POLICY_UNSET_V1,
    REASON_COST_POLICY_UNSET,
    REASON_NON_PROFESSIONAL_TIER,
    REASON_NUMERIC_POLICY_UNSET,
    UNTOUCHED_HOLDOUT_BOUNDARY_V1,
    DatasetBindingV1,
    InputRoleV1,
    ParameterDomainV1,
    ParameterKindV1,
    ParameterSpaceV1,
    SearchModeV1,
    SearchPlanV1,
    StrategyLabStudyError,
    StrategySpecV1,
    StudyAuthorityStatusV1,
    StudySpecV1,
    canonical_identity_json_v1,
    seeded_sample_indices_v1,
)
from trade_platform.tardis_capture_engineering_pilot_v1 import (
    UNTOUCHED_HOLDOUT_BOUNDARY_V1 as PILOT_HOLDOUT_BOUNDARY,
)

BOUND = datetime(2026, 8, 1, tzinfo=UTC)
IMPL = "a" * 64
DATA_HASH = "b" * 64


def _space() -> ParameterSpaceV1:
    return ParameterSpaceV1.of(
        ParameterDomainV1.integer_range("slow", start=20, stop_inclusive=40, step=10),
        ParameterDomainV1.integer_range("fast", start=2, stop_inclusive=10, step=4),
        ParameterDomainV1.decimal_set("threshold", [Decimal("0.001"), "0.0025"]),
    )


def _strategy(**overrides: object) -> StrategySpecV1:
    values: dict[str, object] = {
        "family": "sma_cross",
        "version": "1.0.0",
        "implementation_sha256": IMPL,
        "execution_convention": "next_bar_open",
        "input_roles": (InputRoleV1("bars", frozenset({EvidenceTierV1.T1_RETROSPECTIVE, EvidenceTierV1.T4_FIRST_PARTY_CAPTURE})),),
        "parameter_schema": {
            "slow": ParameterKindV1.INTEGER,
            "fast": ParameterKindV1.INTEGER,
            "threshold": ParameterKindV1.DECIMAL,
        },
    }
    values.update(overrides)
    return StrategySpecV1(**values)  # type: ignore[arg-type]


def _binding(**overrides: object) -> DatasetBindingV1:
    values: dict[str, object] = {
        "role": "bars",
        "dataset_version_id": UUID("f07ebfc1-19ce-4757-af4b-10525f6a6992"),
        "content_hash": DATA_HASH,
        "evidence_tier": EvidenceTierV1.T1_RETROSPECTIVE,
        "knowledge_upper_bound_exclusive": BOUND - timedelta(days=1),
    }
    values.update(overrides)
    return DatasetBindingV1(**values)  # type: ignore[arg-type]


def _study(**overrides: object) -> StudySpecV1:
    values: dict[str, object] = {
        "strategy": _strategy(),
        "parameter_space": _space(),
        "datasets": (_binding(),),
        "search": SearchPlanV1(SearchModeV1.EXHAUSTIVE, max_trials=18),
        "evaluation_upper_bound_exclusive": BOUND,
    }
    values.update(overrides)
    return StudySpecV1(**values)  # type: ignore[arg-type]


class ParameterDomainTests(unittest.TestCase):
    def test_decimal_values_are_exact_canonical_text(self) -> None:
        domain = ParameterDomainV1.decimal_set("x", [Decimal("1.50"), "0.0001", 3, Decimal("-0")])
        self.assertEqual(domain.values, ("1.5", "0.0001", "3", "0"))
        self.assertEqual(domain.typed("1.5"), Decimal("1.5"))

    def test_float_is_refused_everywhere(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1.decimal_set("x", [0.1])  # type: ignore[list-item]
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1.integer_range("x", start=1, stop_inclusive=2.0, step=1)  # type: ignore[arg-type]
        with self.assertRaises(StrategyLabStudyError):
            canonical_identity_json_v1({"value": 0.1})

    def test_two_spellings_of_one_number_are_a_duplicate(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1.decimal_set("x", ["1.5", "1.50"])

    def test_non_finite_decimal_is_refused(self) -> None:
        for bad in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaises(StrategyLabStudyError):
                ParameterDomainV1.decimal_set("x", [bad])

    def test_direct_construction_must_already_be_canonical(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1("x", ParameterKindV1.INTEGER, ("05",))
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1("x", ParameterKindV1.DECIMAL, ("1.50",))
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1("x", ParameterKindV1.CATEGORICAL, (" a",))

    def test_empty_and_invalid_ranges_are_refused(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1.categorical_set("x", [])
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1.integer_range("x", start=5, stop_inclusive=1, step=1)
        with self.assertRaises(StrategyLabStudyError):
            ParameterDomainV1.integer_range("x", start=1, stop_inclusive=5, step=0)


class ParameterSpaceTests(unittest.TestCase):
    def test_mixed_radix_addressing_round_trips_every_point(self) -> None:
        space = _space()
        self.assertEqual(space.cardinality, 3 * 3 * 2)
        points = [space.point_at(i) for i in range(space.cardinality)]
        self.assertEqual(len({tuple(sorted(p.items())) for p in points}), space.cardinality)
        for index, point in enumerate(points):
            self.assertEqual(space.index_of(point), index)
        self.assertEqual(points[0], {"fast": "2", "slow": "20", "threshold": "0.001"})
        self.assertEqual(points[1], {"fast": "2", "slow": "20", "threshold": "0.0025"})

    def test_names_must_be_sorted_and_unique(self) -> None:
        a = ParameterDomainV1.categorical_set("a", ["x"])
        b = ParameterDomainV1.categorical_set("b", ["y"])
        with self.assertRaises(StrategyLabStudyError):
            ParameterSpaceV1((b, a))
        with self.assertRaises(StrategyLabStudyError):
            ParameterSpaceV1.of(a, a)

    def test_points_outside_the_space_are_refused(self) -> None:
        space = _space()
        with self.assertRaises(StrategyLabStudyError):
            space.index_of({"fast": "3", "slow": "20", "threshold": "0.001"})
        with self.assertRaises(StrategyLabStudyError):
            space.index_of({"fast": "2", "slow": "20"})
        with self.assertRaises(StrategyLabStudyError):
            space.point_at(space.cardinality)


class SeededSampleTests(unittest.TestCase):
    def test_sample_is_deterministic_distinct_and_seed_dependent(self) -> None:
        first = seeded_sample_indices_v1(seed=7, population=1000, count=50)
        self.assertEqual(first, seeded_sample_indices_v1(seed=7, population=1000, count=50))
        self.assertEqual(len(set(first)), 50)
        self.assertEqual(list(first), sorted(first))
        self.assertNotEqual(first, seeded_sample_indices_v1(seed=8, population=1000, count=50))

    def test_sample_is_pinned_to_sha256_not_a_library_prng(self) -> None:
        # Independent formulation of the rule, plus a pinned value: changing the
        # ranking rule would silently change every sampled study's trial set.
        def rank(i: int) -> bytes:
            return hashlib.sha256(f"0:{i}".encode()).digest()

        expected = tuple(sorted(sorted(range(10), key=rank)[:3]))
        self.assertEqual(seeded_sample_indices_v1(seed=0, population=10, count=3), expected)
        self.assertEqual(expected, (4, 7, 9))


class StudyTests(unittest.TestCase):
    def test_identity_is_deterministic_and_label_is_not_identity(self) -> None:
        first = _study(label="first")
        second = _study(label="second")
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(first.study_id, second.study_id)
        self.assertEqual(first, second)

    def test_any_identity_change_changes_the_study_id(self) -> None:
        base = _study().study_id
        variants = [
            _study(strategy=_strategy(implementation_sha256="c" * 64)),
            _study(strategy=_strategy(execution_convention="same_bar_close")),
            _study(datasets=(_binding(content_hash="d" * 64),)),
            _study(search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, max_trials=19)),
            _study(evaluation_upper_bound_exclusive=BOUND - timedelta(hours=1)),
        ]
        self.assertEqual(len({base, *(v.study_id for v in variants)}), 1 + len(variants))

    def test_timezone_spelling_does_not_change_identity(self) -> None:
        plus_two = timezone(timedelta(hours=2))
        self.assertEqual(
            _study().content_hash,
            _study(evaluation_upper_bound_exclusive=BOUND.astimezone(plus_two)).content_hash,
        )

    def test_exhaustive_trials_cover_the_space_once_with_stable_ids(self) -> None:
        study = _study()
        trials = list(study.trials())
        self.assertEqual(len(trials), 18)
        self.assertEqual(study.planned_trial_count, 18)
        self.assertEqual([t.ordinal for t in trials], list(range(18)))
        self.assertEqual(len({t.trial_id for t in trials}), 18)
        again = {t.trial_id for t in _study(label="resubmitted").trials()}
        self.assertEqual({t.trial_id for t in trials}, again)
        self.assertTrue(all(t.study_id == study.study_id for t in trials))

    def test_trial_identity_binds_the_study(self) -> None:
        point = {"fast": "2", "slow": "20", "threshold": "0.001"}
        a = _study().trial_for(point)
        b = _study(datasets=(_binding(content_hash="d" * 64),)).trial_for(point)
        self.assertNotEqual(a.trial_id, b.trial_id)

    def test_seeded_sample_plans_exactly_the_budget(self) -> None:
        study = _study(search=SearchPlanV1(SearchModeV1.SEEDED_SAMPLE, max_trials=5, seed=11))
        trials = list(study.trials())
        self.assertEqual(len(trials), 5)
        self.assertEqual(study.planned_trial_count, 5)
        self.assertEqual(trials, list(_study(search=SearchPlanV1(SearchModeV1.SEEDED_SAMPLE, max_trials=5, seed=11)).trials()))

    def test_grid_is_never_silently_truncated(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            _study(search=SearchPlanV1(SearchModeV1.EXHAUSTIVE, max_trials=17))
        with self.assertRaises(StrategyLabStudyError):
            _study(search=SearchPlanV1(SearchModeV1.SEEDED_SAMPLE, max_trials=18, seed=1))

    def test_search_plan_requires_explicit_budget_and_seed_rules(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            SearchPlanV1(SearchModeV1.EXHAUSTIVE, max_trials=0)
        with self.assertRaises(StrategyLabStudyError):
            SearchPlanV1(SearchModeV1.EXHAUSTIVE, max_trials=5, seed=1)
        with self.assertRaises(StrategyLabStudyError):
            SearchPlanV1(SearchModeV1.SEEDED_SAMPLE, max_trials=5)

    def test_holdout_is_a_hard_upper_bound(self) -> None:
        self.assertEqual(UNTOUCHED_HOLDOUT_BOUNDARY_V1, PILOT_HOLDOUT_BOUNDARY)
        _study(evaluation_upper_bound_exclusive=UNTOUCHED_HOLDOUT_BOUNDARY_V1)
        with self.assertRaises(StrategyLabStudyError):
            _study(evaluation_upper_bound_exclusive=UNTOUCHED_HOLDOUT_BOUNDARY_V1 + timedelta(microseconds=1))

    def test_dataset_knowledge_may_not_extend_past_the_evaluation_bound(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            _study(datasets=(_binding(knowledge_upper_bound_exclusive=BOUND + timedelta(seconds=1)),))

    def test_dataset_roles_and_tiers_must_match_the_strategy(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            _study(datasets=(_binding(evidence_tier=EvidenceTierV1.T2_EVENT_TIME),))
        with self.assertRaises(StrategyLabStudyError):
            _study(datasets=(_binding(role="quotes"),))
        with self.assertRaises(StrategyLabStudyError):
            _study(datasets=())

    def test_parameter_space_must_match_the_strategy_schema(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            _study(strategy=_strategy(parameter_schema={"fast": ParameterKindV1.INTEGER}))
        with self.assertRaises(StrategyLabStudyError):
            _study(strategy=_strategy(parameter_schema={
                "slow": ParameterKindV1.INTEGER, "fast": ParameterKindV1.INTEGER, "threshold": ParameterKindV1.CATEGORICAL,
            }))

    def test_unresolved_owner_policies_cannot_be_filled_in(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            _study(numeric_policy_slot="FLOAT64_SEARCH_DECIMAL_AUTHORITY")
        with self.assertRaises(StrategyLabStudyError):
            _study(cost_policy_slot="TAKER_FEE_5_5_BPS")
        identity = _study().identity()
        self.assertEqual(identity["numeric_policy_slot"], NUMERIC_POLICY_UNSET_V1)
        self.assertEqual(identity["cost_policy_slot"], COST_POLICY_UNSET_V1)

    def test_every_v1_study_is_non_authoritative_and_says_why(self) -> None:
        authority = _study().authority()
        self.assertIs(authority.status, StudyAuthorityStatusV1.NON_AUTHORITATIVE)
        self.assertFalse(authority.promotable)
        self.assertEqual(
            authority.reasons,
            (REASON_NUMERIC_POLICY_UNSET, REASON_COST_POLICY_UNSET, REASON_NON_PROFESSIONAL_TIER),
        )
        professional = _study(datasets=(_binding(evidence_tier=EvidenceTierV1.T4_FIRST_PARTY_CAPTURE),))
        self.assertEqual(professional.authority().reasons, (REASON_NUMERIC_POLICY_UNSET, REASON_COST_POLICY_UNSET))
        self.assertFalse(professional.authority().promotable)

    def test_naive_datetimes_and_bad_hashes_are_refused(self) -> None:
        with self.assertRaises(StrategyLabStudyError):
            _study(evaluation_upper_bound_exclusive=datetime(2026, 8, 1))  # noqa: DTZ001
        with self.assertRaises(StrategyLabStudyError):
            _strategy(implementation_sha256="A" * 64)
        with self.assertRaises(StrategyLabStudyError):
            _binding(content_hash="short")

    def test_typed_point_gives_exact_values(self) -> None:
        typed = _space().typed_point({"fast": "6", "slow": "30", "threshold": "0.0025"})
        self.assertEqual(typed, {"fast": 6, "slow": 30, "threshold": Decimal("0.0025")})


if __name__ == "__main__":
    unittest.main()
