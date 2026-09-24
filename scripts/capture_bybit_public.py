"""Operator entry point for first-party capture (Phase 3Z.2 recorder, Phase R1A operations).

Public market data only. No credential, no account, no order, no broker.

Record the production BTCUSDT contract until interrupted::

    .venv\\Scripts\\python scripts/capture_bybit_public.py run

Record unattended: restart after a crash, ask Windows not to idle-sleep, stop
cleanly (and stay stopped) when free disk space falls below the floor::

    .venv\\Scripts\\python scripts/capture_bybit_public.py supervise

Measure what multi-symbol capture costs on this host (R1A; a pinned engineering
sample, not a production universe; written to a separate archive root)::

    .venv\\Scripts\\python scripts/capture_bybit_public.py measure --seconds 86400

Inspect, prove, compact and back up the archive::

    .venv\\Scripts\\python scripts/capture_bybit_public.py health
    .venv\\Scripts\\python scripts/capture_bybit_public.py availability
    .venv\\Scripts\\python scripts/capture_bybit_public.py replay <partition-directory>
    .venv\\Scripts\\python scripts/capture_bybit_public.py compact
    .venv\\Scripts\\python scripts/capture_bybit_public.py backup <destination-root>

Capture only accrues while a recorder runs. A shut-down, sleeping or suspended
machine records nothing and writes nothing -- it cannot. The hole is visible
because coverage is positive-only: ``availability`` reports it as a derived gap.
A hard crash leaves a PARTIAL partition that proves nothing and is excluded.
Compaction is lossless: the manifest keeps binding the uncompressed bytes and
the compressed file is proven to reproduce them before the original is removed.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.bybit_public_websocket_recorder_v1 import (
    CaptureDiskBudgetError,
    CaptureFleetResultV1,
    iter_health_lines,
    request_keep_awake_v1,
    run_capture_fleet_v1,
)
from trade_platform.first_party_capture_archive_v1 import (
    BACKUP_COPIED_AND_VERIFIED,
    END_PROOF_DISK_BUDGET_STOP,
    CompactionResultV1,
    backup_archive_v1,
    compaction_candidates_v1,
    default_archive_root,
    derive_archive_availability_by_source_v1,
    disk_free_bytes_v1,
    iter_compaction_results_v1,
    nanos_to_datetime,
    read_partition_status_v1,
    replay_partition_v1,
    verify_partition_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_capture_contract_v1,
    first_party_bybit_measurement_contracts_v1,
)
from trade_platform.first_party_capture_measurement_v1 import (
    CapacityMeasurementV1,
    build_capacity_report_v1,
    bybit_clock_offset_payload_v1,
    render_capacity_report_text_v1,
    sample_bybit_server_clock_offset_v1,
    write_capacity_report_v1,
)

_GIB = 1024**3

#: An engineering safety floor, not an economic parameter: below it capture
#: stops cleanly rather than filling the system volume.
DEFAULT_MIN_FREE_GIB = 20.0

#: Clock-offset samples during a measurement run are this far apart.
_CLOCK_OFFSET_INTERVAL_SECONDS = 900.0


def _root(args: argparse.Namespace) -> Path:
    return Path(args.root) if args.root else default_archive_root()


def _measurement_root(args: argparse.Namespace) -> Path:
    if args.root:
        return Path(args.root)
    return default_archive_root().parent / "capture-measurement"


def _min_free_bytes(args: argparse.Namespace) -> int:
    return int(float(args.min_free_gb) * _GIB)


def _print_fleet(result: CaptureFleetResultV1) -> None:
    for health in result.sessions:
        print(health.summary())
    print(f"end_proof      {result.end_proof}")
    print(f"restarts       {result.restarts}")
    print(f"clock_samples  {len(result.clock_samples)} (failed {result.clock_sample_failures})")
    for failure, count in result.failures.items():
        print(f"lane_failure   x{count} {failure}")
    for compaction in result.compactions:
        print(
            f"compacted      {compaction.uncompressed_bytes} -> {compaction.compressed_bytes} "
            f"bytes {compaction.directory}"
        )
    for directory, reason in result.compaction_failures:
        print(f"compact_failed {directory} {reason}")


def _run(args: argparse.Namespace) -> int:
    contract = first_party_bybit_capture_contract_v1()
    root = _root(args)
    print(f"source_id      {contract.source_id}")
    print(f"contract_hash  {contract.content_hash()}")
    print(f"endpoint       {contract.endpoint}")
    print(f"topics         {', '.join(contract.topics())}")
    print(f"archive_root   {root}")
    print(f"min_free       {args.min_free_gb} GiB")
    print("Ctrl-C to stop. Capture accrues only while this process runs.\n")
    try:
        result = run_capture_fleet_v1(
            (contract,),
            archive_root=root,
            max_seconds=args.seconds,
            min_free_bytes=_min_free_bytes(args),
            clock_sampler=bybit_clock_offset_payload_v1,
        )
    except CaptureDiskBudgetError as error:
        print(f"refused: {error}")
        return 2
    _print_fleet(result)
    return 2 if result.end_proof == END_PROOF_DISK_BUDGET_STOP else 0


def _supervise(args: argparse.Namespace) -> int:
    """Keep the production recorder running; each restart is a new session."""
    contract = first_party_bybit_capture_contract_v1()
    root = _root(args)
    awake = request_keep_awake_v1()
    print(f"supervising {contract.exchange_symbol} into {root}; keep-awake requested={awake}")
    print("Ctrl-C to stop. A reboot, shutdown or lid-close still ends the session.\n")
    while True:
        try:
            result = run_capture_fleet_v1(
                (contract,),
                archive_root=root,
                min_free_bytes=_min_free_bytes(args),
                clock_sampler=bybit_clock_offset_payload_v1,
            )
        except CaptureDiskBudgetError as error:
            print(f"refused: {error}; free space before restarting capture")
            return 2
        except KeyboardInterrupt:
            return 0
        except Exception as error:  # noqa: BLE001 - supervisor must outlive any failure
            print(f"{datetime.now(UTC).isoformat()} fleet failed: {type(error).__name__}: {error}")
            time.sleep(args.restart_delay)
            continue
        _print_fleet(result)
        if result.end_proof == END_PROOF_DISK_BUDGET_STOP:
            print("stopped: free disk space fell below the floor")
            return 2
        return 0


def _measure(args: argparse.Namespace) -> int:
    contracts = first_party_bybit_measurement_contracts_v1()
    root = _measurement_root(args)
    measurement = CapacityMeasurementV1()
    print(f"capacity measurement: {len(contracts)} symbols, {args.seconds:.0f}s, root {root}")
    print(f"symbols        {', '.join(contract.exchange_symbol for contract in contracts)}")
    print("An engineering sample for measurement only -- not a production universe.\n")
    awake = request_keep_awake_v1()
    print(f"keep-awake requested={awake}")
    disk_start = disk_free_bytes_v1(root)
    measurement.sample_resources()
    started = time.monotonic()
    try:
        result = run_capture_fleet_v1(
            contracts,
            archive_root=root,
            max_seconds=args.seconds,
            min_free_bytes=_min_free_bytes(args),
            observer_for=measurement.observer_for,
            on_maintenance=measurement.sample_resources,
            maintenance_interval_seconds=60.0,
            clock_sampler=bybit_clock_offset_payload_v1,
            clock_sample_interval_seconds=_CLOCK_OFFSET_INTERVAL_SECONDS,
        )
    except CaptureDiskBudgetError as error:
        print(f"refused: {error}")
        return 2
    measured = time.monotonic() - started
    measurement.sample_resources()

    by_symbol = {contract.exchange_symbol: str(contract.source_id) for contract in contracts}
    summaries: dict[str, dict[str, int]] = {}
    for health in result.sessions:
        key = by_symbol.get(health.exchange_symbol or "")
        if key is None:
            continue
        entry = summaries.setdefault(
            key,
            {"sessions": 0, "reconnects": 0, "gaps": 0, "contract_violations": 0,
             "clock_discontinuities": 0},
        )
        entry["sessions"] += 1
        entry["reconnects"] += health.reconnects
        entry["gaps"] += health.gaps_recorded
        entry["contract_violations"] += health.contract_violations
        entry["clock_discontinuities"] += health.clock_discontinuities

    report = build_capacity_report_v1(
        measurement,
        contracts=contracts,
        measured_seconds=measured,
        compactions=result.compactions,
        session_summaries=summaries,
        disk_free_start_bytes=disk_start,
        disk_free_end_bytes=disk_free_bytes_v1(root),
        failures=result.failures,
        restarts=result.restarts,
        end_proof=result.end_proof,
        clock_samples=result.clock_samples,
        clock_sample_failures=result.clock_sample_failures,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = write_capacity_report_v1(report, root / "reports", stamp=stamp)
    print(render_capacity_report_text_v1(report))
    print(f"\nreport         {path}")
    for directory, reason in result.compaction_failures:
        print(f"compact_failed {directory} {reason}")
    return 0


def _clock(args: argparse.Namespace) -> int:
    """Sample the host clock against Bybit's public server time (evidence, not a sync)."""
    samples = [
        sample
        for sample in (sample_bybit_server_clock_offset_v1() for _ in range(args.samples))
        if sample is not None
    ]
    if not samples:
        print("no clock sample could be taken (network or endpoint unavailable)")
        return 1
    for sample in samples:
        print(
            f"offset (server - host) {sample.offset_estimate_nanos / 1e6:+9.1f} ms  "
            f"bound +/- {sample.offset_bound_nanos / 1e6:.1f} ms"
        )
    best = min(samples, key=lambda sample: sample.offset_bound_nanos)
    worst = max(abs(sample.offset_estimate_nanos) + sample.offset_bound_nanos for sample in samples)
    print(
        f"tightest: {best.offset_estimate_nanos / 1e6:+.1f} ms +/- "
        f"{best.offset_bound_nanos / 1e6:.1f} ms"
    )
    if worst > args.warn_ms * 1_000_000:
        print(
            f"WARNING: the host clock may be off by more than {args.warn_ms:g} ms. "
            "Arrival timestamps are recorded on this clock; enable Windows time "
            "synchronization before relying on T4 knowledge times."
        )
        return 3
    return 0


