"""Phase R4.0 -- Strategy Lab study contract: immutable study and trial identity.

``RESEARCH_ONLY``. This module is the policy-neutral identity layer of the R4
Strategy Lab kernel. It declares *what* a study searches -- one strategy
specification, one exact parameter space, the datasets it binds by role and
evidence tier, the search budget and seed, the evaluation upper bound -- and
derives content-addressed study and trial identities from that declaration. It
evaluates nothing, persists nothing, prices nothing and grants no authority.

What a study binds
------------------
* :class:`StrategySpecV1` -- a strategy family, version and implementation
  hash; the input roles it reads, each with the evidence tiers admissible for
  it; its execution convention (a label the strategy owns, e.g. which bar a
  decision executes on); and its parameter schema (names and domain kinds).
* :class:`ParameterSpaceV1` -- one exact, finite domain per parameter. Values
  are canonical text: integers, finite ``Decimal`` values (never ``float``) and
  categorical labels. Two spellings of one number (``1.5``/``1.50``) are one
  value and are refused as duplicates.
* :class:`DatasetBindingV1` -- dataset version id, content hash, evidence tier
  and the exclusive knowledge-time upper bound of the bound data, per role.
* :class:`SearchPlanV1` -- ``EXHAUSTIVE`` (every point, budget must cover the
  whole space: a grid is never silently truncated) or ``SEEDED_SAMPLE`` (a
  budget-sized sample without replacement, selected by a seed through a
  SHA-256 ranking that does not depend on any library's PRNG).

Fail closed, never fill in
--------------------------
The numeric doctrine (owner decision OR-3) and the research cost methodology
(owner decision OR-6) are open. A study records both as explicit *slots* whose
only admissible value in v1 is the unset marker -- the same mechanism as the
T2 ``publication_lag_slot`` (OR-5). Admitting a policy is a later, reviewed
contract version, never a field value. Consequently every v1 study is
``NON_AUTHORITATIVE`` and ``NOT_PROMOTABLE`` by construction, and
:meth:`StudySpecV1.authority` says why. Nothing here supplies a default fee,
spread, slippage, funding assumption, threshold or numeric tolerance.

The untouched holdout is a hard upper bound: a study's evaluation bound may
not exceed :data:`UNTOUCHED_HOLDOUT_BOUNDARY_V1`, and every bound dataset's
knowledge-time upper bound must sit at or below the study's bound.

Identity rules
--------------
Identity payloads are canonical JSON over ``str``/``int``/``bool``/``None``,
lists and string-keyed objects only; a ``float`` (or any other type) is
refused, so binary floating point can never enter an identity hash. The study
id is ``uuid5`` of the study content hash; a trial id is ``uuid5`` of the trial
content hash, which binds the study content hash and the canonical parameter
point. Resubmitting an identical declaration yields identical ids. A
human-readable label is metadata, outside identity.

Every trial a study plans counts toward its multiple-testing denominator
(:attr:`StudySpecV1.planned_trial_count`), including any a strategy later
reports as inadmissible; a result never cites a smaller count.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import prod
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .evidence_tier_authority_v1 import PROFESSIONAL_EVIDENCE_TIERS_V1, EvidenceTierV1

STUDY_SCHEMA_VERSION_V1: Final = "strategy-lab-study-v1"
TRIAL_SCHEMA_VERSION_V1: Final = "strategy-lab-trial-v1"

#: Pre-registered untouched holdout boundary (equal by test to the boundary the
#: earlier engineering pilot pre-registered). Nothing at or after it is searched.
UNTOUCHED_HOLDOUT_BOUNDARY_V1: Final = datetime(2026, 8, 20, tzinfo=UTC)

#: The only admissible numeric-policy slot value in v1 (owner decision OR-3).
NUMERIC_POLICY_UNSET_V1: Final = "UNSET_PENDING_OWNER_DECISION_OR_3"
#: The only admissible cost-policy slot value in v1 (owner decision OR-6).
COST_POLICY_UNSET_V1: Final = "UNSET_PENDING_OWNER_DECISION_OR_6"

REASON_NUMERIC_POLICY_UNSET: Final = "NUMERIC_POLICY_UNSET_PENDING_OR_3"
REASON_COST_POLICY_UNSET: Final = "COST_POLICY_UNSET_PENDING_OR_6"
REASON_NON_PROFESSIONAL_TIER: Final = "BOUND_DATASET_TIER_NOT_PROFESSIONAL"
REASON_SEARCH_TIER_ONLY: Final = "SEARCH_TIER_RESULTS_REQUIRE_A_DECIMAL_AUTHORITY_RERUN"
REASON_COST_POLICY_GROSS: Final = "COST_POLICY_GROSS_NON_PROMOTABLE_NO_VERIFIED_FEE_SCHEDULE"

#: Operational guards against accidental enormous declarations; not evidence rules.
MAX_DOMAIN_VALUES_V1: Final = 100_000
MAX_SAMPLED_SPACE_V1: Final = 10_000_000

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.strategy_lab_study_v1")
_SLUG: Final = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_VERSION: Final = re.compile(r"^[0-9a-z][0-9a-z_.+-]{0,63}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


class StrategyLabStudyError(ValueError):
    """Raised when a study declaration is incomplete, incoherent or out of bounds."""


# ---------------------------------------------------------------------------
# Canonical identity encoding
# ---------------------------------------------------------------------------


def _check_identity_value(value: object, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _check_identity_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise StrategyLabStudyError(f"identity_key_not_text:{path}")
            _check_identity_value(item, f"{path}.{key}")
        return
    raise StrategyLabStudyError(f"identity_value_type_refused:{path}:{type(value).__name__}")


def canonical_identity_json_v1(payload: Mapping[str, Any]) -> str:
    """Canonical JSON of an identity payload; refuses floats and unknown types."""
    _check_identity_value(dict(payload), "$")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def identity_hash_v1(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_identity_json_v1(payload).encode("ascii")).hexdigest()


def _slug(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SLUG.match(value):
        raise StrategyLabStudyError(f"{name}_must_be_a_lowercase_slug")
    return value


def _sha256_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.match(value):
        raise StrategyLabStudyError(f"{name}_must_be_64_lowercase_hex")
    return value


def _utc(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise StrategyLabStudyError(f"{name}_must_be_timezone_aware")
    return value.astimezone(UTC)


def _instant(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


# ---------------------------------------------------------------------------
# Parameter domains and spaces
# ---------------------------------------------------------------------------


class ParameterKindV1(StrEnum):
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    CATEGORICAL = "CATEGORICAL"


def _integer_text(value: object, name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StrategyLabStudyError(f"{name}_integer_value_must_be_int")
    return str(value)


def _decimal_text(value: object, name: str) -> str:
    if isinstance(value, (float, bool)) or not isinstance(value, (Decimal, str, int)):
        raise StrategyLabStudyError(f"{name}_decimal_value_must_be_decimal_or_text")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise StrategyLabStudyError(f"{name}_decimal_value_unparseable") from error
    if not number.is_finite():
        raise StrategyLabStudyError(f"{name}_decimal_value_must_be_finite")
    text = format(number.normalize(), "f")
    return "0" if text in {"-0", "0"} else text


@dataclass(frozen=True, slots=True)
class ParameterDomainV1:
    """One parameter's finite admissible values, as canonical text in declared order."""

    name: str
    kind: ParameterKindV1
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        _slug(self.name, "parameter_name")
        if not isinstance(self.kind, ParameterKindV1):
            raise StrategyLabStudyError("parameter_kind_unknown")
        if not self.values:
            raise StrategyLabStudyError(f"parameter_domain_empty:{self.name}")
        if len(self.values) > MAX_DOMAIN_VALUES_V1:
            raise StrategyLabStudyError(f"parameter_domain_too_large:{self.name}")
        canonical = tuple(self._canonical(value) for value in self.values)
        if canonical != self.values:
            raise StrategyLabStudyError(f"parameter_values_not_canonical:{self.name}")
        if len(set(canonical)) != len(canonical):
            raise StrategyLabStudyError(f"parameter_values_duplicated:{self.name}")

    def _canonical(self, value: object) -> str:
        if not isinstance(value, str):
            raise StrategyLabStudyError(f"{self.name}_values_must_be_canonical_text")
        if self.kind is ParameterKindV1.INTEGER:
            try:
                return str(int(value))
            except ValueError as error:
                raise StrategyLabStudyError(f"{self.name}_integer_value_unparseable") from error
        if self.kind is ParameterKindV1.DECIMAL:
            return _decimal_text(value, self.name)
        if not isinstance(value, str) or not value or value != value.strip():
            raise StrategyLabStudyError(f"{self.name}_categorical_value_must_be_trimmed_text")
        return value

    @classmethod
    def integer_range(cls, name: str, *, start: int, stop_inclusive: int, step: int) -> ParameterDomainV1:
        for label, item in (("start", start), ("stop", stop_inclusive), ("step", step)):
            _integer_text(item, f"{name}_{label}")
        if step <= 0 or stop_inclusive < start:
            raise StrategyLabStudyError(f"parameter_range_invalid:{name}")
        if (stop_inclusive - start) // step + 1 > MAX_DOMAIN_VALUES_V1:
            raise StrategyLabStudyError(f"parameter_domain_too_large:{name}")
        return cls(name, ParameterKindV1.INTEGER, tuple(str(v) for v in range(start, stop_inclusive + 1, step)))

    @classmethod
    def integer_values(cls, name: str, values: Sequence[int]) -> ParameterDomainV1:
        """An explicit list of integers (e.g. meaningful lookbacks), in declared order."""
        return cls(name, ParameterKindV1.INTEGER, tuple(_integer_text(v, name) for v in values))

    @classmethod
    def decimal_set(cls, name: str, values: Sequence[Decimal | str | int]) -> ParameterDomainV1:
        return cls(name, ParameterKindV1.DECIMAL, tuple(_decimal_text(v, name) for v in values))

    @classmethod
    def categorical_set(cls, name: str, values: Sequence[str]) -> ParameterDomainV1:
        return cls(name, ParameterKindV1.CATEGORICAL, tuple(values))

    def typed(self, text: str) -> int | Decimal | str:
        if text not in self.values:
            raise StrategyLabStudyError(f"parameter_value_outside_domain:{self.name}")
        if self.kind is ParameterKindV1.INTEGER:
            return int(text)
        if self.kind is ParameterKindV1.DECIMAL:
            return Decimal(text)
        return text

    def payload(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind.value, "values": list(self.values)}


