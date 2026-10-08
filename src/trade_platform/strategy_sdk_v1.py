"""Phase R4.6 -- Strategy SDK v1: bar strategies, one causal execution convention, two numeric tiers.

``RESEARCH_ONLY``. A small SDK for price/trade bar strategies over T2 research
bar windows (:mod:`trade_platform.public_archive_research_bars_v1`). It plugs
into the existing R4 kernel unchanged: a family declares a
:class:`~trade_platform.strategy_lab_study_v1.StrategySpecV1` and an exact
:class:`~trade_platform.strategy_lab_study_v1.ParameterSpaceV1`;
:class:`BarStrategyEvaluatorV1` is the
:class:`~trade_platform.strategy_lab_worker_v1.TrialEvaluatorV1` the R4.2 worker
pool runs. No new experiment authority, ledger or queue exists here.

Execution convention ``t2-close-plus-lag-next-strictly-later-open-v1``
---------------------------------------------------------------------
* A family maps bars ``0..i`` (only those) to a target position
  ``t_i`` in ``{-1, 0, +1}``. Rolling windows never reach across a *segment*
  boundary: a segment is a run of bars with no declared missing UTC day
  between them, and a window that would need bars from before the segment
  start yields "no decision" (flat).
* The decision is made at ``close_i + lag`` where ``lag`` is the OR-5 T2
  dissemination lag the study is bound to (baseline 2 s; the 5/30/60 s sweep is
  re-evaluated by the authority rerun). A bar's information is never used
  before that instant.
* The decision fills at the *open* of the first bar whose open is strictly
  later than the decision instant, in the same segment. A decision that has no
  such bar in its segment never fills. Positions are flat across a segment
  boundary: the position is closed at the open of the segment's last bar.
* Interval returns are open-to-open. Search economics are gross (OR-6 with no
  verified fee schedule); the break-even cost per side is reported instead of
  invented costs. It is *additive*: the sum of simple interval returns divided
  by turnover, i.e. the per-side cost at which the summed gross edge is zero.
* The forced flat at the open of a segment's last bar uses the knowledge that
  the next UTC day is declared missing. No price information is used and the
  fill price is a real open, but that exit instant is a data-availability
  convention, not a causal strategy decision.
* A study window must end by 2026-08-18: the last bar of 2026-08-19 closes at
  the holdout boundary, so its knowledge bound plus the 60 s sweep lag would
  cross it and the study is refused (fail closed).

Two numeric tiers (owner decision OR-3)
---------------------------------------
:meth:`StrategyFamilyV1.targets_f64` is the vectorized SEARCH tier and
:meth:`StrategyFamilyV1.targets_decimal` the authoritative tier. Every decision
comparison is written without division or square root (cross-multiplied, or
compared on squares) and evaluated *exactly* in both tiers: integer price ticks
with exact int64 window sums in the search tier (a float is used only where the
two sides are far apart, with an exact integer recheck otherwise), exact
``Decimal`` in the authority tier. Both tiers therefore make identical
decisions; a dataset or window outside the int64 exactness bounds fails closed.
Float arithmetic remains in the search *metrics* only, so search rankings stay
``SEARCH_NON_AUTHORITATIVE``. The near-tie count reports exact boundary
equalities (and float-near comparisons for the channel family, whose float
prices are an order-preserving image of the exact ones); a flagged candidate
still joins the OR-3 rerun set.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import sys
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, ClassVar, Final

import numpy as np

from .evidence_tier_authority_v1 import EvidenceTierV1
from .strategy_lab_ledger_v1 import TrialOutcomeV1
from .strategy_lab_policies_v1 import (
    AUTHORITATIVE_DECIMAL_PRECISION_V1,
    NEAR_TIE_RELATIVE_TOLERANCE_V1,
    NUMERIC_TIER_SEARCH,
)
from .strategy_lab_study_v1 import (
    InputRoleV1,
    ParameterDomainV1,
    ParameterSpaceV1,
    StrategySpecV1,
    StudySpecV1,
)
from .strategy_lab_worker_v1 import TrialEvaluationV1

EXECUTION_CONVENTION_V1: Final = "t2-close-plus-lag-next-strictly-later-open-v1"
BARS_ROLE: Final = "bars"
NEAR_TIE_TOL: Final = float(NEAR_TIE_RELATIVE_TOLERANCE_V1)
_MICROS_PER_DAY: Final = 86_400_000_000
_FUNDING_GRID_MICROS: Final = 8 * 3_600_000_000
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


class StrategySdkError(ValueError):
    """Raised when bars, parameters or a binding cannot be evaluated honestly."""


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------


def _micros(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(microseconds=1)


#: Exactness bounds of the integer search arithmetic. With |centred tick| < 2^20
#: and windows <= 2^13 bars, every rolling sum (< 2^33), sum of squares
#: (< 2^53), ``w*Q - S^2`` (< 2^62 for windows <= 2^11) and the trend cross
#: products (< 2^56) are exact in int64. A dataset or window outside the bounds
#: fails closed instead of losing exactness.
_MAX_TICK_DEVIATION: Final = 1 << 20
_MAX_EXACT_WINDOW: Final = 1 << 13
_MAX_EXACT_VARIANCE_WINDOW: Final = 1 << 11


def _exact_ticks(prices: Sequence[Decimal]) -> tuple[int, int, np.ndarray]:
    """``(scale, centre, ticks)``: prices as exact integer ticks centred on their mid-range."""
    # Frame decimals carry the column scale (trailing zeros); the grid is the
    # normalized precision of the actual prices.
    places = max(0, max(-int(p.normalize().as_tuple().exponent) for p in prices))  # type: ignore[operator]
    if places > 9:
        raise StrategySdkError("price_precision_too_fine_for_exact_ticks")
    scale = 10 ** places
    raw = [int(p * scale) for p in prices]
    if any(Decimal(r) != p * scale for r, p in zip(raw, prices, strict=True)):
        raise StrategySdkError("price_not_on_its_decimal_grid")
    centre = (max(raw) + min(raw)) // 2
    centred = np.array([r - centre for r in raw], dtype=np.int64)
    if centred.size and int(np.max(np.abs(centred))) >= _MAX_TICK_DEVIATION:
        raise StrategySdkError("price_range_too_wide_for_exact_int64_arithmetic")
    return scale, centre, centred


def _window_sums(values: np.ndarray, window: int) -> np.ndarray:
    """Exact trailing sums over ``values[i-window+1..i]`` (int64; 0 where incomplete).

    The running sum may wrap in int64, but every *window* sum is far inside the
    int64 range, so the modular difference is the exact window sum.
    """
    if window > _MAX_EXACT_WINDOW:
        raise StrategySdkError("window_exceeds_the_exact_arithmetic_bound")
    out = np.zeros(values.shape, dtype=np.int64)
    if values.size < window:
        return out
    with np.errstate(over="ignore"):
        csum = np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(values, dtype=np.int64)))
        out[window - 1:] = csum[window:] - csum[:-window]
    return out


def _exact_scaled_square_compare(a: np.ndarray, n: np.ndarray, p: int, q: int, mask: np.ndarray,
                                 op: str) -> tuple[np.ndarray, int]:
    """``q^2 * a^2 (op) p^2 * n`` exactly, for int64 ``a`` and ``n``; returns (result, exact ties).

    Float decides wherever the two sides are separated by far more than float
    rounding; every other element is decided with Python integers.
    """
    lhs = float(q * q) * np.square(a.astype(float))
    rhs = float(p * p) * n.astype(float)
    result = {"<=": lhs <= rhs, ">=": lhs >= rhs}[op]
    close = mask & (np.abs(lhs - rhs) <= 1e-9 * np.maximum(np.maximum(np.abs(lhs), np.abs(rhs)), 1.0))
    ties = 0
    for index in np.flatnonzero(close):
        left = q * q * int(a[index]) ** 2
        right = p * p * int(n[index])
        ties += left == right
        result[index] = left <= right if op == "<=" else left >= right
    return result, ties


def _ratio(value: Decimal) -> tuple[int, int]:
    numerator, denominator = value.as_integer_ratio()
    return int(numerator), int(denominator)


@dataclass(frozen=True)
class BarsV1:
    """One bar window in both tiers. Rows are in ``bar_open_at`` order."""

    open_us: np.ndarray
    close_us: np.ndarray
    segment: np.ndarray
    open_f: np.ndarray
    high_f: np.ndarray
    low_f: np.ndarray
    close_f: np.ndarray
    open_d: tuple[Decimal, ...]
    high_d: tuple[Decimal, ...]
    low_d: tuple[Decimal, ...]
    close_d: tuple[Decimal, ...]
    segment_start: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0, dtype=np.int64))
    #: Closes as exact integer ticks (``close * close_scale``), centred on the
    #: window's mid-range (the centre cancels in every decision; it only decides
    #: whether the window is admissible), so rolling sums are exact int64 arithmetic.
    close_ticks: np.ndarray = field(repr=False, default_factory=lambda: np.zeros(0, dtype=np.int64))
    close_scale: int = 1
    close_centre: int = 0

    @property
    def size(self) -> int:
        return int(self.open_us.shape[0])

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[Any]], *, segment_keys: Sequence[Any] | None = None) -> BarsV1:
        """From ``T2_ARCHIVE_OHLCV_1M``-shaped rows (bar_open_at, bar_close_at, _, open, high, low, close, ...).

        A new segment starts after a missing UTC day and, when ``segment_keys``
        is given (R8: a live T4 capture session and normalizer generation per
        bar), wherever the key changes -- so no window ever spans a capture gap.
        """
        if not rows:
            raise StrategySdkError("no_bars")
        if segment_keys is not None and len(segment_keys) != len(rows):
            raise StrategySdkError("segment_keys_must_match_rows")
        open_us: np.ndarray = np.fromiter((_micros(row[0]) for row in rows), dtype=np.int64, count=len(rows))
        close_us: np.ndarray = np.fromiter((_micros(row[1]) for row in rows), dtype=np.int64, count=len(rows))
        if np.any(np.diff(open_us) <= 0):
            raise StrategySdkError("bars_not_strictly_increasing")
        days: np.ndarray = open_us // _MICROS_PER_DAY
        breaks = np.concatenate(([False], np.diff(days) > 1))
        if segment_keys is not None:
            key_change = np.array([False] + [segment_keys[i] != segment_keys[i - 1] for i in range(1, len(rows))])
            breaks = breaks | key_change
        segment: np.ndarray = np.cumsum(breaks).astype(np.int64)
        starts: np.ndarray = np.zeros(len(rows), dtype=np.int64)
        boundary = np.flatnonzero(np.concatenate(([True], segment[1:] != segment[:-1])))
        starts[boundary] = boundary
        starts = np.maximum.accumulate(starts)
        decimals = [tuple(Decimal(row[k]) for row in rows) for k in (3, 4, 5, 6)]
        scale, centre, ticks = _exact_ticks(decimals[3])
        return cls(
            open_us=open_us, close_us=close_us, segment=segment,
            open_f=np.array([float(v) for v in decimals[0]]), high_f=np.array([float(v) for v in decimals[1]]),
            low_f=np.array([float(v) for v in decimals[2]]), close_f=np.array([float(v) for v in decimals[3]]),
            open_d=decimals[0], high_d=decimals[1], low_d=decimals[2], close_d=decimals[3],
            segment_start=starts, close_ticks=ticks, close_scale=scale, close_centre=centre,
        )

    def bars_in_segment(self) -> np.ndarray:
        """``i - segment_start(i) + 1``: how many bars of the current segment bar ``i`` closes."""
        return np.arange(self.size, dtype=np.int64) - self.segment_start + 1


# ---------------------------------------------------------------------------
# Execution convention (shared by both tiers)
# ---------------------------------------------------------------------------


def execution_indices_v1(bars: BarsV1, lag_us: int) -> np.ndarray:
    """For each bar ``i``, the index of the bar whose open fills its decision, else ``-1``."""
    exec_index: np.ndarray = np.asarray(np.searchsorted(bars.open_us, bars.close_us + lag_us, side="right"))
    valid: np.ndarray = exec_index < bars.size
    same_segment: np.ndarray = np.zeros(bars.size, dtype=bool)
    same_segment[valid] = bars.segment[exec_index[valid]] == bars.segment[valid]
    return np.where(valid & same_segment, exec_index, -1)


def held_positions_v1(bars: BarsV1, targets: Sequence[int] | np.ndarray, lag_us: int) -> list[int]:
    """The position held over each open-to-open interval ``[open_j, open_{j+1})``.

    Pure integer logic, used verbatim by both tiers so the execution rule can
    never differ between search and authority.
    """
    exec_index = execution_indices_v1(bars, lag_us)
    n = bars.size
    pending: dict[int, int] = {}
    for i in range(n):
        j = int(exec_index[i])
        if j >= 0:
            pending[j] = int(targets[i])  # a later decision filling at the same open wins
    held = [0] * n
    current = 0
    segment = bars.segment
    for j in range(n):
        if j == 0 or segment[j] != segment[j - 1]:
            current = 0
        if j in pending:
            current = pending[j]
        last_of_segment = j == n - 1 or segment[j + 1] != segment[j]
        held[j] = 0 if last_of_segment else current
    return held


# ---------------------------------------------------------------------------
# Families
# ---------------------------------------------------------------------------


def _near_tie(lhs: np.ndarray, rhs: np.ndarray | float, mask: np.ndarray) -> int:
    rhs_array = np.broadcast_to(np.asarray(rhs, dtype=float), lhs.shape)
    scale = np.maximum(np.maximum(np.abs(lhs), np.abs(rhs_array)), 1e-300)
    return int(np.count_nonzero(mask & (np.abs(lhs - rhs_array) <= NEAR_TIE_TOL * scale)))


def _hold_events(events: np.ndarray, bars: BarsV1, ready: np.ndarray) -> np.ndarray:
    """Forward-fill events (NaN = keep) within segments; flat before any event or warmup."""
    filled = np.where(ready, events, np.nan)
    starts = bars.segment_start
    filled[np.flatnonzero(np.isnan(filled) & (np.arange(bars.size) == starts))] = 0.0
    index = np.where(~np.isnan(filled), np.arange(bars.size), 0)
    np.maximum.accumulate(index, out=index)
    out = filled[index]
    out[~ready] = 0.0
    return np.nan_to_num(out).astype(np.int64)


def _rolling_extreme_prior_f64(values: np.ndarray, window: int, *, maximum: bool) -> np.ndarray:
    """Extreme over the *prior* ``window`` bars ``values[i-window..i-1]`` (sparse table)."""
    op = np.maximum if maximum else np.minimum
    n = values.size
    out = np.full(n, np.nan)
    if n <= window:
        return out
    k = math.floor(math.log2(window))
    table = values.copy()
    for level in range(k):
        step = 1 << level
        table = np.concatenate((op(table[:-step], table[step:]), table[-step:]))
    span = 1 << k
    lo: np.ndarray = np.arange(window, n) - window
    hi: np.ndarray = np.arange(window, n) - span
    out[window:] = op(table[lo], table[hi])
    return out


def _decimal_context() -> Any:
    """The OR-3 authoritative arithmetic context (precision 50, half-even)."""
    return localcontext(prec=AUTHORITATIVE_DECIMAL_PRECISION_V1, rounding=ROUND_HALF_EVEN)


class StrategyFamilyV1:
    """Base class: a family's identity, its exact parameter space and both tiers."""

    family: ClassVar[str]
    version: ClassVar[str]

    def parameter_space(self) -> ParameterSpaceV1:
        raise NotImplementedError

    def inadmissible(self, params: Mapping[str, Any]) -> str | None:
        """A reason the point is meaningless (e.g. fast >= slow), else ``None``."""
        return None

    def targets_f64(self, bars: BarsV1, params: Mapping[str, Any]) -> tuple[np.ndarray, int]:
        raise NotImplementedError

    def targets_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> list[int]:
        raise NotImplementedError

    def explain_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> dict[str, str]:
        """The decision quantities at the last bar, exactly, for a human-readable signal reason."""
        return {}

    def spec(self) -> StrategySpecV1:
        # The whole SDK module is on every evaluation path (bars, execution,
        # helpers, metrics), so all of it is identity: any code change is a new
        # study, never a silent resume that mixes results from two codes.
        source = f"{self.family}:{self.version}:" + inspect.getsource(sys.modules[__name__])
        return StrategySpecV1(
            family=self.family, version=self.version,
            implementation_sha256=hashlib.sha256(source.encode()).hexdigest(),
            execution_convention=EXECUTION_CONVENTION_V1,
            input_roles=(InputRoleV1(BARS_ROLE, frozenset({EvidenceTierV1.T2_EVENT_TIME})),),
            parameter_schema={domain.name: domain.kind for domain in self.parameter_space().domains},
        )