def _health(args: argparse.Namespace) -> int:
    root = _root(args)
    lines = list(iter_health_lines(root))
    if not lines:
        print(f"no capture partitions under {root}")
        return 0
    for line in lines:
        print(line)
    print(f"disk_free      {disk_free_bytes_v1(root) / _GIB:.1f} GiB")
    return 0


def _replay(args: argparse.Namespace) -> int:
    directory = Path(args.partition)
    partition = read_partition_status_v1(directory)
    print(f"status {partition.status} reasons={','.join(partition.reasons) or 'none'}")
    if args.allow_partial:
        count = sum(1 for _ in replay_partition_v1(directory, require_complete=False))
        print(f"replayed {count} records from an unproven partition; nothing is claimed")
        return 0
    verification = verify_partition_v1(directory)
    print(
        f"verified {verification.record_count} records: hashes, contract, ordering, "
        f"coverage placement and counts all reconcile"
    )
    for channel, total in verification.records_by_channel.items():
        print(f"  {channel}: {total}")
    for window in verification.coverage:
        print(
            f"  window [{window.start_utc_nanos}, {window.end_utc_nanos}) "
            f"records={window.record_count} end_proof={window.end_proof}"
        )
    for gap in verification.gaps:
        print(f"  gap [{gap.start_utc_nanos}, {gap.end_utc_nanos}) kind={gap.kind}")
    return 0


