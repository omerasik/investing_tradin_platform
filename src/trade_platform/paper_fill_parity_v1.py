"""Live/replay paper-fill parity -- a read-only, content-addressed PASS / FAIL / INCOMPLETE assessment.

``RESEARCH_ONLY`` / paper only. Nothing here opens an account, sizes a position,
routes an order or promotes a candidate: the result is evidence for the OR-7
promotion rule "live/replay parity", never the decision.

What is compared
----------------
The *live* side is what the forward path recorded: the completed bars the R8
runner was fed (:class:`~trade_platform.live_signals_v1.LiveBarV1`), the R8
signals it emitted and the R10 paper fills taken from them. The *replay* side is
reconstructed here from sealed T4 evidence (:class:`SealedReplayBarV1`: a bar of
a sealed segment plus that seal's content hash) by calling the unchanged R8
runner and R10 engine -- the same signal and fill semantics, never a copy:

* every replay bar is fed to :class:`~trade_platform.live_signals_v1.LiveStrategyRunnerV1`
  with a clock at the epoch, so nothing is warm-up and each decision instant is
  the sealed bar's finalized, bracketed ``complete_market_knowledge_micros``;
* fills follow :class:`~trade_platform.paper_incubation_v1.PaperIncubationEngineV1`
  in live order (a bar first resolves pending fills, then adds its signals).

Knowledge time is two facts, never one. The live decision and the live bars
carry a *provisional* bound (``LIVE_PROVISIONAL``: no later clock sample yet);
the sealed replay carries the *finalized* bracketed one. Both are recorded on
every observation and neither is compared for equality -- what must agree is
the consequence: the fill minute each side derives from its own decision.

Observations (one per bar, per signal and per live-sequence fault)
-------------------------------------------------------------------
Each is ``MATCH``, ``DIVERGED`` or ``UNAVAILABLE`` with exact reason codes,
content-addressed over its facts. No tolerance: prices, quantities and
references compare exactly; no price is ever inferred.

* **Bars** (within the live observation window of each symbol): every field of
  the bar except knowledge times. A sealed bar the live feed never produced
  inside its window is a live capture gap (``BAR_ABSENT_FROM_LIVE``); a live bar
  absent from the seal is ``BAR_ABSENT_FROM_SEALED_REPLAY`` -- or, after the last
  sealed bar, ``REPLAY_BAR_NOT_SEALED`` (unavailable).
* **Signals**, keyed by (symbol, study, trial, signal bar), across eight
  dimensions: ``candidate`` (identity, authority, holdout, policy slots),
  ``signal`` (explanation and bar/window provenance), ``target``,
  ``timestamp`` (the fill minute derived from each side's decision),
  ``fill_eligibility`` (R10 status: an unobserved fill bar and a
  sequence-ambiguous open are statuses, never filled), ``signed_exposure``,
  ``price`` and ``provenance`` (fill bar session, first-trade reference, trade
  manifest, cost policy, fill rule). A fill missing on one side makes its
  dimensions ``UNAVAILABLE``, never a match.
* A replay signal without a live one is a divergence only where the live runner
  provably ran: the next live signal of the candidate names the previous one
  (``previous_signal_id``). If that next signal starts a new chain the runner
  restarted in between and its warm-up emitted nothing (``LIVE_RUNNER_RESTART_GAP``);
  with no later live signal continuity is unproven; outside the live window the
  bar was not observed. All three are ``UNAVAILABLE``.
* Live sequence faults: a signal naming a predecessor that is not the
  immediately preceding live signal of its candidate (``DIVERGED``) or one not
  supplied (``UNAVAILABLE``); two live signals or fills for one key; a fill whose
  signal hash disagrees or whose signal was not supplied.

Assessment
----------
``FAIL`` if any observation diverged; otherwise ``INCOMPLETE`` if any is
unavailable or no signal matched with a ``FILLED`` fill on both sides; otherwise
``PASS``. The reasons are the sorted, distinct codes. Evidence that does not
re-derive its own hash is refused outright (:class:`PaperFillParityError`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from .first_party_t4_normalization_v1 import T4MinuteBarV1
from .live_signals_v1 import _NAMESPACE as _LIVE_SIGNAL_NAMESPACE
from .live_signals_v1 import (
    AUTHORITY_INCUBATING,
    LiveBarV1,
    LiveHoldoutGateV1,
    LiveSignalV1,
    LiveStrategyRunnerV1,
    WatchedCandidateV1,
)
from .paper_incubation_report_v1 import FILL_STATUS_FILLED, _micros
from .paper_incubation_v1 import (
    PaperFillV1,
    PaperIncubationEngineV1,
    PendingSignalV1,
    expected_fill_bar_open_micros_v1,
)
from .strategy_lab_policies_v1 import CostPolicyV1
from .strategy_lab_study_v1 import identity_hash_v1

FILL_PARITY_SCHEMA_VERSION_V1: Final = "paper-fill-parity-v1"
FILL_PARITY_OBSERVATION_SCHEMA_VERSION_V1: Final = "paper-fill-parity-observation-v1"

OUTCOME_MATCH: Final = "MATCH"
OUTCOME_DIVERGED: Final = "DIVERGED"
OUTCOME_UNAVAILABLE: Final = "UNAVAILABLE"

ASSESSMENT_PASS: Final = "PASS"
ASSESSMENT_FAIL: Final = "FAIL"
ASSESSMENT_INCOMPLETE: Final = "INCOMPLETE"

KIND_BAR: Final = "BAR"
KIND_SIGNAL: Final = "SIGNAL"
KIND_LIVE_SEQUENCE: Final = "LIVE_SEQUENCE"

SIGNAL_DIMENSIONS_V1: Final = ("candidate", "signal", "target", "timestamp", "fill_eligibility",
                               "signed_exposure", "price", "provenance")
_FILL_DIMENSIONS: Final = ("fill_eligibility", "signed_exposure", "price", "provenance")

# Unavailable reasons (live observation or sealed evidence missing: never a match).
LIVE_NOT_OBSERVED_AT_BAR: Final = "LIVE_NOT_OBSERVED_AT_BAR"
LIVE_RUNNER_RESTART_GAP: Final = "LIVE_RUNNER_RESTART_GAP"
LIVE_CONTINUITY_UNPROVEN_AFTER_LAST_SIGNAL: Final = "LIVE_CONTINUITY_UNPROVEN_AFTER_LAST_SIGNAL"
LIVE_FILL_BAR_NOT_YET_OBSERVED: Final = "LIVE_FILL_BAR_NOT_YET_OBSERVED"
LIVE_FILL_NOT_RECORDED: Final = "LIVE_FILL_NOT_RECORDED"
LIVE_SIGNAL_PREDECESSOR_NOT_SUPPLIED: Final = "LIVE_SIGNAL_PREDECESSOR_NOT_SUPPLIED"
LIVE_FILL_SIGNAL_NOT_SUPPLIED: Final = "LIVE_FILL_SIGNAL_NOT_SUPPLIED"
REPLAY_BAR_NOT_SEALED: Final = "REPLAY_BAR_NOT_SEALED"
REPLAY_FILL_BAR_NOT_SEALED: Final = "REPLAY_FILL_BAR_NOT_SEALED"
NO_MATCHED_FILLED_OBSERVATION: Final = "NO_MATCHED_FILLED_OBSERVATION"

# Divergence reasons.
BAR_DIVERGED: Final = "BAR_DIVERGED"
BAR_ABSENT_FROM_LIVE: Final = "BAR_ABSENT_FROM_LIVE"
BAR_ABSENT_FROM_SEALED_REPLAY: Final = "BAR_ABSENT_FROM_SEALED_REPLAY"
SIGNAL_ABSENT_LIVE: Final = "SIGNAL_ABSENT_LIVE"
SIGNAL_ABSENT_REPLAY: Final = "SIGNAL_ABSENT_REPLAY"
LIVE_SIGNAL_CHAIN_ORDER_MISMATCH: Final = "LIVE_SIGNAL_CHAIN_ORDER_MISMATCH"
DUPLICATE_LIVE_SIGNAL: Final = "DUPLICATE_LIVE_SIGNAL"
DUPLICATE_LIVE_FILL: Final = "DUPLICATE_LIVE_FILL"
LIVE_FILL_SIGNAL_HASH_MISMATCH: Final = "LIVE_FILL_SIGNAL_HASH_MISMATCH"

_NAMESPACE: Final = uuid5(NAMESPACE_URL, "trade_platform.paper_fill_parity_v1")
_BAR_FIELDS: Final = ("open_price", "high_price", "low_price", "close_price", "base_volume", "quote_turnover",
                      "trade_count", "block_trade_count", "rpi_trade_count", "first_trade_reference",
                      "last_trade_reference", "open_is_sequence_ambiguous", "close_is_sequence_ambiguous",
                      "closing_record_hash", "trade_manifest_hash")


class PaperFillParityError(ValueError):
    """Raised when parity evidence is malformed or does not re-derive its own identity."""


@dataclass(frozen=True, slots=True)
class SealedReplayBarV1:
    """One bar of a sealed T4 segment, bound to that seal's content hash.

    ``segment`` groups bars the way the live feed's continuity does (a rolling
    window never crosses it); the caller derives it from the sealed coverage.
    """

    symbol: str
    segment: int
    session_id: UUID
    bar: T4MinuteBarV1
    seal_content_hash: str

    def __post_init__(self) -> None:
        if not self.seal_content_hash:
            raise PaperFillParityError("replay_bar_requires_its_seal_content_hash")

    def as_runner_bar(self) -> LiveBarV1:
        """The R8 runner's bar shape, completed at the sealed (finalized) knowledge time, bound 0."""
        return LiveBarV1(self.symbol, self.segment, self.session_id, self.bar,
                         self.bar.complete_market_knowledge_micros * 1000, 0)


