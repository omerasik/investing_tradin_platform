"""Run one CI shard of the unittest suite and prove, across shards, that nothing was dropped.

CI runs the suite as parallel jobs, each against its own disposable PostgreSQL service, so the
wall-clock is bounded by the slowest shard instead of the sum. Splitting must never cost coverage:

* ``run --shard NAME`` discovers the WHOLE suite exactly as ``python -m unittest discover -s tests``
  does, runs only the test modules assigned to NAME (in discovery order), and writes a JSON report
  holding every discovered test id and every id it ran.
* ``verify REPORT...`` fails closed unless every shard discovered the identical suite, the shards
  partition it exactly (no test in two shards, none in none), every assigned test ran and passed,
  and no test was skipped for a reason outside ``PERMITTED_SKIP_REASONS`` -- so a missing
  ``POSTGRES_TEST_DSN`` can no longer turn the Postgres tests into silent skips.

Assignment is by test module. A module not named in ``HEAVY_MODULES`` -- including a new module or
one that fails to import -- runs in ``core``, so the partition is total by construction. The heavy
list only balances wall-clock (measured from CI log timestamps); a stale entry costs time, never
coverage.
"""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Runtime-dominant modules on GitHub-hosted runners (see the CI profile in the PR that added this).
HEAVY_MODULES = frozenset({
    "test_dry_activation_e2e_postgres",
    "test_live_signals_v1",
    "test_live_signals_v1_postgres",
    "test_paper_incubation_v1",
    "test_paper_incubation_v1_postgres",
    "test_strategy_lab_validation_v1",
    "test_crypto_liquidity_capacity_v1_postgres",
    "test_historical_dataset_sealing_scale_postgres",
})
SHARDS = ("core", "heavy")

# The only skips CI accepts: inputs that are deliberately never present on a runner.
PERMITTED_SKIP_REASONS = (
    "TARDIS_FREE_SAMPLE_DIR is not set",
)


def _iter_tests(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def shard_of(test_id: str) -> str:
    return "heavy" if test_id.split(".", 1)[0] in HEAVY_MODULES else "core"


def run(shard: str, report_path: Path) -> int:
    # Mirror `python -m unittest discover -s tests`: the repository root is importable
    # (tests import `tests.*`), not this scripts/ directory.
    sys.path[:] = [str(ROOT), *(p for p in sys.path[1:])]
    discovered = list(_iter_tests(unittest.TestLoader().discover(str(ROOT / "tests"))))
    selected = [test for test in discovered if shard_of(test.id()) == shard]
    runner = unittest.TextTestRunner(verbosity=2, durations=25)
    result = runner.run(unittest.TestSuite(selected))
    report = {
        "shard": shard,
        "discovered": sorted(test.id() for test in discovered),
        "assigned": sorted(test.id() for test in selected),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "skipped": sorted([test.id(), reason] for test, reason in result.skipped),
        "successful": result.wasSuccessful(),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def verify(report_paths: list[Path]) -> list[str]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    problems = []
    names = sorted(report["shard"] for report in reports)
    if names != sorted(SHARDS):
        problems.append(f"expected one report per shard {sorted(SHARDS)}, got {names}")
    if not reports:
        return problems
    discovered = set(reports[0]["discovered"])
    if not discovered:
        problems.append("no tests were discovered")
    seen: dict[str, str] = {}
    for report in reports:
        shard = report["shard"]
        if set(report["discovered"]) != discovered:
            problems.append(f"{shard}: discovered a different suite than {reports[0]['shard']}")
        for test_id in report["assigned"]:
            # A TestCase imported into a second module is discovered twice under one id; both
            # copies follow that id to the same shard, so only a cross-shard repeat is a defect.
            if seen.get(test_id, shard) != shard:
                problems.append(f"{test_id}: assigned to both {seen[test_id]} and {shard}")
            seen[test_id] = shard
        if report["tests_run"] != len(report["assigned"]):
            problems.append(f"{shard}: ran {report['tests_run']} of {len(report['assigned'])} assigned tests")
        if not report["successful"]:
            problems.append(f"{shard}: {report['failures']} failures, {report['errors']} errors, "
                            f"{report['unexpected_successes']} unexpected successes")
        for test_id, reason in report["skipped"]:
            if not any(permitted in reason for permitted in PERMITTED_SKIP_REASONS):
                problems.append(f"{test_id}: skipped in CI ({reason})")
    missing = discovered - set(seen)
    if missing:
        problems.append(f"{len(missing)} discovered tests ran in no shard, e.g. {sorted(missing)[:5]}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--shard", choices=SHARDS, required=True)
    run_parser.add_argument("--report", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("reports", type=Path, nargs="+")
    args = parser.parse_args()
    if args.command == "run":
        return run(args.shard, args.report)
    problems = verify(args.reports)
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.reports]
    total = len(reports[0]["discovered"])
    skipped = sum(len(report["skipped"]) for report in reports)
    split = ", ".join(f"{report['shard']}={report['tests_run']}" for report in reports)
    print(f"PASS: {total} discovered tests ran exactly once across shards ({split}); {skipped} permitted skips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
