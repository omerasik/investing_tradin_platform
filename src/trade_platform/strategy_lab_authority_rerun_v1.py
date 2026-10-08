"""Phase R4.7 -- the Decimal authority rerun of frozen and flagged Strategy Lab candidates (OR-3).

``RESEARCH_ONLY``. The owner's numeric doctrine (OR-3, 2026-10-08) makes float64
the search tier and ``Decimal`` the authoritative tier. This module is the only
door from one to the other for an R4.6 SDK study:

1. **Rerun set.** From a finished study's manifest and one frozen candidate set:
   every frozen candidate; every evaluated candidate whose float selection
   metric lies inside the OR-3 relative error band around the cutoff; every
   candidate the study flagged with a near-tie decision (the OR-3 near-tie
   scope is the whole study, not only the top of the ranking).
2. **Recompute the complete path in Decimal.** For each candidate the family's
   ``targets_decimal`` is run on the exact bar prices, the shared integer
   execution rule (:func:`~trade_platform.strategy_sdk_v1.held_positions_v1`)
   turns targets into held positions, and every metric is recomputed with
   ``Decimal`` arithmetic at the OR-3 precision. Nothing is converted from a
   float result.
3. **Reconcile.** The float held path is recomputed too and compared bar by
   bar. ``RECONCILED`` means identical positions; ``DIVERGED_DECIMAL_WINS``
   records how many bars and trades differ and uses the Decimal numbers.
4. **OR-5 sweep.** Every frozen candidate is also recomputed at each mandatory
   sweep lag (5/30/60 s). All results are kept; none is chosen.
5. **Authoritative selection.** The selection rule of the candidate set is
   re-applied to the Decimal metrics of the rerun set. It is ``ESTABLISHED``
   only if no candidate outside the rerun set could reach the cutoff -- i.e.
   the best float metric outside the rerun set is not inside the error band of
   the Decimal cutoff. Otherwise it fails closed
   (``FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED``).

Authority here is *numeric* authority over search economics. The study stays
gross under OR-6 without a verified fee schedule and T2 results stay
CONDITIONAL under OR-5; nothing here promotes anything (that is R6).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Final
from uuid import UUID

from .persistence import PostgresDatabase
from .strategy_lab_manifest_v1 import CandidateSetV1, StudyManifestV1
from .strategy_lab_policies_v1 import (
    AUTHORITATIVE_DECIMAL_PRECISION_V1,
    NUMERIC_TIER_AUTHORITATIVE,
    OR5_SWEEP_LAGS_V1,
    SELECTION_CUTOFF_ERROR_BAND_RELATIVE_V1,
    or3_numeric_policy_v1,
)
from .strategy_lab_study_v1 import StudySpecV1, identity_hash_v1
from .strategy_sdk_v1 import (
    FAMILIES_V1,
    BarsV1,
    held_positions_v1,
    load_bars_v1,
    restrict_to_bound_v1,
)

RERUN_SCHEMA_VERSION_V1: Final = "strategy-lab-authority-rerun-v1"
RECONCILED: Final = "RECONCILED"
DIVERGED_DECIMAL_WINS: Final = "DIVERGED_DECIMAL_WINS"
SELECTION_ESTABLISHED: Final = "ESTABLISHED"
SELECTION_FAIL_CLOSED: Final = "FAIL_CLOSED_AUTHORITY_NOT_ESTABLISHED"

REASON_FROZEN: Final = "FROZEN_CANDIDATE"
REASON_CUTOFF_BAND: Final = "INSIDE_SELECTION_CUTOFF_ERROR_BAND"
REASON_NEAR_TIE: Final = "NEAR_TIE_FLAGGED"

_METRIC_QUANTUM: Final = Decimal("1E-18")
_MICROS_PER_DAY: Final = 86_400_000_000
_FUNDING_GRID_MICROS: Final = 8 * 3_600_000_000


class AuthorityRerunError(ValueError):
    """Raised when a rerun cannot be performed or established honestly."""


@contextmanager
def _authoritative() -> Iterator[None]:
    with localcontext(prec=AUTHORITATIVE_DECIMAL_PRECISION_V1, rounding=ROUND_HALF_EVEN):
        yield


def _q(value: Decimal | None) -> str | None:
    if value is None or not value.is_finite():
        return None
    text = format(value.quantize(_METRIC_QUANTUM), "f")
    return "0" if text.strip("-0.") == "" else text


def decimal_metrics_v1(
    bars: BarsV1, held: Sequence[int], *, cost_bps_per_side: Decimal | None = None, cost_label: str | None = None,
) -> dict[str, Any]:
    """The search metrics' definitions, recomputed exactly in Decimal (quantized to 1e-18).

    ``cost_bps_per_side`` (R6) charges ``cost * |held_j - held_{j-1}|`` at the
    fill of interval ``j``; it must come from an OR-6 cost policy with a verified
    fee schedule and a named scenario (``cost_label``). Without it the metrics
    are gross and labelled so.
    """
    if (cost_bps_per_side is None) != (cost_label is None):
        raise AuthorityRerunError("a_cost_requires_its_scenario_label")
    n = bars.size
    with _authoritative():
        returns = [bars.open_d[j + 1] / bars.open_d[j] - 1 for j in range(n - 1)] + [Decimal(0)]
        cost = Decimal(0) if cost_bps_per_side is None else cost_bps_per_side / 10_000
        strategy = [
            held[j] * returns[j] - cost * abs(held[j] - (held[j - 1] if j else 0)) for j in range(n)
        ]
        turnover = Decimal(0)
        trades = 0
        previous = 0
        for value in held:
            change = abs(value - previous)
            if change:
                trades += 1
                turnover += change
            previous = value
        equity = Decimal(1)
        peak = Decimal(1)
        drawdown = Decimal(0)
        for step in strategy:
            equity *= 1 + step
            peak = max(peak, equity)
            drawdown = max(drawdown, 1 - equity / peak)
        daily: dict[int, Decimal] = {}
        monthly: dict[tuple[int, int], Decimal] = {}
        for j in range(n):
            day = int(bars.open_us[j]) // _MICROS_PER_DAY
            daily[day] = daily.get(day, Decimal(1)) * (1 + strategy[j])
            stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(days=day)
            key = (stamp.year, stamp.month)
            monthly[key] = monthly.get(key, Decimal(1)) * (1 + strategy[j])
        day_returns = [value - 1 for value in daily.values()]
        month_returns = [value - 1 for value in monthly.values()]
        sharpe: Decimal | None = None
        if len(day_returns) > 1:
            mean = sum(day_returns, Decimal(0)) / len(day_returns)
            variance = sum(((r - mean) ** 2 for r in day_returns), Decimal(0)) / (len(day_returns) - 1)
            if variance > 0:
                sharpe = mean / variance.sqrt() * Decimal(365).sqrt()
        gross_sum = sum(strategy, Decimal(0))
        crossings = 0
        for j in range(n - 1):
            if held[j] != 0 and int(bars.open_us[j + 1]) // _FUNDING_GRID_MICROS > int(bars.open_us[j]) // _FUNDING_GRID_MICROS:
                crossings += 1
        return {
            "numeric_tier": NUMERIC_TIER_AUTHORITATIVE,
            "cost_mode": "GROSS_NON_PROMOTABLE" if cost_label is None else f"NET_OF:{cost_label}",
            "total_return": _q(equity - 1),
            "sharpe_daily_annualized": _q(sharpe),
            "max_drawdown": _q(drawdown),
            "trades": trades,
            "turnover": _q(turnover),
            "exposure": _q(sum((Decimal(abs(v)) for v in held), Decimal(0)) / n),
            "break_even_bps_per_side": _q(gross_sum / turnover * 10_000) if turnover else None,
            "positive_month_fraction": _q(Decimal(sum(1 for r in month_returns if r > 0)) / len(month_returns)),
            "worst_month_return": _q(min(month_returns)),
            "funding_window_crossings": crossings,
            "bars_evaluated": n,
        }


# ---------------------------------------------------------------------------
# Rerun set
# ---------------------------------------------------------------------------


def _metric(row: Mapping[str, Any], metric: str) -> Decimal | None:
    raw = row["metrics"].get(metric)
    if raw is None or isinstance(raw, bool):
        return None
    value = Decimal(str(raw))
    return value if value.is_finite() else None


def rerun_set_v1(manifest: StudyManifestV1, candidates: CandidateSetV1) -> dict[str, list[str]]:
    """``trial_id -> reasons`` for every candidate OR-3 requires to be recomputed."""
    if candidates.manifest_hash != manifest.manifest_hash:
        raise AuthorityRerunError("candidate_set_is_not_from_this_manifest")
    rule = candidates.identity["rule"]
    metric = str(rule["metric"])
    band = Decimal(SELECTION_CUTOFF_ERROR_BAND_RELATIVE_V1)
    frozen = [item["trial_id"] for item in candidates.candidates]
    cutoff = Decimal(str(candidates.candidates[-1]["metric_value"]))
    tolerance = abs(cutoff) * band
    reasons: dict[str, list[str]] = {trial: [REASON_FROZEN] for trial in frozen}
    for row in manifest.results:
        if row["outcome"] != "EVALUATED":
            continue
        trial = str(row["trial_id"])
        value = _metric(row, metric)
        if value is not None and abs(value - cutoff) <= tolerance:
            reasons.setdefault(trial, []).append(REASON_CUTOFF_BAND)
        if int(row["metrics"].get("near_tie_decisions") or 0) > 0:
            reasons.setdefault(trial, []).append(REASON_NEAR_TIE)
    return {trial: sorted(set(items)) for trial, items in sorted(reasons.items())}


# ---------------------------------------------------------------------------
# One candidate (process-pool safe)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Job:
    data_root: Any
    study: StudySpecV1
    trial_id: str
    parameters: Mapping[str, str]
    lag_micros: int


def _rerun_one(job: _Job) -> dict[str, Any]:
    study = job.study
    family = FAMILIES_V1[study.strategy.family]
    if family.spec() != study.strategy:
        raise AuthorityRerunError("study_strategy_is_not_this_sdk_family_version")
    params = study.parameter_space.typed_point(job.parameters)
    (binding,) = study.datasets
    lag = timedelta(microseconds=job.lag_micros)
    bars = load_bars_v1(job.data_root, binding.dataset_version_id, expected_content_hash=binding.content_hash)
    bars = restrict_to_bound_v1(bars, study.evaluation_upper_bound_exclusive, lag)
    exact_targets = family.targets_decimal(bars, params)
    exact_held = held_positions_v1(bars, exact_targets, job.lag_micros)
    float_targets, ties = family.targets_f64(bars, params)
    float_held = held_positions_v1(bars, float_targets, job.lag_micros)
    divergent = sum(1 for a, b in zip(exact_held, float_held, strict=True) if a != b)
    metrics = decimal_metrics_v1(bars, exact_held)
    float_trades = sum(1 for prev, cur in zip([0, *float_held[:-1]], float_held, strict=True) if prev != cur)
    return {
        "trial_id": job.trial_id,
        "lag_micros": job.lag_micros,
        "reconciliation": RECONCILED if divergent == 0 else DIVERGED_DECIMAL_WINS,
        "held_divergence_bars": divergent,
        "trade_count_divergence": abs(int(metrics["trades"]) - float_trades),
        "float_near_tie_decisions": ties,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# The rerun
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthorityRerunV1:
    identity: Mapping[str, Any]
    rerun_hash: str

    @property
    def selection_status(self) -> str:
        return str(self.identity["authoritative_selection"]["status"])


def run_authority_rerun_v1(
    study: StudySpecV1, manifest: StudyManifestV1, candidates: CandidateSetV1, *, data_root: Any,
    workers: int = 1,
) -> AuthorityRerunV1:
    """Recompute the OR-3 rerun set in Decimal, reconcile, sweep, and re-establish the selection."""
    if study.policies is None or study.numeric_policy_slot != or3_numeric_policy_v1().slot:
        raise AuthorityRerunError("authority_rerun_requires_an_or_3_bound_study")
    if manifest.identity["study_content_hash"] != study.content_hash:
        raise AuthorityRerunError("manifest_is_not_from_this_study")
    baseline = study.timing_lag
    if baseline is None:
        raise AuthorityRerunError("t2_study_without_the_or_5_timing_policy")
    reasons = rerun_set_v1(manifest, candidates)
    by_trial = {str(row["trial_id"]): row for row in manifest.results}
    trials = {str(trial.trial_id): trial for trial in study.trials()}
    frozen = [str(item["trial_id"]) for item in candidates.candidates]
    jobs = [
        _Job(data_root, study, trial, trials[trial].parameters, baseline // timedelta(microseconds=1))
        for trial in reasons
    ]
    jobs += [
        _Job(data_root, study, trial, trials[trial].parameters, lag // timedelta(microseconds=1))
        for trial in frozen for lag in OR5_SWEEP_LAGS_V1
    ]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            outputs = list(pool.map(_rerun_one, jobs))
    else:
        outputs = [_rerun_one(job) for job in jobs]
    results = []
    for output in outputs:
        trial = output["trial_id"]
        results.append({
            **output,
            "trial_content_hash": trials[trial].content_hash,
            "search_result_content_hash": by_trial[trial]["result_content_hash"],
            "reasons": reasons.get(trial, [REASON_FROZEN]),
        })
    results.sort(key=lambda item: (item["trial_id"], item["lag_micros"]))
    selection = _authoritative_selection(manifest, candidates, results, reasons,
                                         baseline // timedelta(microseconds=1))
    identity = {
        "schema_version": RERUN_SCHEMA_VERSION_V1,
        "study_id": str(study.study_id),
        "study_content_hash": study.content_hash,
        "manifest_hash": manifest.manifest_hash,
        "candidate_set_hash": candidates.candidate_set_hash,
        "numeric_policy_slot": study.numeric_policy_slot,
        "cost_policy_slot": study.cost_policy_slot,
        "numeric_tier": NUMERIC_TIER_AUTHORITATIVE,
        "baseline_lag_micros": baseline // timedelta(microseconds=1),
        "sweep_lags_micros": [lag // timedelta(microseconds=1) for lag in OR5_SWEEP_LAGS_V1],
        "rerun_count": len(reasons),
        "results": results,
        "authoritative_selection": selection,
        "claim_ceiling": "CONDITIONAL_T2_GROSS_NOT_PROMOTED",
    }
    return AuthorityRerunV1(identity, identity_hash_v1(identity))


def _authoritative_selection(
    manifest: StudyManifestV1, candidates: CandidateSetV1, results: Sequence[Mapping[str, Any]],
    reasons: Mapping[str, Sequence[str]], baseline_lag: int,
) -> dict[str, Any]:
    rule = candidates.identity["rule"]
    metric, top_k = str(rule["metric"]), int(rule["top_k"])
    sign = -1 if rule["direction"] == "HIGHER_IS_BETTER" else 1
    exact: list[tuple[Decimal, str]] = []
    undefined: list[str] = []
    for item in results:
        if item["lag_micros"] != baseline_lag:
            continue
        raw = item["metrics"].get(metric)
        if raw is None:
            undefined.append(item["trial_id"])
        else:
            exact.append((Decimal(str(raw)), item["trial_id"]))
    hash_of = {str(row["trial_id"]): str(row["trial_content_hash"]) for row in manifest.results}
    exact.sort(key=lambda pair: (sign * pair[0], hash_of[pair[1]]))
    if len(exact) < top_k:
        return {"status": SELECTION_FAIL_CLOSED, "reason": "fewer_defined_decimal_metrics_than_top_k",
                "selected": [], "undefined_metric_trials": sorted(undefined)}
    selected = exact[:top_k]
    cutoff = selected[-1][0]
    band = abs(cutoff) * Decimal(SELECTION_CUTOFF_ERROR_BAND_RELATIVE_V1)
    # Candidates never recomputed keep float metrics; Decimal authority over them
    # holds only if none can reach the Decimal cutoff within the error band.
    outside = [
        value for row in manifest.results
        if row["outcome"] == "EVALUATED" and str(row["trial_id"]) not in reasons
        for value in [_metric(row, metric)] if value is not None
    ]
    best_outside = (max(outside) if sign == -1 else min(outside)) if outside else None
    reaches = best_outside is not None and (
        best_outside >= cutoff - band if sign == -1 else best_outside <= cutoff + band
    )
    tie_at_cutoff = len(exact) > top_k and exact[top_k][0] == cutoff
    status = SELECTION_FAIL_CLOSED if reaches else SELECTION_ESTABLISHED
    return {
        "status": status,
        "reason": "an_unrecomputed_candidate_can_reach_the_decimal_cutoff" if reaches else None,
        "rule": dict(rule),
        "selected": [{"rank": rank, "trial_id": trial, "metric_value": _q(value)}
                     for rank, (value, trial) in enumerate(selected, start=1)],
        "decimal_cutoff": _q(cutoff),
        "best_unrecomputed_float_metric": None if best_outside is None else _q(best_outside),
        "cutoff_tie": tie_at_cutoff,
        "changed_versus_search_selection": sorted(trial for _, trial in selected)
        != sorted(str(item["trial_id"]) for item in candidates.candidates),
        "undefined_metric_trials": sorted(undefined),
    }


# ---------------------------------------------------------------------------
# Persistence (migration 20261008_0056)
# ---------------------------------------------------------------------------


class PostgresAuthorityRerunStoreV1:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def record(self, rerun: AuthorityRerunV1) -> bool:
        identity = rerun.identity
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO strategy_lab_authority_reruns (rerun_hash, candidate_set_hash, study_id, "
                "numeric_tier, numeric_policy_slot, selection_status, rerun_count, identity, recorded_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) ON CONFLICT (rerun_hash) DO NOTHING RETURNING rerun_hash",
                (rerun.rerun_hash, identity["candidate_set_hash"], UUID(str(identity["study_id"])),
                 identity["numeric_tier"], identity["numeric_policy_slot"], rerun.selection_status,
                 identity["rerun_count"], json.dumps(dict(identity), sort_keys=True), datetime.now(UTC)),
            )
            return cursor.fetchone() is not None

    def for_candidate_set(self, candidate_set_hash: str) -> tuple[AuthorityRerunV1, ...]:
        with self._database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT rerun_hash, identity FROM strategy_lab_authority_reruns "
                           "WHERE candidate_set_hash=%s ORDER BY recorded_at, rerun_hash", (candidate_set_hash,))
            rows = cursor.fetchall()
        out = []
        for rerun_hash, raw in rows:
            identity = raw if isinstance(raw, dict) else json.loads(raw)
            if identity_hash_v1(identity) != rerun_hash:
                raise AuthorityRerunError("stored_rerun_hash_mismatch")
            out.append(AuthorityRerunV1(identity, rerun_hash))
        return tuple(out)


__all__ = [
    "DIVERGED_DECIMAL_WINS",
    "RECONCILED",
    "RERUN_SCHEMA_VERSION_V1",
    "SELECTION_ESTABLISHED",
    "SELECTION_FAIL_CLOSED",
    "AuthorityRerunError",
    "AuthorityRerunV1",
    "PostgresAuthorityRerunStoreV1",
    "decimal_metrics_v1",
    "rerun_set_v1",
    "run_authority_rerun_v1",
]