@dataclass(frozen=True, slots=True)
class FillParityObservationV1:
    identity: Mapping[str, Any]
    observation_id: UUID
    content_hash: str

    @property
    def outcome(self) -> str:
        return str(self.identity["outcome"])

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(self.identity["reasons"])


@dataclass(frozen=True, slots=True)
class FillParityReportV1:
    identity: Mapping[str, Any]
    report_id: UUID
    content_hash: str
    observations: tuple[FillParityObservationV1, ...]

    @property
    def status(self) -> str:
        return str(self.identity["assessment"]["status"])

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(self.identity["assessment"]["reasons"])


def _observation(kind: str, key: Mapping[str, Any], outcome: str, reasons: Iterable[str],
                 facts: Mapping[str, Any]) -> FillParityObservationV1:
    identity = {"schema_version": FILL_PARITY_OBSERVATION_SCHEMA_VERSION_V1, "kind": kind, "key": dict(key),
                "outcome": outcome, "reasons": sorted(set(reasons)), "facts": dict(facts)}
    content_hash = identity_hash_v1(identity)
    return FillParityObservationV1(identity, uuid5(_NAMESPACE, f"observation:{content_hash}"), content_hash)


def _value(value: object) -> object:
    return format(value, "f") if isinstance(value, Decimal) else value