@dataclass(frozen=True, slots=True)
class ParameterSpaceV1:
    """The Cartesian product of parameter domains, ordered by parameter name.

    Point ``i`` is the mixed-radix decoding of ``i`` with the last name varying
    fastest, so any point is addressable without materializing the space.
    """

    domains: tuple[ParameterDomainV1, ...]

    def __post_init__(self) -> None:
        if not self.domains:
            raise StrategyLabStudyError("parameter_space_empty")
        names = [domain.name for domain in self.domains]
        if names != sorted(names) or len(set(names)) != len(names):
            raise StrategyLabStudyError("parameter_space_names_must_be_unique_and_sorted")

    @classmethod
    def of(cls, *domains: ParameterDomainV1) -> ParameterSpaceV1:
        return cls(tuple(sorted(domains, key=lambda domain: domain.name)))

    @property
    def cardinality(self) -> int:
        return prod(len(domain.values) for domain in self.domains)

    def schema(self) -> dict[str, str]:
        return {domain.name: domain.kind.value for domain in self.domains}

    def point_at(self, index: int) -> dict[str, str]:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < self.cardinality:
            raise StrategyLabStudyError("parameter_point_index_out_of_range")
        point: dict[str, str] = {}
        for domain in reversed(self.domains):
            index, digit = divmod(index, len(domain.values))
            point[domain.name] = domain.values[digit]
        return dict(sorted(point.items()))

    def index_of(self, point: Mapping[str, str]) -> int:
        if sorted(point) != [domain.name for domain in self.domains]:
            raise StrategyLabStudyError("parameter_point_names_mismatch")
        index = 0
        for domain in self.domains:
            if point[domain.name] not in domain.values:
                raise StrategyLabStudyError(f"parameter_value_outside_domain:{domain.name}")
            index = index * len(domain.values) + domain.values.index(point[domain.name])
        return index

    def typed_point(self, point: Mapping[str, str]) -> dict[str, int | Decimal | str]:
        self.index_of(point)
        return {domain.name: domain.typed(point[domain.name]) for domain in self.domains}

    def payload(self) -> list[dict[str, Any]]:
        return [domain.payload() for domain in self.domains]


