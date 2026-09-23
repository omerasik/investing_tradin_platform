"""Operator entry point for the Phase 3Z.2 first-party capture recorder.

Public market data only. No credential, no account, no order, no broker.

Record until interrupted (the normal operator mode)::

    .venv\\Scripts\\python scripts/capture_bybit_public.py run

Record a short bounded smoke capture::

    .venv\\Scripts\\python scripts/capture_bybit_public.py run --seconds 60

Check what the archive currently holds and whether it is provable::

    .venv\\Scripts\\python scripts/capture_bybit_public.py health

Replay a finalized partition, re-verifying every checksum and content hash::

    .venv\\Scripts\\python scripts/capture_bybit_public.py replay <partition-directory>

Capture only accrues while this process runs. A shut-down, sleeping or
suspended machine records nothing, and the recorder says so by leaving an
explicit gap rather than by implying continuous coverage.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trade_platform.bybit_public_websocket_recorder_v1 import (
    BybitPublicCaptureRecorderV1,
    iter_health_lines,
)
from trade_platform.first_party_capture_archive_v1 import (
    default_archive_root,
    read_partition_status_v1,
    replay_partition_v1,
)
from trade_platform.first_party_capture_authority_v1 import (
    first_party_bybit_capture_contract_v1,
)


def _run(args: argparse.Namespace) -> int:
    contract = first_party_bybit_capture_contract_v1()
    root = Path(args.root) if args.root else default_archive_root()
    print(f"source_id      {contract.source_id}")
    print(f"contract_hash  {contract.content_hash()}")
    print(f"endpoint       {contract.endpoint}")
    print(f"topics         {', '.join(contract.topics())}")
    print(f"archive_root   {root}")
    print("Ctrl-C to stop. Capture accrues only while this process runs.\n")
    recorder = BybitPublicCaptureRecorderV1(archive_root=root)
    try:
        health = recorder.run(max_seconds=args.seconds, max_records=args.records)
    except KeyboardInterrupt:
        print("\ninterrupted; finalizing partition")
        health = recorder.health()
    print(health.summary())
    if health.partition_directory is not None:
        print(f"partition      {health.partition_directory}")
    return 0


def _health(args: argparse.Namespace) -> int:
    root = Path(args.root) if args.root else default_archive_root()
    lines = list(iter_health_lines(root))
    if not lines:
        print(f"no capture partitions under {root}")
        return 0
    for line in lines:
        print(line)
    return 0


def _replay(args: argparse.Namespace) -> int:
    directory = Path(args.partition)
    partition = read_partition_status_v1(directory)
    print(f"status {partition.status} reasons={','.join(partition.reasons) or 'none'}")
    count = 0
    channels: dict[str, int] = {}
    for record in replay_partition_v1(directory, require_complete=not args.allow_partial):
        count += 1
        channels[record.channel] = channels.get(record.channel, 0) + 1
    print(f"replayed {count} records, all hashes verified")
    for channel, total in sorted(channels.items()):
        print(f"  {channel}: {total}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="archive root (default: ~/.trade_platform/capture)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="record the official public Bybit feed")
    run.add_argument("--seconds", type=float, default=None, help="stop after N seconds")
    run.add_argument("--records", type=int, default=None, help="stop after N records")
    run.set_defaults(handler=_run)

    health = sub.add_parser("health", help="show partition status under the archive root")
    health.set_defaults(handler=_health)

    replay = sub.add_parser("replay", help="re-verify and replay one partition")
    replay.add_argument("partition")
    replay.add_argument("--allow-partial", action="store_true")
    replay.set_defaults(handler=_replay)

    args = parser.parse_args(argv)
    handler = args.handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