# ---------------------------------------------------------------------------
# Evidence intake
# ---------------------------------------------------------------------------


def _verified_signal(signal: LiveSignalV1) -> str:
    content_hash = identity_hash_v1(dict(signal.identity))
    if uuid5(_LIVE_SIGNAL_NAMESPACE, f"live-signal:{content_hash}") != signal.signal_id:
        raise PaperFillParityError("live_signal_identity_does_not_rederive")
    return content_hash


def _verified_fill(fill: PaperFillV1) -> None:
    if identity_hash_v1(dict(fill.identity)) != fill.content_hash:
        raise PaperFillParityError("live_fill_identity_does_not_rederive")


def _signal_key(signal: LiveSignalV1) -> tuple[str, str, str, int]:
    identity = signal.identity
    return (str(identity["symbol"]), str(identity["candidate"]["study_id"]), str(identity["candidate"]["trial_id"]),
            int(identity["evidence"]["bar_open_micros"]))


def _key_payload(key: tuple[str, str, str, int]) -> dict[str, Any]:
    return {"symbol": key[0], "study_id": key[1], "trial_id": key[2], "signal_bar_open_micros": key[3]}


def _unique_bars(bars: Iterable[tuple[str, T4MinuteBarV1]], side: str) -> dict[tuple[str, int], T4MinuteBarV1]:
    out: dict[tuple[str, int], T4MinuteBarV1] = {}
    for symbol, bar in bars:
        key = (symbol, bar.bar_open_micros)
        if key in out:
            raise PaperFillParityError(f"{side}_bars_repeat_a_minute")
        out[key] = bar
    return out