def _availability(args: argparse.Namespace) -> int:
    root = _root(args)
    sources, unattributed = derive_archive_availability_by_source_v1(root)
    production = first_party_bybit_capture_contract_v1().source_id
    for source in sources:
        purpose = "production" if source.contract.source_id == production else "measurement"
        print(f"== {source.contract.exchange_symbol} ({purpose}) source={source.contract.source_id}")
        availability = source.availability
        for window in availability.windows:
            interval = window.interval
            print(
                f"COVERED   {nanos_to_datetime(interval.start_utc_nanos).isoformat()} -> "
                f"{nanos_to_datetime(interval.last_proven_utc_nanos).isoformat()} "
                f"records={interval.record_count} end_proof={interval.end_proof} "
                f"session={window.session_id}"
            )
        for gap in availability.gaps:
            end = (
                "open" if gap.end_utc_nanos is None
                else nanos_to_datetime(gap.end_utc_nanos).isoformat()
            )
            print(
                f"UNPROVEN  {nanos_to_datetime(gap.start_utc_nanos).isoformat()} -> {end} "
                f"kind={gap.kind}"
            )
        for directory, reasons in availability.excluded:
            print(f"EXCLUDED  {directory} reasons={','.join(reasons)}")
    for directory, reasons in unattributed:
        print(f"UNATTRIBUTED {directory} reasons={','.join(reasons)}")
    return 0