# ---------------------------------------------------------------------------
# Strategy, datasets, search plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InputRoleV1:
    """A named strategy input and the evidence tiers that may fill it."""

    role: str
    admissible_tiers: frozenset[EvidenceTierV1]

    def __post_init__(self) -> None:
        _slug(self.role, "input_role")
        if not self.admissible_tiers or not all(isinstance(t, EvidenceTierV1) for t in self.admissible_tiers):
            raise StrategyLabStudyError(f"input_role_needs_admissible_tiers:{self.role}")

    def payload(self) -> dict[str, Any]:
        return {"role": self.role, "admissible_tiers": sorted(t.value for t in self.admissible_tiers)}


@dataclass(frozen=True, slots=True)
class StrategySpecV1:
    family: str
    version: str
    implementation_sha256: str
    execution_convention: str
    input_roles: tuple[InputRoleV1, ...]
    parameter_schema: Mapping[str, ParameterKindV1]

    def __post_init__(self) -> None:
        _slug(self.family, "strategy_family")
        if not isinstance(self.version, str) or not _VERSION.match(self.version):
            raise StrategyLabStudyError("strategy_version_must_be_lowercase_version_text")
        _sha256_text(self.implementation_sha256, "implementation_sha256")
        _slug(self.execution_convention, "execution_convention")
        roles = [item.role for item in self.input_roles]
        if not roles or roles != sorted(roles) or len(set(roles)) != len(roles):
            raise StrategyLabStudyError("input_roles_must_be_nonempty_unique_and_sorted")
        if not self.parameter_schema:
            raise StrategyLabStudyError("parameter_schema_empty")
        for name, kind in self.parameter_schema.items():
            _slug(name, "parameter_name")
            if not isinstance(kind, ParameterKindV1):
                raise StrategyLabStudyError(f"parameter_kind_unknown:{name}")
        object.__setattr__(self, "parameter_schema", dict(sorted(self.parameter_schema.items())))

    def payload(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "version": self.version,
            "implementation_sha256": self.implementation_sha256,
            "execution_convention": self.execution_convention,
            "input_roles": [item.payload() for item in self.input_roles],
            "parameter_schema": {name: kind.value for name, kind in self.parameter_schema.items()},
        }