def _replay(candidates: Sequence[WatchedCandidateV1], gate: LiveHoldoutGateV1, cost_policy: CostPolicyV1,
            bars: Sequence[SealedReplayBarV1]) -> tuple[list[LiveSignalV1], list[PaperFillV1]]:
    """R8 signals and R10 fills over the sealed bars, through the unchanged engines."""
    runner = LiveStrategyRunnerV1(candidates, holdout_gate=gate, clock=lambda: datetime(1970, 1, 1, tzinfo=UTC))
    engine = PaperIncubationEngineV1(cost_policy)
    signals: list[LiveSignalV1] = []
    fills: list[PaperFillV1] = []
    for item in sorted(bars, key=lambda b: (b.bar.bar_open_micros, b.symbol)):
        bar = item.as_runner_bar()
        fills.extend(engine.on_bars([bar]))
        emitted = runner.on_bars([bar])
        signals.extend(emitted)
        engine.add(PendingSignalV1.from_signal(signal) for signal in emitted)
    return signals, fills


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _bar_observations(live: Mapping[tuple[str, int], T4MinuteBarV1], live_sessions: Mapping[tuple[str, int], UUID],
                      replay: Mapping[tuple[str, int], SealedReplayBarV1],
                      windows: Mapping[str, tuple[int, int]]) -> list[FillParityObservationV1]:
    last_sealed: dict[str, int] = {}
    for symbol, opened in replay:
        last_sealed[symbol] = max(opened, last_sealed.get(symbol, opened))
    keys = set(live) | {key for key in replay if key[0] in windows
                        and windows[key[0]][0] <= key[1] <= windows[key[0]][1]}
    out = []
    for key in sorted(keys):
        payload = {"symbol": key[0], "bar_open_micros": key[1]}
        live_bar, sealed = live.get(key), replay.get(key)
        knowledge: dict[str, Any] = {
            "live_provisional": None if live_bar is None else {
                "open_market_knowledge_micros": live_bar.open_market_knowledge_micros,
                "complete_market_knowledge_micros": live_bar.complete_market_knowledge_micros},
            "sealed_finalized": None if sealed is None else {
                "seal_content_hash": sealed.seal_content_hash,
                "open_market_knowledge_micros": sealed.bar.open_market_knowledge_micros,
                "complete_market_knowledge_micros": sealed.bar.complete_market_knowledge_micros},
        }
        if live_bar is None:
            out.append(_observation(KIND_BAR, payload, OUTCOME_DIVERGED, [BAR_ABSENT_FROM_LIVE],
                                    {"knowledge": knowledge}))
            continue
        if sealed is None:
            beyond = key[1] > last_sealed.get(key[0], -1)
            out.append(_observation(KIND_BAR, payload, OUTCOME_UNAVAILABLE if beyond else OUTCOME_DIVERGED,
                                    [REPLAY_BAR_NOT_SEALED if beyond else BAR_ABSENT_FROM_SEALED_REPLAY],
                                    {"knowledge": knowledge}))
            continue
        live_values = {name: getattr(live_bar, name) for name in _BAR_FIELDS} | {"session_id": live_sessions[key]}
        sealed_values = {name: getattr(sealed.bar, name) for name in _BAR_FIELDS} | {"session_id": sealed.session_id}
        differing = sorted(name for name in live_values if live_values[name] != sealed_values[name])
        facts = {"knowledge": knowledge, "differing_fields": differing,
                 "live": {name: _value(live_values[name]) if name != "session_id" else str(live_values[name])
                          for name in differing},
                 "sealed": {name: _value(sealed_values[name]) if name != "session_id" else str(sealed_values[name])
                            for name in differing}}
        out.append(_observation(KIND_BAR, payload, OUTCOME_DIVERGED if differing else OUTCOME_MATCH,
                                [BAR_DIVERGED] if differing else [], facts))
    return out


def _candidate_facts(identity: Mapping[str, Any]) -> dict[str, Any]:
    return {"candidate": identity["candidate"], "authority": identity["authority"],
            "authority_evidence_hash": identity["authority_evidence_hash"], "holdout": identity["holdout"],
            "numeric_policy_slot": identity["numeric_policy_slot"], "cost_policy_slot": identity["cost_policy_slot"],
            "execution": identity["execution"]}


def _signal_facts(identity: Mapping[str, Any]) -> dict[str, Any]:
    evidence = identity["evidence"]
    return {"explanation": identity["explanation"],
            "evidence": {name: evidence[name] for name in (
                "session_id", "bar_open_micros", "closing_record_hash", "trade_manifest_hash",
                "window_first_bar_open_micros", "window_bars", "window_session_boundaries")}}