def _direction_domain() -> ParameterDomainV1:
    return ParameterDomainV1.categorical_set("direction", ["long_only", "long_short"])


class TrendMovingAverageCrossV1(StrategyFamilyV1):
    """Long (or short) while the fast mean is above (below) the slow mean by more than ``band``.

    ``band`` is a hysteresis band on ``fast/slow - 1``: inside it the previous
    target is kept, so the strategy does not flip on noise around the cross.
    """

    family = "trend_ma_cross"
    version = "1.0.0"

    def parameter_space(self) -> ParameterSpaceV1:
        return ParameterSpaceV1.of(
            ParameterDomainV1.integer_values("fast_bars", [15, 30, 60, 120, 240, 480]),
            ParameterDomainV1.integer_values("slow_bars", [60, 120, 240, 480, 960, 1440, 2880, 5760]),
            ParameterDomainV1.decimal_set("band", ["0", "0.0005", "0.001", "0.002", "0.005"]),
            _direction_domain(),
        )

    def inadmissible(self, params: Mapping[str, Any]) -> str | None:
        return "fast_not_shorter_than_slow" if params["fast_bars"] >= params["slow_bars"] else None

    # Decision rule, in both tiers, without division:
    #   fast/slow - 1 > band  <=>  q*(s*S_f - f*S_s) > p*f*S_s     (band = p/q, S_s > 0)
    # with S the exact window sums of closes; the search tier evaluates it in
    # exact int64 ticks, the authority tier in exact Decimal, so both decide alike.

    def targets_f64(self, bars: BarsV1, params: Mapping[str, Any]) -> tuple[np.ndarray, int]:
        fast, slow = int(params["fast_bars"]), int(params["slow_bars"])
        p, q = _ratio(Decimal(params["band"]))
        short = -1.0 if params["direction"] == "long_short" else 0.0
        ready = bars.bars_in_segment() >= slow
        # Bound every int64 product with Python integers *before* forming it, so
        # no intermediate can wrap unnoticed (PIT review).
        max_tick = int(np.max(np.abs(bars.close_ticks))) if bars.size else 0
        cross_bound = q * 2 * fast * slow * max_tick
        scale_bound = max(p, 1) * fast * slow * (max_tick + abs(bars.close_centre))
        if cross_bound >= 2 ** 62 or scale_bound >= 2 ** 62:
            raise StrategySdkError("trend_cross_products_exceed_the_exact_arithmetic_bound")
        sum_fast = _window_sums(bars.close_ticks, fast)
        sum_slow = _window_sums(bars.close_ticks, slow)
        cross = slow * sum_fast - fast * sum_slow  # = s*raw_f - f*raw_s (the centre cancels)
        scale: np.ndarray = fast * (sum_slow + slow * bars.close_centre)  # = f * raw slow sum > 0
        left: np.ndarray = q * cross
        right: np.ndarray = p * scale
        events = np.full(bars.size, np.nan)
        events[ready & (left > right)] = 1.0
        events[ready & (left < -right)] = short
        ties = int(np.count_nonzero(ready & ((left == right) | (left == -right))))
        return _hold_events(events, bars, ready), ties

    def targets_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> list[int]:
        fast, slow = int(params["fast_bars"]), int(params["slow_bars"])
        p, q = _ratio(Decimal(params["band"]))
        short = -1 if params["direction"] == "long_short" else 0
        out = [0] * bars.size
        with _decimal_context():
            fast_sum = slow_sum = Decimal(0)
            fast_q: deque[Decimal] = deque()
            slow_q: deque[Decimal] = deque()
            state = 0
            for i, close in enumerate(bars.close_d):
                if i == int(bars.segment_start[i]):
                    fast_sum = slow_sum = Decimal(0)
                    fast_q.clear()
                    slow_q.clear()
                    state = 0
                fast_q.append(close)
                slow_q.append(close)
                fast_sum += close
                slow_sum += close
                if len(fast_q) > fast:
                    fast_sum -= fast_q.popleft()
                if len(slow_q) > slow:
                    slow_sum -= slow_q.popleft()
                if len(slow_q) < slow:
                    continue
                left = q * (slow * fast_sum - fast * slow_sum)
                right = p * fast * slow_sum
                if left > right:
                    state = 1
                elif left < -right:
                    state = short
                out[i] = state
        return out

    def explain_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> dict[str, str]:
        fast, slow = int(params["fast_bars"]), int(params["slow_bars"])
        if bars.size < slow:
            return {"rule": "fast/slow mean cross", "state": "warming_up"}
        with _decimal_context():
            fast_mean = sum(bars.close_d[-fast:], Decimal(0)) / fast
            slow_mean = sum(bars.close_d[-slow:], Decimal(0)) / slow
            ratio = fast_mean / slow_mean - 1
        return {"rule": f"long if fast({fast})/slow({slow}) - 1 > band, short if < -band",
                "fast_mean": _q12(fast_mean), "slow_mean": _q12(slow_mean),
                "ratio_minus_one": _q12(ratio), "band": str(params["band"])}