@dataclass(frozen=True, slots=True)
class DatasetBindingV1:
    role: str
    dataset_version_id: UUID
    content_hash: str
    evidence_tier: EvidenceTierV1
    knowledge_upper_bound_exclusive: datetime

    def __post_init__(self) -> None:
        _slug(self.role, "dataset_role")
        if not isinstance(self.dataset_version_id, UUID):
            raise StrategyLabStudyError("dataset_version_id_must_be_uuid")
        _sha256_text(self.content_hash, "dataset_content_hash")
        if not isinstance(self.evidence_tier, EvidenceTierV1):
            raise StrategyLabStudyError("dataset_evidence_tier_unknown")
        object.__setattr__(
            self, "knowledge_upper_bound_exclusive", _utc(self.knowledge_upper_bound_exclusive, "knowledge_bound")
        )

    def payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "dataset_version_id": str(self.dataset_version_id),
            "content_hash": self.content_hash,
            "evidence_tier": self.evidence_tier.value,
            "knowledge_upper_bound_exclusive": _instant(self.knowledge_upper_bound_exclusive),
        }


class SearchModeV1(StrEnum):
    EXHAUSTIVE = "EXHAUSTIVE"
    SEEDED_SAMPLE = "SEEDED_SAMPLE"


@dataclass(frozen=True, slots=True)
class SearchPlanV1:
    mode: SearchModeV1
    max_trials: int
    seed: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SearchModeV1):
            raise StrategyLabStudyError("search_mode_unknown")
        if isinstance(self.max_trials, bool) or not isinstance(self.max_trials, int) or self.max_trials <= 0:
            raise StrategyLabStudyError("search_budget_must_be_a_positive_int")
        if self.mode is SearchModeV1.EXHAUSTIVE and self.seed is not None:
            raise StrategyLabStudyError("exhaustive_search_takes_no_seed")
        if self.mode is SearchModeV1.SEEDED_SAMPLE and (
            isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0
        ):
            raise StrategyLabStudyError("seeded_sample_needs_a_nonnegative_int_seed")

    def payload(self) -> dict[str, Any]:
        return {"mode": self.mode.value, "max_trials": self.max_trials, "seed": self.seed}