def _compact(args: argparse.Namespace) -> int:
    root = _root(args)
    failed = 0
    for outcome in iter_compaction_results_v1(compaction_candidates_v1(root)):
        if isinstance(outcome, CompactionResultV1):
            print(
                f"compacted {outcome.uncompressed_bytes} -> {outcome.compressed_bytes} bytes "
                f"{outcome.directory}"
            )
        else:
            failed += 1
            print(f"failed {outcome[0]} {outcome[1]}")
    return 1 if failed else 0


def _backup(args: argparse.Namespace) -> int:
    outcomes = backup_archive_v1(_root(args), Path(args.destination))
    for outcome in outcomes:
        detail = "" if outcome.detail is None else f" {outcome.detail}"
        print(f"{outcome.action} {outcome.destination_directory}{detail}")
    copied = sum(1 for outcome in outcomes if outcome.action == BACKUP_COPIED_AND_VERIFIED)
    print(f"{copied} partition(s) copied and verified, {len(outcomes)} considered")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", help="archive root (default: ~/.trade_platform/capture)")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_floor(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--min-free-gb",
            type=float,
            default=DEFAULT_MIN_FREE_GIB,
            help=f"stop cleanly below this much free disk (default {DEFAULT_MIN_FREE_GIB:g})",
        )

    run = sub.add_parser("run", help="record the production contract of the official public feed")
    run.add_argument("--seconds", type=float, default=None, help="stop after N seconds")
    with_floor(run)
    run.set_defaults(handler=_run)

    supervise = sub.add_parser("supervise", help="run the production recorder unattended")
    supervise.add_argument("--restart-delay", type=float, default=60.0)
    with_floor(supervise)
    supervise.set_defaults(handler=_supervise)

    measure = sub.add_parser("measure", help="R1A capacity measurement over the pinned sample")
    measure.add_argument("--seconds", type=float, default=600.0)
    with_floor(measure)
    measure.set_defaults(handler=_measure)

    clock = sub.add_parser("clock", help="measure the host clock against Bybit server time")
    clock.add_argument("--samples", type=int, default=5)
    clock.add_argument("--warn-ms", type=float, default=250.0)
    clock.set_defaults(handler=_clock)

    health = sub.add_parser("health", help="show partition status under the archive root")
    health.set_defaults(handler=_health)

    replay = sub.add_parser("replay", help="re-verify and replay one partition")
    replay.add_argument("partition")
    replay.add_argument("--allow-partial", action="store_true")
    replay.set_defaults(handler=_replay)

    availability = sub.add_parser("availability", help="proven windows and gaps, per source")
    availability.set_defaults(handler=_availability)

    compact = sub.add_parser("compact", help="losslessly compress finalized partitions")
    compact.set_defaults(handler=_compact)

    backup = sub.add_parser("backup", help="copy COMPLETE partitions elsewhere and re-verify")
    backup.add_argument("destination")
    backup.set_defaults(handler=_backup)

    args = parser.parse_args(argv)
    handler = args.handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