class MeanReversionZScoreV1(StrategyFamilyV1):
    """Fade stretched moves: long below ``-entry_z`` sigmas, short above ``+entry_z``; flat inside ``exit_z``."""

    family = "mean_reversion_z"
    version = "1.0.0"

    def parameter_space(self) -> ParameterSpaceV1:
        return ParameterSpaceV1.of(
            ParameterDomainV1.integer_values("lookback_bars", [30, 60, 120, 240, 480, 1440]),
            ParameterDomainV1.decimal_set("entry_z", ["1.5", "2", "2.5", "3", "3.5"]),
            ParameterDomainV1.decimal_set("exit_z", ["0", "0.25", "0.5", "1"]),
            _direction_domain(),
        )

    def inadmissible(self, params: Mapping[str, Any]) -> str | None:
        return "exit_not_inside_entry" if params["exit_z"] >= params["entry_z"] else None

    # Decision rule, in both tiers, without division or square root. With
    # A = w*x_i - S and N = w*Q - S^2 (S, Q the window sum and sum of squares;
    # z = A / sqrt(N), N > 0 is the variance gate) and thresholds e = p/q:
    #   z <= -e  <=>  A < 0 and q^2*A^2 >= p^2*N      z >= e  <=>  A > 0 and q^2*A^2 >= p^2*N
    #   |z| <= x <=>  q^2*A^2 <= p^2*N   (x = 0: A == 0)
    # A and N are exact integers (search tier, ticks) or exact Decimals (authority).

    def targets_f64(self, bars: BarsV1, params: Mapping[str, Any]) -> tuple[np.ndarray, int]:
        window = int(params["lookback_bars"])
        if window > _MAX_EXACT_VARIANCE_WINDOW:
            raise StrategySdkError("variance_window_exceeds_the_exact_arithmetic_bound")
        ep, eq = _ratio(Decimal(params["entry_z"]))
        xp, xq = _ratio(Decimal(params["exit_z"]))
        short = -1.0 if params["direction"] == "long_short" else 0.0
        ready = bars.bars_in_segment() >= window
        ticks = bars.close_ticks
        total = _window_sums(ticks, window)
        squares = _window_sums(ticks * ticks, window)
        a = window * ticks - total
        n = window * squares - total * total
        live = ready & (n > 0)
        beyond_entry, entry_ties = _exact_scaled_square_compare(a, n, ep, eq, live, ">=")
        inside_exit, exit_ties = _exact_scaled_square_compare(a, n, xp, xq, live, "<=")
        events = np.full(bars.size, np.nan)
        events[live & inside_exit] = 0.0
        events[live & beyond_entry & (a < 0)] = 1.0
        events[live & beyond_entry & (a > 0)] = short
        return _hold_events(events, bars, ready), entry_ties + exit_ties

    def targets_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> list[int]:
        window = int(params["lookback_bars"])
        ep, eq = _ratio(Decimal(params["entry_z"]))
        xp, xq = _ratio(Decimal(params["exit_z"]))
        short = -1 if params["direction"] == "long_short" else 0
        out = [0] * bars.size
        with _decimal_context():
            q: deque[Decimal] = deque()
            total = squares = Decimal(0)
            state = 0
            for i, close in enumerate(bars.close_d):
                if i == int(bars.segment_start[i]):
                    q.clear()
                    total = squares = Decimal(0)
                    state = 0
                q.append(close)
                total += close
                squares += close * close
                if len(q) > window:
                    old = q.popleft()
                    total -= old
                    squares -= old * old
                if len(q) < window:
                    continue
                a = window * close - total
                n = window * squares - total * total
                if n > 0:
                    a2 = a * a
                    if a < 0 and eq * eq * a2 >= ep * ep * n:
                        state = 1
                    elif a > 0 and eq * eq * a2 >= ep * ep * n:
                        state = short
                    elif xq * xq * a2 <= xp * xp * n:
                        state = 0
                out[i] = state
        return out

    def explain_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> dict[str, str]:
        window = int(params["lookback_bars"])
        if bars.size < window:
            return {"rule": "z-score fade", "state": "warming_up"}
        with _decimal_context():
            closes = bars.close_d[-window:]
            mean = sum(closes, Decimal(0)) / window
            variance = sum(((c - mean) ** 2 for c in closes), Decimal(0)) / window
            z = (closes[-1] - mean) / variance.sqrt() if variance > 0 else None
        return {"rule": f"long at z <= -{params['entry_z']}, short at z >= {params['entry_z']}, "
                        f"flat at |z| <= {params['exit_z']} (lookback {window})",
                "close": str(closes[-1]), "mean": _q12(mean), "z": "undefined" if z is None else _q12(z)}