def _fill_facts(fill: PaperFillV1) -> dict[str, Any]:
    identity = fill.identity
    evidence = identity["evidence"] or {}
    return {
        "fill_eligibility": identity["status"],
        "signed_exposure": {"from": identity["target_from"], "to": identity["target_to"],
                            "taken": identity["status"] == FILL_STATUS_FILLED},
        "price": identity["fill_price"],
        "provenance": {"fill_bar_open_micros": identity["fill_bar_open_micros"],
                       "session_id": evidence.get("session_id"),
                       "first_trade_reference": evidence.get("first_trade_reference"),
                       "trade_manifest_hash": evidence.get("trade_manifest_hash"),
                       "cost_policy_hash": identity["cost_policy_hash"], "fill_rule": identity["fill_rule"],
                       "fill_liquidity": identity["fill_liquidity"], "sizing": identity["sizing"]},
    }


def _knowledge(signal: LiveSignalV1 | None, fill: PaperFillV1 | None) -> dict[str, Any] | None:
    if signal is None:
        return None
    evidence = (fill.identity["evidence"] or {}) if fill is not None else {}
    return {"clock_bound": signal.identity["evidence"]["clock_bound"],
            "decided_at_micros": _micros(signal.decided_at),
            "complete_market_knowledge_micros": signal.identity["evidence"]["complete_market_knowledge_micros"],
            "fill_open_market_knowledge_micros": evidence.get("open_market_knowledge_micros")}


def _compare_signal(key: tuple[str, str, str, int], live: LiveSignalV1, replay: LiveSignalV1,
                    live_fill: PaperFillV1 | None, replay_fill: PaperFillV1 | None, *, live_last_bar: int,
                    replay_last_bar: int, seals: Mapping[tuple[str, int], str]) -> FillParityObservationV1:
    dims: dict[str, dict[str, Any]] = {}

    def put(name: str, left: object, right: object) -> None:
        dims[name] = {"outcome": OUTCOME_MATCH if left == right else OUTCOME_DIVERGED, "live": left,
                      "replay": right}

    put("candidate", _candidate_facts(live.identity), _candidate_facts(replay.identity))
    put("signal", _signal_facts(live.identity), _signal_facts(replay.identity))
    put("target", [live.identity["target_from"], live.identity["target_to"]],
        [replay.identity["target_from"], replay.identity["target_to"]])
    live_minute = expected_fill_bar_open_micros_v1(live.decided_at)
    replay_minute = expected_fill_bar_open_micros_v1(replay.decided_at)
    put("timestamp", live_minute, replay_minute)
    missing: list[str] = []
    if live_fill is None:
        missing.append(LIVE_FILL_BAR_NOT_YET_OBSERVED if live_minute > live_last_bar else LIVE_FILL_NOT_RECORDED)
    if replay_fill is None:
        missing.append(REPLAY_FILL_BAR_NOT_SEALED)
    if live_fill is None or replay_fill is None:
        for name in _FILL_DIMENSIONS:
            dims[name] = {"outcome": OUTCOME_UNAVAILABLE, "unavailable": sorted(missing)}
    else:
        left, right = _fill_facts(live_fill), _fill_facts(replay_fill)
        for name in _FILL_DIMENSIONS:
            put(name, left[name], right[name])
    reasons = [f"{name.upper()}_DIVERGED" for name, dim in dims.items() if dim["outcome"] == OUTCOME_DIVERGED]
    outcome = OUTCOME_DIVERGED if reasons else OUTCOME_UNAVAILABLE if missing else OUTCOME_MATCH
    if outcome != OUTCOME_DIVERGED:
        reasons = missing
    replay_fill_bar = None if replay_fill is None else (key[0], int(replay_fill.identity["fill_bar_open_micros"]))
    facts = {
        "dimensions": {name: dims[name] for name in SIGNAL_DIMENSIONS_V1},
        "live_signal": {"signal_id": str(live.signal_id), "previous_signal_id": live.identity["previous_signal_id"],
                        "fill_id": None if live_fill is None else str(live_fill.fill_id)},
        "replay_signal": {"signal_id": str(replay.signal_id),
                          "fill_id": None if replay_fill is None else str(replay_fill.fill_id),
                          "signal_seal_content_hash": seals.get((key[0], key[3])),
                          "fill_seal_content_hash": None if replay_fill_bar is None else seals.get(replay_fill_bar)},
        "knowledge": {"live_provisional": _knowledge(live, live_fill),
                      "sealed_finalized": _knowledge(replay, replay_fill)},
        "fill_both_filled": (live_fill is not None and replay_fill is not None
                             and live_fill.status == FILL_STATUS_FILLED and replay_fill.status == FILL_STATUS_FILLED),
        "observed_through": {"live_last_bar_open_micros": live_last_bar,
                             "replay_last_bar_open_micros": replay_last_bar},
    }
    return _observation(KIND_SIGNAL, _key_payload(key), outcome, reasons, facts)