def seeded_sample_indices_v1(*, seed: int, population: int, count: int) -> tuple[int, ...]:
    """``count`` distinct indices of ``range(population)``, ascending, chosen by SHA-256 rank.

    Index ``i`` ranks by ``sha256("<seed>:<i>")``; the ``count`` lowest ranks win.
    The selection depends on nothing but the seed and the arithmetic of SHA-256,
    so it is stable across Python versions and libraries.
    """
    if not 0 < count <= population:
        raise StrategyLabStudyError("sample_count_out_of_range")
    if population > MAX_SAMPLED_SPACE_V1:
        raise StrategyLabStudyError("sampled_space_too_large")
    prefix = f"{seed}:".encode("ascii")
    ranked = heapq.nsmallest(
        count, range(population), key=lambda i: hashlib.sha256(prefix + str(i).encode("ascii")).digest()
    )
    return tuple(sorted(ranked))


# ---------------------------------------------------------------------------
# Study and trials
# ---------------------------------------------------------------------------


class StudyAuthorityStatusV1(StrEnum):
    NON_AUTHORITATIVE = "NON_AUTHORITATIVE"


@dataclass(frozen=True, slots=True)
class StudyAuthorityV1:
    status: StudyAuthorityStatusV1
    promotable: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TrialV1:
    study_id: UUID
    ordinal: int
    space_index: int
    parameters: Mapping[str, str]
    content_hash: str
    trial_id: UUID