class ChannelBreakoutV1(StrategyFamilyV1):
    """Enter on a close beyond the prior ``entry_bars`` channel; exit on the shorter ``exit_bars`` channel."""

    family = "breakout_channel"
    version = "1.0.0"

    def parameter_space(self) -> ParameterSpaceV1:
        return ParameterSpaceV1.of(
            ParameterDomainV1.integer_values("entry_bars", [60, 120, 240, 480, 960, 1440, 2880]),
            ParameterDomainV1.integer_values("exit_bars", [15, 30, 60, 120, 240, 480]),
            _direction_domain(),
        )

    def inadmissible(self, params: Mapping[str, Any]) -> str | None:
        return "exit_channel_not_shorter" if params["exit_bars"] >= params["entry_bars"] else None

    def explain_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> dict[str, str]:
        return _breakout_explain(bars, params)

    def targets_f64(self, bars: BarsV1, params: Mapping[str, Any]) -> tuple[np.ndarray, int]:
        n_entry, n_exit = int(params["entry_bars"]), int(params["exit_bars"])
        allow_short = params["direction"] == "long_short"
        ready = bars.bars_in_segment() > n_entry
        upper = _rolling_extreme_prior_f64(bars.high_f, n_entry, maximum=True)
        lower = _rolling_extreme_prior_f64(bars.low_f, n_entry, maximum=False)
        exit_low = _rolling_extreme_prior_f64(bars.low_f, n_exit, maximum=False)
        exit_high = _rolling_extreme_prior_f64(bars.high_f, n_exit, maximum=True)
        close = bars.close_f
        long_entry = ready & (close > upper)
        short_entry = ready & (close < lower) & allow_short
        long_exit = ready & (close < exit_low)
        short_exit = ready & (close > exit_high)
        ties = (_near_tie(close, upper, ready) + _near_tie(close, lower, ready)
                + _near_tie(close, exit_low, ready) + _near_tie(close, exit_high, ready))
        # Stateful (an exit depends on the held side): walk the event bars and the
        # segment starts only, filling the state between them.
        starts = np.flatnonzero(np.arange(bars.size) == bars.segment_start)
        marks = np.union1d(starts, np.flatnonzero(long_entry | short_entry | long_exit | short_exit))
        is_start: np.ndarray = np.zeros(bars.size, dtype=bool)
        is_start[starts] = True
        targets: np.ndarray = np.zeros(bars.size, dtype=np.int64)
        state = 0
        for position, i in enumerate(marks):
            if is_start[i]:
                state = 0
            if ready[i]:
                state = _breakout_step(state, bool(long_entry[i]), bool(short_entry[i]),
                                       bool(long_exit[i]), bool(short_exit[i]))
            end = int(marks[position + 1]) if position + 1 < marks.size else bars.size
            targets[int(i):end] = state
        targets[~ready] = 0
        return targets, ties

    def targets_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> list[int]:
        n_entry, n_exit = int(params["entry_bars"]), int(params["exit_bars"])
        allow_short = params["direction"] == "long_short"
        out = [0] * bars.size
        state = 0
        windows = {
            "upper": _PriorExtreme(bars.high_d, n_entry, maximum=True),
            "lower": _PriorExtreme(bars.low_d, n_entry, maximum=False),
            "exit_low": _PriorExtreme(bars.low_d, n_exit, maximum=False),
            "exit_high": _PriorExtreme(bars.high_d, n_exit, maximum=True),
        }
        for i in range(bars.size):
            start = int(bars.segment_start[i])
            if i == start:
                state = 0
                for window in windows.values():
                    window.reset(start)
            # Each window covers exactly bars [i-N, i-1] of this segment.
            for window in windows.values():
                window.advance_to(i)
            if i - start < n_entry:
                continue
            close = bars.close_d[i]
            state = _breakout_step(state, close > windows["upper"].value(),
                                   allow_short and close < windows["lower"].value(),
                                   close < windows["exit_low"].value(), close > windows["exit_high"].value())
            out[i] = state
        return out