def _replay_only(key: tuple[str, str, str, int], replay: LiveSignalV1,
                 chain: Sequence[tuple[int, LiveSignalV1]], window: tuple[int, int] | None,
                 seals: Mapping[tuple[str, int], str]) -> FillParityObservationV1:
    """A replay signal with no live counterpart: a divergence only where the live runner provably ran."""
    bar = key[3]
    facts: dict[str, Any] = {"replay_signal": {"signal_id": str(replay.signal_id),
                                               "target": [replay.identity["target_from"], replay.identity["target_to"]],
                                               "signal_seal_content_hash": seals.get((key[0], bar))},
                             "knowledge": {"live_provisional": None, "sealed_finalized": _knowledge(replay, None)}}
    if window is None or not window[0] <= bar <= window[1]:
        return _observation(KIND_SIGNAL, _key_payload(key), OUTCOME_UNAVAILABLE, [LIVE_NOT_OBSERVED_AT_BAR], facts)
    later = next((signal for opened, signal in chain if opened > bar), None)
    if later is None:
        return _observation(KIND_SIGNAL, _key_payload(key), OUTCOME_UNAVAILABLE,
                            [LIVE_CONTINUITY_UNPROVEN_AFTER_LAST_SIGNAL], facts)
    facts["next_live_signal"] = {"signal_id": str(later.signal_id),
                                 "previous_signal_id": later.identity["previous_signal_id"]}
    if later.identity["previous_signal_id"] is None:
        return _observation(KIND_SIGNAL, _key_payload(key), OUTCOME_UNAVAILABLE, [LIVE_RUNNER_RESTART_GAP], facts)
    return _observation(KIND_SIGNAL, _key_payload(key), OUTCOME_DIVERGED, [SIGNAL_ABSENT_LIVE], facts)