@dataclass(frozen=True, slots=True)
class StudySpecV1:
    strategy: StrategySpecV1
    parameter_space: ParameterSpaceV1
    datasets: tuple[DatasetBindingV1, ...]
    search: SearchPlanV1
    evaluation_upper_bound_exclusive: datetime
    numeric_policy_slot: str = NUMERIC_POLICY_UNSET_V1
    cost_policy_slot: str = COST_POLICY_UNSET_V1
    label: str = field(default="", compare=False)
    #: R4.6: the owner policies this study is bound by (OR-3 numeric, OR-5 T2
    #: timing, OR-6 cost), as their exact payloads. ``None`` keeps the v1 unset
    #: slots and leaves the identity byte-identical to an R4.0 study.
    policies: Mapping[str, Any] | None = field(default=None, hash=False)

    def __post_init__(self) -> None:
        bound = _utc(self.evaluation_upper_bound_exclusive, "evaluation_upper_bound")
        object.__setattr__(self, "evaluation_upper_bound_exclusive", bound)
        if bound > UNTOUCHED_HOLDOUT_BOUNDARY_V1:
            raise StrategyLabStudyError("evaluation_bound_crosses_the_untouched_holdout")
        if self.policies is None:
            if self.numeric_policy_slot != NUMERIC_POLICY_UNSET_V1:
                raise StrategyLabStudyError("numeric_policy_slot_requires_bound_owner_policies")
            if self.cost_policy_slot != COST_POLICY_UNSET_V1:
                raise StrategyLabStudyError("cost_policy_slot_requires_bound_owner_policies")
        else:
            self._bind_policies()
        if self.parameter_space.schema() != {k: v.value for k, v in self.strategy.parameter_schema.items()}:
            raise StrategyLabStudyError("parameter_space_does_not_match_the_strategy_schema")
        roles = [binding.role for binding in self.datasets]
        if roles != sorted(roles) or len(set(roles)) != len(roles):
            raise StrategyLabStudyError("dataset_roles_must_be_unique_and_sorted")
        declared = {item.role: item for item in self.strategy.input_roles}
        if set(roles) != set(declared):
            raise StrategyLabStudyError("dataset_roles_must_match_the_strategy_input_roles")
        for binding in self.datasets:
            if binding.evidence_tier not in declared[binding.role].admissible_tiers:
                raise StrategyLabStudyError(f"dataset_tier_not_admissible_for_role:{binding.role}")
            if binding.knowledge_upper_bound_exclusive > bound:
                raise StrategyLabStudyError(f"dataset_knowledge_extends_past_the_evaluation_bound:{binding.role}")
        cardinality = self.parameter_space.cardinality
        if self.search.mode is SearchModeV1.EXHAUSTIVE and self.search.max_trials < cardinality:
            raise StrategyLabStudyError("exhaustive_budget_smaller_than_the_space")
        if self.search.mode is SearchModeV1.SEEDED_SAMPLE:
            if self.search.max_trials >= cardinality:
                raise StrategyLabStudyError("seeded_sample_budget_covers_the_space_use_exhaustive")
            if cardinality > MAX_SAMPLED_SPACE_V1:
                raise StrategyLabStudyError("sampled_space_too_large")
        if not isinstance(self.label, str):
            raise StrategyLabStudyError("study_label_must_be_text")

    def _bind_policies(self) -> None:
        """Admit only the owner-approved OR-3 and OR-5 payloads and an OR-6 cost policy payload.

        The slots are derived from the payloads; a caller cannot state a slot
        that disagrees with them. A study binding T2 data must bind OR-5.
        """
        from .strategy_lab_policies_v1 import (
            OR6_SCHEMA_VERSION_V1,
            PolicyV1,
            or3_numeric_policy_v1,
            or5_t2_timing_policy_v1,
        )

        policies = self.policies or {}
        if not isinstance(policies, Mapping) or set(policies) != {"numeric", "timing", "cost"}:
            raise StrategyLabStudyError("policies_must_name_numeric_timing_and_cost")
        canonical = {key: (None if value is None else dict(value)) for key, value in sorted(policies.items())}
        canonical_identity_json_v1(canonical)  # refuses floats and unknown types
        if canonical["numeric"] != or3_numeric_policy_v1().payload:
            raise StrategyLabStudyError("numeric_policy_is_not_the_owner_approved_or_3")
        uses_t2 = any(binding.evidence_tier is EvidenceTierV1.T2_EVENT_TIME for binding in self.datasets)
        if canonical["timing"] is not None and canonical["timing"] != or5_t2_timing_policy_v1().payload:
            raise StrategyLabStudyError("timing_policy_is_not_the_owner_approved_or_5")
        if uses_t2 and canonical["timing"] is None:
            raise StrategyLabStudyError("t2_dataset_requires_the_or_5_timing_policy")
        cost = canonical["cost"]
        if not isinstance(cost, dict) or cost.get("schema_version") != OR6_SCHEMA_VERSION_V1:
            raise StrategyLabStudyError("cost_policy_must_be_an_or_6_payload")
        numeric_slot = PolicyV1(canonical["numeric"]).slot
        cost_slot = PolicyV1(cost).slot
        for given, derived, name in ((self.numeric_policy_slot, numeric_slot, "numeric"),
                                     (self.cost_policy_slot, cost_slot, "cost")):
            if given not in {derived, NUMERIC_POLICY_UNSET_V1, COST_POLICY_UNSET_V1}:
                raise StrategyLabStudyError(f"{name}_policy_slot_disagrees_with_its_payload")
        object.__setattr__(self, "policies", canonical)
        object.__setattr__(self, "numeric_policy_slot", numeric_slot)
        object.__setattr__(self, "cost_policy_slot", cost_slot)

    def identity(self) -> dict[str, Any]:
        identity: dict[str, Any] = {
            "schema_version": STUDY_SCHEMA_VERSION_V1,
            "strategy": self.strategy.payload(),
            "parameter_space": self.parameter_space.payload(),
            "datasets": [binding.payload() for binding in self.datasets],
            "search": self.search.payload(),
            "evaluation_upper_bound_exclusive": _instant(self.evaluation_upper_bound_exclusive),
            "numeric_policy_slot": self.numeric_policy_slot,
            "cost_policy_slot": self.cost_policy_slot,
        }
        if self.policies is not None:
            identity["policies"] = dict(self.policies)
        return identity

    @property
    def timing_lag(self) -> Any:
        """The OR-5 baseline lag a T2-bound study evaluates at, or ``None``."""
        if self.policies is None or self.policies.get("timing") is None:
            return None
        from datetime import timedelta

        return timedelta(microseconds=int(self.policies["timing"]["baseline_dissemination_lag_micros"]))

    @property
    def content_hash(self) -> str:
        return identity_hash_v1(self.identity())

    @property
    def study_id(self) -> UUID:
        return uuid5(_NAMESPACE, f"study:{self.content_hash}")

    @property
    def planned_trial_count(self) -> int:
        """The multiple-testing denominator every result of this study must cite."""
        if self.search.mode is SearchModeV1.EXHAUSTIVE:
            return self.parameter_space.cardinality
        return self.search.max_trials

    def authority(self) -> StudyAuthorityV1:
        if self.policies is None:
            reasons = [REASON_NUMERIC_POLICY_UNSET, REASON_COST_POLICY_UNSET]
        else:
            # A study is a search: its results are float search-tier evidence
            # whatever the policies say (OR-3); only a Decimal rerun is authoritative.
            reasons = [REASON_SEARCH_TIER_ONLY]
            if self.policies["cost"].get("mode") == "GROSS_NON_PROMOTABLE":
                reasons.append(REASON_COST_POLICY_GROSS)
        if any(binding.evidence_tier not in PROFESSIONAL_EVIDENCE_TIERS_V1 for binding in self.datasets):
            reasons.append(REASON_NON_PROFESSIONAL_TIER)
        return StudyAuthorityV1(StudyAuthorityStatusV1.NON_AUTHORITATIVE, False, tuple(reasons))

    def _indices(self) -> Sequence[int]:
        cardinality = self.parameter_space.cardinality
        if self.search.mode is SearchModeV1.EXHAUSTIVE:
            return range(cardinality)
        if self.search.seed is None:
            raise StrategyLabStudyError("seeded_sample_needs_a_nonnegative_int_seed")
        return seeded_sample_indices_v1(seed=self.search.seed, population=cardinality, count=self.search.max_trials)

    def trial_for(self, point: Mapping[str, str], *, ordinal: int = -1) -> TrialV1:
        space_index = self.parameter_space.index_of(point)
        parameters = self.parameter_space.point_at(space_index)
        payload = {
            "schema_version": TRIAL_SCHEMA_VERSION_V1,
            "study_content_hash": self.content_hash,
            "parameters": parameters,
        }
        content_hash = identity_hash_v1(payload)
        return TrialV1(
            study_id=self.study_id,
            ordinal=ordinal,
            space_index=space_index,
            parameters=parameters,
            content_hash=content_hash,
            trial_id=uuid5(_NAMESPACE, f"trial:{content_hash}"),
        )

    def trials(self) -> Iterator[TrialV1]:
        """Every planned trial, in ascending space order, with its ordinal."""
        for ordinal, index in enumerate(self._indices()):
            yield self.trial_for(self.parameter_space.point_at(index), ordinal=ordinal)


__all__ = [
    "COST_POLICY_UNSET_V1",
    "MAX_DOMAIN_VALUES_V1",
    "MAX_SAMPLED_SPACE_V1",
    "NUMERIC_POLICY_UNSET_V1",
    "REASON_COST_POLICY_UNSET",
    "REASON_NON_PROFESSIONAL_TIER",
    "REASON_NUMERIC_POLICY_UNSET",
    "STUDY_SCHEMA_VERSION_V1",
    "TRIAL_SCHEMA_VERSION_V1",
    "UNTOUCHED_HOLDOUT_BOUNDARY_V1",
    "DatasetBindingV1",
    "InputRoleV1",
    "ParameterDomainV1",
    "ParameterKindV1",
    "ParameterSpaceV1",
    "SearchModeV1",
    "SearchPlanV1",
    "StrategyLabStudyError",
    "StrategySpecV1",
    "StudyAuthorityStatusV1",
    "StudyAuthorityV1",
    "StudySpecV1",
    "TrialV1",
    "canonical_identity_json_v1",
    "identity_hash_v1",
    "seeded_sample_indices_v1",
]