class _PriorExtreme:
    """Exact running max/min over the prior ``window`` values (monotonic deque, O(n))."""

    def __init__(self, values: Sequence[Decimal], window: int, *, maximum: bool) -> None:
        self._values = values
        self._window = window
        self._maximum = maximum
        self._queue: deque[int] = deque()
        self._next = 0

    def reset(self, start: int) -> None:
        """A new segment begins at ``start``: nothing before it may enter the window."""
        self._queue.clear()
        self._next = start

    def advance_to(self, i: int) -> None:
        """Make the window ``[i - window, i - 1]`` (the bar ``i`` itself is never included)."""
        self._next = max(self._next, i - self._window)
        while self._next < i:
            value = self._values[self._next]
            while self._queue and (
                self._values[self._queue[-1]] <= value if self._maximum else self._values[self._queue[-1]] >= value
            ):
                self._queue.pop()
            self._queue.append(self._next)
            self._next += 1
        while self._queue and self._queue[0] < i - self._window:
            self._queue.popleft()

    def value(self) -> Decimal:
        return self._values[self._queue[0]]


def _q12(value: Decimal) -> str:
    return format(value.quantize(Decimal("1E-12")), "f")


def _breakout_explain(bars: BarsV1, params: Mapping[str, Any]) -> dict[str, str]:
    n_entry, n_exit = int(params["entry_bars"]), int(params["exit_bars"])
    if bars.size <= n_entry:
        return {"rule": "channel breakout", "state": "warming_up"}
    return {"rule": f"long above prior {n_entry}-bar high, short below its low; exit on the {n_exit}-bar channel",
            "close": str(bars.close_d[-1]), "prior_high": str(max(bars.high_d[-n_entry - 1:-1])),
            "prior_low": str(min(bars.low_d[-n_entry - 1:-1]))}


