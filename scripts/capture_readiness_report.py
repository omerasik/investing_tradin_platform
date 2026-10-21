"""Read-only R1B report from explicit SYMBOL=partition selections; never scans a host.

Example (no partitions selected, reports missing coverage and UNKNOWN telemetry)::

    python scripts/capture_readiness_report.py --start 2026-10-20T00:00:00Z \
        --end 2026-10-21T00:00:00Z --observed-at 2026-10-21T04:00:00Z \
        --diagnostic-max-unproven-nanos 0 --max-partitions 3 --max-report-hours 24

Add --partition BTCUSDT=/absolute/partition for each selected partition. Full
record replay can be costly: select a bounded set deliberately. A diagnostic
bound is mandatory and never approves formal S. No host probes are performed.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from trade_platform.capture_readiness_report_v1 import capture_readiness_report_v1


def utc_nanos(value: str) -> int:
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp_requires_timezone")
    delta = instant.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--partition", action="append", default=[], metavar="SYMBOL=PATH")
    parser.add_argument("--start", type=utc_nanos, required=True)
    parser.add_argument("--end", type=utc_nanos, required=True)
    parser.add_argument("--observed-at", type=utc_nanos, required=True)
    parser.add_argument("--diagnostic-max-unproven-nanos", type=int, required=True)
    parser.add_argument("--max-partitions", type=int, required=True)
    parser.add_argument("--max-report-hours", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        selections = []
        for selection in args.partition:
            symbol, separator, directory = selection.partition("=")
            if not separator or not directory:
                raise ValueError("partition_requires_SYMBOL_equals_PATH")
            selections.append((symbol, Path(directory)))
        report = capture_readiness_report_v1(
            selections, start_utc_nanos=args.start, end_utc_nanos=args.end,
            observed_at_utc_nanos=args.observed_at,
            diagnostic_max_unproven_nanos=args.diagnostic_max_unproven_nanos,
            max_partitions=args.max_partitions, max_report_hours=args.max_report_hours,
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(asdict(report), default=str, sort_keys=True))
    return 1 if report.readiness == "NO_GO" else 0


if __name__ == "__main__":
    raise SystemExit(main())
