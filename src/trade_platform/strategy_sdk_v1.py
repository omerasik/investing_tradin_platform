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
  invented costs.

Two numeric tiers (owner decision OR-3)
---------------------------------------
:meth:`StrategyFamilyV1.targets_f64` is the float64 SEARCH tier (vectorized
with numpy). :meth:`StrategyFamilyV1.targets_decimal` is the authoritative tier,
a straightforward ``Decimal`` implementation of the same rule over the same
bars. Every decision comparison also reports a near-tie count: comparisons
whose two sides agree within the OR-3 relative tolerance, where float rounding
could have flipped the decision. A flagged candidate joins the rerun set.
"""

from __future__ import annotations

import hashlib
import inspect
import math
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

    @property
    def size(self) -> int:
        return int(self.open_us.shape[0])

    @classmethod
    def from_rows(cls, rows: Sequence[Sequence[Any]]) -> BarsV1:
        """From ``T2_ARCHIVE_OHLCV_1M`` rows (bar_open_at, bar_close_at, _, open, high, low, close, ...)."""
        if not rows:
            raise StrategySdkError("no_bars")
        open_us: np.ndarray = np.fromiter((_micros(row[0]) for row in rows), dtype=np.int64, count=len(rows))
        close_us: np.ndarray = np.fromiter((_micros(row[1]) for row in rows), dtype=np.int64, count=len(rows))
        if np.any(np.diff(open_us) <= 0):
            raise StrategySdkError("bars_not_strictly_increasing")
        days: np.ndarray = open_us // _MICROS_PER_DAY
        breaks = np.concatenate(([False], np.diff(days) > 1))
        segment: np.ndarray = np.cumsum(breaks).astype(np.int64)
        starts: np.ndarray = np.zeros(len(rows), dtype=np.int64)
        boundary = np.flatnonzero(np.concatenate(([True], segment[1:] != segment[:-1])))
        starts[boundary] = boundary
        starts = np.maximum.accumulate(starts)
        decimals = [tuple(Decimal(row[k]) for row in rows) for k in (3, 4, 5, 6)]
        return cls(
            open_us=open_us, close_us=close_us, segment=segment,
            open_f=np.array([float(v) for v in decimals[0]]), high_f=np.array([float(v) for v in decimals[1]]),
            low_f=np.array([float(v) for v in decimals[2]]), close_f=np.array([float(v) for v in decimals[3]]),
            open_d=decimals[0], high_d=decimals[1], low_d=decimals[2], close_d=decimals[3],
            segment_start=starts,
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


def _rolling_mean_f64(values: np.ndarray, window: int) -> np.ndarray:
    """Trailing mean over ``values[i-window+1..i]`` (NaN where the window is incomplete overall)."""
    out = np.full(values.shape, np.nan)
    if values.size < window:
        return out
    shift = values[0]
    csum = np.concatenate(([0.0], np.cumsum(values - shift)))
    out[window - 1:] = (csum[window:] - csum[:-window]) / window + shift
    return out


def _rolling_std_f64(values: np.ndarray, window: int, mean: np.ndarray) -> np.ndarray:
    out = np.full(values.shape, np.nan)
    if values.size < window:
        return out
    shift = values[0]
    centred = values - shift
    csum2 = np.concatenate(([0.0], np.cumsum(centred * centred)))
    second = (csum2[window:] - csum2[:-window]) / window
    m = mean[window - 1:] - shift
    out[window - 1:] = np.sqrt(np.maximum(second - m * m, 0.0))
    return out


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

    def spec(self) -> StrategySpecV1:
        source = inspect.getsource(type(self)) + inspect.getsource(held_positions_v1)
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

    def targets_f64(self, bars: BarsV1, params: Mapping[str, Any]) -> tuple[np.ndarray, int]:
        fast, slow = int(params["fast_bars"]), int(params["slow_bars"])
        band = float(params["band"])
        short = -1.0 if params["direction"] == "long_short" else 0.0
        ready = bars.bars_in_segment() >= slow
        ratio = _rolling_mean_f64(bars.close_f, fast) / _rolling_mean_f64(bars.close_f, slow) - 1.0
        events = np.full(bars.size, np.nan)
        events[ratio > band] = 1.0
        events[ratio < -band] = short
        ties = _near_tie(ratio, band, ready) + _near_tie(ratio, -band, ready)
        return _hold_events(events, bars, ready), ties

    def targets_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> list[int]:
        fast, slow = int(params["fast_bars"]), int(params["slow_bars"])
        band = Decimal(params["band"])
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
                ratio = (fast_sum / fast) / (slow_sum / slow) - 1
                if ratio > band:
                    state = 1
                elif ratio < -band:
                    state = short
                out[i] = state
        return out


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

    def targets_f64(self, bars: BarsV1, params: Mapping[str, Any]) -> tuple[np.ndarray, int]:
        window = int(params["lookback_bars"])
        entry, exit_ = float(params["entry_z"]), float(params["exit_z"])
        short = -1.0 if params["direction"] == "long_short" else 0.0
        ready = bars.bars_in_segment() >= window
        mean = _rolling_mean_f64(bars.close_f, window)
        std = _rolling_std_f64(bars.close_f, window, mean)
        with np.errstate(divide="ignore", invalid="ignore"):
            z = np.where(std > 0, (bars.close_f - mean) / std, np.nan)
        live = ready & ~np.isnan(z)
        events = np.full(bars.size, np.nan)
        events[live & (np.abs(z) <= exit_)] = 0.0
        events[live & (z <= -entry)] = 1.0
        events[live & (z >= entry)] = short
        ties = (_near_tie(z, -entry, live) + _near_tie(z, entry, live)
                + _near_tie(np.abs(z), exit_, live))
        return _hold_events(events, bars, ready), ties

    def targets_decimal(self, bars: BarsV1, params: Mapping[str, Any]) -> list[int]:
        window = int(params["lookback_bars"])
        entry, exit_ = Decimal(params["entry_z"]), Decimal(params["exit_z"])
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
                mean = total / window
                variance = squares / window - mean * mean
                if variance > 0:
                    z = (close - mean) / variance.sqrt()
                    if z <= -entry:
                        state = 1
                    elif z >= entry:
                        state = short
                    elif abs(z) <= exit_:
                        state = 0
                out[i] = state
        return out


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
        for i in range(bars.size):
            start = int(bars.segment_start[i])
            if i == start:
                state = 0
            if i - start < n_entry:
                continue
            close = bars.close_d[i]
            upper = max(bars.high_d[i - n_entry:i])
            lower = min(bars.low_d[i - n_entry:i])
            exit_low = min(bars.low_d[i - n_exit:i])
            exit_high = max(bars.high_d[i - n_exit:i])
            state = _breakout_step(state, close > upper, allow_short and close < lower,
                                   close < exit_low, close > exit_high)
            out[i] = state
        return out


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