def _breakout_step(state: int, long_entry: bool, short_entry: bool, long_exit: bool, short_exit: bool) -> int:
    if long_entry:
        return 1
    if short_entry:
        return -1
    if state == 1 and long_exit:
        return 0
    if state == -1 and short_exit:
        return 0
    return state


FAMILIES_V1: Final[dict[str, StrategyFamilyV1]] = {
    family.family: family
    for family in (TrendMovingAverageCrossV1(), MeanReversionZScoreV1(), ChannelBreakoutV1())
}


# ---------------------------------------------------------------------------
# Metrics (both tiers share the accounting definitions)
# ---------------------------------------------------------------------------


def _text(value: float | None) -> str | None:
    if value is None or not math.isfinite(value):
        return None
    return format(value, ".12g")


def search_metrics_f64(bars: BarsV1, held: Sequence[int], *, near_ties: int) -> dict[str, Any]:
    """Gross float64 SEARCH metrics. Text values; undefined metrics are ``None``."""
    h: np.ndarray = np.asarray(held, dtype=float)
    n = bars.size
    r = np.zeros(n)
    r[:-1] = bars.open_f[1:] / bars.open_f[:-1] - 1.0
    s = h * r
    changes = np.abs(np.diff(np.concatenate(([0.0], h))))
    turnover = float(changes.sum())
    trades = int(np.count_nonzero(changes))
    equity: np.ndarray = np.cumprod(1.0 + s)
    peak = np.maximum.accumulate(equity)
    drawdown = float(np.max(1.0 - equity / peak)) if n else 0.0
    days: np.ndarray = bars.open_us // _MICROS_PER_DAY
    _, day_index = np.unique(days, return_inverse=True)
    daily = np.exp(np.bincount(day_index, weights=np.log1p(s))) - 1.0
    sharpe = None
    if daily.size > 1 and float(np.std(daily, ddof=1)) > 0:
        sharpe = float(np.mean(daily) / np.std(daily, ddof=1) * math.sqrt(365.0))
    months: np.ndarray = (days.astype("datetime64[D]").astype("datetime64[M]")).astype(np.int64)
    _, month_index = np.unique(months, return_inverse=True)
    monthly = np.exp(np.bincount(month_index, weights=np.log1p(s))) - 1.0
    gross_sum = float(s.sum())
    crossings = _funding_crossings(bars, h)
    return {
        "numeric_tier": NUMERIC_TIER_SEARCH,
        "cost_mode": "GROSS_NON_PROMOTABLE",
        "total_return": _text(float(equity[-1] - 1.0)),
        "sharpe_daily_annualized": _text(sharpe),
        "max_drawdown": _text(drawdown),
        "trades": trades,
        "turnover": _text(turnover),
        "exposure": _text(float(np.mean(np.abs(h)))),
        "break_even_bps_per_side": _text(gross_sum / turnover * 1e4) if turnover > 0 else None,
        "positive_month_fraction": _text(float(np.mean(monthly > 0))) if monthly.size else None,
        "worst_month_return": _text(float(np.min(monthly))) if monthly.size else None,
        "funding_window_crossings": crossings,
        "near_tie_decisions": near_ties,
        "bars_evaluated": n,
    }