def _live_sequence(chains: Mapping[tuple[str, str, str], Sequence[tuple[int, LiveSignalV1]]],
                   supplied: set[str]) -> list[FillParityObservationV1]:
    out = []
    for (symbol, study, trial), chain in sorted(chains.items()):
        for index, (opened, signal) in enumerate(chain):
            named = signal.identity["previous_signal_id"]
            if named is None:
                continue
            expected = str(chain[index - 1][1].signal_id) if index > 0 else None
            if named == expected:
                continue
            key = _key_payload((symbol, study, trial, opened))
            facts = {"signal_id": str(signal.signal_id), "previous_signal_id": named,
                     "preceding_live_signal_id": expected}
            if named not in supplied:
                out.append(_observation(KIND_LIVE_SEQUENCE, key, OUTCOME_UNAVAILABLE,
                                        [LIVE_SIGNAL_PREDECESSOR_NOT_SUPPLIED], facts))
            else:
                out.append(_observation(KIND_LIVE_SEQUENCE, key, OUTCOME_DIVERGED,
                                        [LIVE_SIGNAL_CHAIN_ORDER_MISMATCH], facts))
    return out


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def assess_fill_parity_v1(
    *,
    candidates: Sequence[WatchedCandidateV1],
    holdout_gate: LiveHoldoutGateV1,
    cost_policy: CostPolicyV1,
    replay_bars: Sequence[SealedReplayBarV1],
    live_bars: Sequence[LiveBarV1],
    live_signals: Sequence[LiveSignalV1],
    live_fills: Sequence[PaperFillV1],
) -> FillParityReportV1:
    """Reconstruct the replay with R8/R10 and assess it against the live record. Read-only, in memory."""
    if not candidates:
        raise PaperFillParityError("parity_requires_at_least_one_candidate")
    if any(candidate.authority != AUTHORITY_INCUBATING for candidate in candidates):
        raise PaperFillParityError("parity_is_assessed_only_for_incubating_candidates")
    live_bar_map = _unique_bars(((bar.symbol, bar.bar) for bar in live_bars), "live")
    live_sessions = {(bar.symbol, bar.bar.bar_open_micros): bar.session_id for bar in live_bars}
    _unique_bars(((bar.symbol, bar.bar) for bar in replay_bars), "replay")
    replay_map = {(bar.symbol, bar.bar.bar_open_micros): bar for bar in replay_bars}
    seals = {key: bar.seal_content_hash for key, bar in replay_map.items()}
    windows: dict[str, tuple[int, int]] = {}
    for symbol, opened in live_bar_map:
        low, high = windows.get(symbol, (opened, opened))
        windows[symbol] = (min(low, opened), max(high, opened))

    observations: list[FillParityObservationV1] = []
    observations.extend(_bar_observations(live_bar_map, live_sessions, replay_map, windows))

    # Live signals: integrity, duplicates and per-candidate chains.
    live_hashes: dict[str, str] = {}
    live_by_key: dict[tuple[str, str, str, int], LiveSignalV1] = {}
    chains: dict[tuple[str, str, str], list[tuple[int, LiveSignalV1]]] = {}
    for signal in sorted(live_signals, key=lambda s: (_signal_key(s), str(s.signal_id))):
        content_hash = _verified_signal(signal)
        if str(signal.signal_id) in live_hashes:
            continue  # the same signal supplied twice is one signal
        live_hashes[str(signal.signal_id)] = content_hash
        key = _signal_key(signal)
        if key in live_by_key:
            observations.append(_observation(KIND_LIVE_SEQUENCE, _key_payload(key), OUTCOME_DIVERGED,
                                             [DUPLICATE_LIVE_SIGNAL],
                                             {"signal_ids": sorted([str(live_by_key[key].signal_id),
                                                                    str(signal.signal_id)])}))
            continue
        live_by_key[key] = signal
        chains.setdefault(key[:3], []).append((key[3], signal))
    observations.extend(_live_sequence(chains, set(live_hashes)))

    # Live fills: integrity, one per signal, bound to a supplied signal with its hash.
    live_fill_by_signal: dict[str, PaperFillV1] = {}
    for fill in sorted(live_fills, key=lambda f: (str(f.identity["signal_id"]), f.content_hash)):
        _verified_fill(fill)
        signal_id = str(fill.identity["signal_id"])
        key_payload = {"signal_id": signal_id}
        previous = live_fill_by_signal.get(signal_id)
        if previous is not None:
            if previous.content_hash != fill.content_hash:
                observations.append(_observation(KIND_LIVE_SEQUENCE, key_payload, OUTCOME_DIVERGED,
                                                 [DUPLICATE_LIVE_FILL],
                                                 {"fill_hashes": sorted([previous.content_hash, fill.content_hash])}))
            continue
        if signal_id not in live_hashes:
            observations.append(_observation(KIND_LIVE_SEQUENCE, key_payload, OUTCOME_UNAVAILABLE,
                                             [LIVE_FILL_SIGNAL_NOT_SUPPLIED], {"fill_hash": fill.content_hash}))
            continue
        if fill.identity["signal_content_hash"] != live_hashes[signal_id]:
            observations.append(_observation(KIND_LIVE_SEQUENCE, key_payload, OUTCOME_DIVERGED,
                                             [LIVE_FILL_SIGNAL_HASH_MISMATCH], {"fill_hash": fill.content_hash}))
            continue
        live_fill_by_signal[signal_id] = fill

    # Replay through the unchanged R8 runner and R10 engine.
    replay_signals, replay_fills = _replay(candidates, holdout_gate, cost_policy, replay_bars)
    replay_by_key = {_signal_key(signal): signal for signal in replay_signals}
    replay_fill_by_signal = {str(fill.identity["signal_id"]): fill for fill in replay_fills}
    replay_last: dict[str, int] = {}
    for symbol, opened in replay_map:
        replay_last[symbol] = max(opened, replay_last.get(symbol, opened))

    for key in sorted(live_by_key.keys() | replay_by_key.keys()):
        live, replay = live_by_key.get(key), replay_by_key.get(key)
        if live is not None and replay is not None:
            observations.append(_compare_signal(
                key, live, replay, live_fill_by_signal.get(str(live.signal_id)),
                replay_fill_by_signal.get(str(replay.signal_id)), live_last_bar=windows[key[0]][1],
                replay_last_bar=replay_last.get(key[0], -1), seals=seals))
        elif replay is not None:
            observations.append(_replay_only(key, replay, chains.get(key[:3], []), windows.get(key[0]), seals))
        elif live is not None:
            sealed = (key[0], key[3]) in replay_map
            beyond = key[3] > replay_last.get(key[0], -1)
            outcome, reason = ((OUTCOME_DIVERGED, SIGNAL_ABSENT_REPLAY) if sealed or not beyond
                               else (OUTCOME_UNAVAILABLE, REPLAY_BAR_NOT_SEALED))
            observations.append(_observation(KIND_SIGNAL, _key_payload(key), outcome, [reason], {
                "live_signal": {"signal_id": str(live.signal_id),
                                "target": [live.identity["target_from"], live.identity["target_to"]]},
                "knowledge": {"live_provisional": _knowledge(live, None), "sealed_finalized": None}}))

    observations.sort(key=lambda item: item.content_hash)
    return _report(observations, candidates=candidates, gate=holdout_gate, cost_policy=cost_policy,
                   seals=sorted(set(seals.values())), live_signal_hashes=sorted(live_hashes.values()),
                   live_fill_hashes=sorted({fill.content_hash for fill in live_fills}), windows=windows)