def _funding_crossings(bars: BarsV1, held: np.ndarray) -> int:
    """Held intervals spanning a 00/08/16 UTC boundary (diagnostic only; funding is not modelled)."""
    if bars.size < 2:
        return 0
    start = bars.open_us[:-1]
    end = bars.open_us[1:]
    crosses = (end // _FUNDING_GRID_MICROS) > (start // _FUNDING_GRID_MICROS)
    return int(np.count_nonzero(crosses & (held[:-1] != 0)))


# ---------------------------------------------------------------------------
# The R4.2 evaluator
# ---------------------------------------------------------------------------


_BARS_CACHE: dict[tuple[str, str], BarsV1] = {}


def load_bars_v1(data_root: Any, dataset_version_id: Any, *, expected_content_hash: str) -> BarsV1:
    """Load (and cache per process) a verified research bar window as both tiers."""
    from .public_archive_research_bars_v1 import load_research_bar_dataset_v1
    from .research_data_plane_v1 import ResearchFrameStoreV1

    key = (str(dataset_version_id), expected_content_hash)
    cached = _BARS_CACHE.get(key)
    if cached is not None:
        return cached
    store = ResearchFrameStoreV1(data_root)
    dataset = load_research_bar_dataset_v1(store, dataset_version_id)
    if dataset.content_hash != expected_content_hash:
        raise StrategySdkError("bound_dataset_content_hash_mismatch")
    if dataset.identity.get("evidence_tier") != EvidenceTierV1.T2_EVENT_TIME.value:
        raise StrategySdkError("bound_dataset_is_not_t2_research_bars")
    rows = list(store.iter_rows(store.load_manifest(dataset.bar_frame_manifest_hash)))
    bars = BarsV1.from_rows(rows)
    _BARS_CACHE[key] = bars
    return bars


def restrict_to_bound_v1(bars: BarsV1, bound_exclusive: datetime, lag: timedelta) -> BarsV1:
    """Only bars whose information is known (close + lag) strictly before ``bound_exclusive``."""
    limit = _micros(bound_exclusive) - lag // timedelta(microseconds=1)
    keep = int(np.searchsorted(bars.close_us, limit, side="left"))
    if keep == bars.size:
        return bars
    if keep == 0:
        raise StrategySdkError("no_bar_inside_the_evaluation_bound")
    sl = slice(0, keep)
    return BarsV1(
        open_us=bars.open_us[sl], close_us=bars.close_us[sl], segment=bars.segment[sl],
        open_f=bars.open_f[sl], high_f=bars.high_f[sl], low_f=bars.low_f[sl], close_f=bars.close_f[sl],
        open_d=bars.open_d[:keep], high_d=bars.high_d[:keep], low_d=bars.low_d[:keep],
        close_d=bars.close_d[:keep], segment_start=bars.segment_start[sl],
        close_ticks=bars.close_ticks[sl], close_scale=bars.close_scale, close_centre=bars.close_centre,
    )


@dataclass(frozen=True)
class BarStrategyEvaluatorV1:
    """Picklable :class:`~trade_platform.strategy_lab_worker_v1.TrialEvaluatorV1` for SDK families."""

    data_root: Any

    def __call__(self, study: StudySpecV1, parameters: Mapping[str, Any]) -> TrialEvaluationV1:
        family = FAMILIES_V1.get(study.strategy.family)
        if family is None or family.spec() != study.strategy:
            raise StrategySdkError("study_strategy_is_not_this_sdk_family_version")
        reason = family.inadmissible(parameters)
        if reason is not None:
            return TrialEvaluationV1(TrialOutcomeV1.INADMISSIBLE_PARAMETERS, {"reason": reason})
        lag = study.timing_lag
        if lag is None:
            raise StrategySdkError("t2_study_without_the_or_5_timing_policy")
        (binding,) = study.datasets
        bars = load_bars_v1(self.data_root, binding.dataset_version_id, expected_content_hash=binding.content_hash)
        bars = restrict_to_bound_v1(bars, study.evaluation_upper_bound_exclusive, lag)
        targets, ties = family.targets_f64(bars, parameters)
        held = held_positions_v1(bars, targets, lag // timedelta(microseconds=1))
        return TrialEvaluationV1(TrialOutcomeV1.EVALUATED, search_metrics_f64(bars, held, near_ties=ties))


def day_of(micros: int) -> date:
    return (_EPOCH + timedelta(microseconds=micros)).date()


__all__ = [
    "BARS_ROLE",
    "EXECUTION_CONVENTION_V1",
    "FAMILIES_V1",
    "BarStrategyEvaluatorV1",
    "BarsV1",
    "ChannelBreakoutV1",
    "MeanReversionZScoreV1",
    "StrategyFamilyV1",
    "StrategySdkError",
    "TrendMovingAverageCrossV1",
    "execution_indices_v1",
    "held_positions_v1",
    "load_bars_v1",
    "restrict_to_bound_v1",
    "search_metrics_f64",
]