def _report(observations: Sequence[FillParityObservationV1], *, candidates: Sequence[WatchedCandidateV1],
            gate: LiveHoldoutGateV1, cost_policy: CostPolicyV1, seals: list[str], live_signal_hashes: list[str],
            live_fill_hashes: list[str], windows: Mapping[str, tuple[int, int]]) -> FillParityReportV1:
    outcomes = Counter(item.outcome for item in observations)
    by_reason = Counter(reason for item in observations for reason in item.reasons)
    diverged = sorted({reason for item in observations if item.outcome == OUTCOME_DIVERGED
                       for reason in item.reasons})
    unavailable = sorted({reason for item in observations if item.outcome == OUTCOME_UNAVAILABLE
                          for reason in item.reasons})
    matched_filled = sum(1 for item in observations if item.identity["kind"] == KIND_SIGNAL
                         and item.outcome == OUTCOME_MATCH and item.identity["facts"]["fill_both_filled"])
    fill_statuses = Counter(item.identity["facts"]["dimensions"]["fill_eligibility"]["live"]
                            for item in observations if item.identity["kind"] == KIND_SIGNAL
                            and item.outcome == OUTCOME_MATCH)
    if diverged:
        status, reasons = ASSESSMENT_FAIL, diverged
    elif unavailable or matched_filled == 0:
        status = ASSESSMENT_INCOMPLETE
        reasons = sorted(set(unavailable) | ({NO_MATCHED_FILLED_OBSERVATION} if matched_filled == 0 else set()))
    else:
        status, reasons = ASSESSMENT_PASS, []
    identity = {
        "schema_version": FILL_PARITY_SCHEMA_VERSION_V1,
        "claim": "READ_ONLY_PARITY_EVIDENCE_NOT_A_PROMOTION_DECISION",
        "inputs": {
            "candidates": sorted(({"study_content_hash": c.study.content_hash, "trial_id": c.trial_id,
                                   "symbol": c.symbol, "authority": c.authority,
                                   "authority_evidence_hash": c.authority_evidence_hash} for c in candidates),
                                 key=lambda item: (item["study_content_hash"], item["trial_id"], item["symbol"])),
            "holdout": gate.payload(),
            "cost_policy_hash": cost_policy.policy().content_hash,
            "seal_content_hashes": seals,
            "live_signal_hashes": live_signal_hashes,
            "live_fill_hashes": live_fill_hashes,
            "live_windows": {symbol: {"first_bar_open_micros": low, "last_bar_open_micros": high}
                             for symbol, (low, high) in sorted(windows.items())},
        },
        "knowledge_time_semantics": {"live": "PROVISIONAL (LIVE_PROVISIONAL bound, no later clock sample)",
                                     "replay": "FINALIZED (sealed bracketed bound)",
                                     "compared": "fill minute derived on each side; knowledge instants never equated"},
        "tolerance": "NONE",
        "observations": [item.content_hash for item in observations],
        "summary": {"outcomes": dict(sorted(outcomes.items())), "reasons": dict(sorted(by_reason.items())),
                    "matched_signals_with_both_fills_filled": matched_filled,
                    "matched_signal_fill_statuses": dict(sorted(fill_statuses.items()))},
        "assessment": {"status": status, "reasons": reasons},
    }
    content_hash = identity_hash_v1(identity)
    return FillParityReportV1(identity, uuid5(_NAMESPACE, f"report:{content_hash}"), content_hash,
                              tuple(observations))


__all__ = [
    "ASSESSMENT_FAIL",
    "ASSESSMENT_INCOMPLETE",
    "ASSESSMENT_PASS",
    "FILL_PARITY_SCHEMA_VERSION_V1",
    "OUTCOME_DIVERGED",
    "OUTCOME_MATCH",
    "OUTCOME_UNAVAILABLE",
    "SIGNAL_DIMENSIONS_V1",
    "FillParityObservationV1",
    "FillParityReportV1",
    "PaperFillParityError",
    "SealedReplayBarV1",
    "assess_fill_parity_v1",
]
