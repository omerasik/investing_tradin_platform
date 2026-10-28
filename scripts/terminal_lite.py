"""Prepare/serve a disposable BTC/ETH/SOL research terminal; no original DB access.

  .venv/Scripts/python scripts/terminal_lite.py prepare
  .venv/Scripts/python scripts/terminal_lite.py prepare-window --from 2026-08-01 --to 2026-08-14
  .venv/Scripts/python scripts/terminal_lite.py activate --subset subsets/2026-08-01_2026-08-14
  .venv/Scripts/python scripts/terminal_lite.py serve
  .venv/Scripts/python scripts/terminal_lite.py stop

Only local existing evidence is read. No acquisition, SQL dump/copy, study
generation, Railway mutation or external data transfer. Local state and secrets
are stored outside the repository in ~/.trade_platform/terminal-lite.
prepare-window/activate never connect to SQL; prepare remains the explicit
first-install bootstrap for the independent Lite database only.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
STATE = Path.home() / ".trade_platform" / "terminal-lite"
MAX_SUBSET_BYTES = 16 * 1024 * 1024


def clean_env() -> dict[str, str]:
    # An inherited research/test DSN must never override the migration target.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("POSTGRES_", "TRADE_PLATFORM_", "LITE_")) and k != "DATABASE_URL"
    }
    env.update(
        OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1"
    )
    return env


def settings() -> dict[str, str]:
    STATE.mkdir(parents=True, exist_ok=True)
    path = STATE / "settings.local.json"
    if not path.exists():
        data = {
            name: secrets.token_hex(32)
            for name in (
                "db_password",
                "reader_password",
                "operator_token",
                "session_secret",
                "dashboard_password",
            )
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        path.chmod(0o600)
    return json.loads(path.read_text(encoding="utf-8"))


def compose(*args: str) -> None:
    env = clean_env()
    env["LITE_DB_PASSWORD"] = settings()["db_password"]
    subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "trade-terminal-lite",
            "-f",
            str(ROOT / "compose.lite.yml"),
            *args,
        ],
        env=env,
        check=True,
        timeout=120,
    )


def prepare_subset(
    archive: Path, source: Path, target: Path, day: date, last: date | None = None
) -> dict:
    """Publish a separate verified subset atomically; never overwrite a prior root."""
    from trade_platform.public_archive_research_bars_v1 import (
        _paths,
        build_research_bar_dataset_v1,
    )
    from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
    from trade_platform.terminal_lite_evidence_v1 import (
        day_binding,
        verified_day,
        verify_lite_subset,
        window_days,
    )

    days = window_days(day, last or day)
    if any(
        target.resolve() == original.resolve()
        or original.resolve() in target.resolve().parents
        or target.resolve() in original.resolve().parents
        for original in (source, archive)
    ):
        raise ValueError("lite_subset_must_be_separate_from_source")
    if target.exists():
        raise ValueError("lite_target_already_exists_preserve_previous_subset")
    src = ResearchFrameStoreV1(source)
    selected = {symbol: [verified_day(archive, src, symbol, d) for d in days] for symbol in SYMBOLS}
    total = sum(m.total_bytes for frames in selected.values() for _, m in frames)
    if total > MAX_SUBSET_BYTES:
        raise ValueError("lite_subset_resource_bound_exceeded")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Staging is a dedicated child of the new target's parent; originals and
    # existing subsets are never removed. A failed build publishes no root.
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}.staging-", dir=target.parent
    ) as temporary:
        staging = Path(temporary) / "evidence"
        dst = ResearchFrameStoreV1(staging)
        evidence = []
        for symbol, frames in selected.items():
            for (record, manifest), d in zip(frames, days, strict=True):
                for obj in manifest.objects:
                    destination = dst.object_path(obj)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src.object_path(obj), destination)
                destination = dst.manifest_path(manifest.manifest_hash)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src.manifest_path(manifest.manifest_hash), destination)
                original = _paths(archive, symbol, d)
                copied = _paths(staging / "archive-provenance", symbol, d)
                for kind in ("manifest", "day_bars", "retention"):
                    if original[kind].is_file():
                        copied[kind].parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(original[kind], copied[kind])
            dataset = build_research_bar_dataset_v1(
                staging / "archive-provenance",
                symbol,
                days[0],
                days[-1],
                store=dst,
                require_continuous=True,
            )
            item = {
                "symbol": symbol,
                "dataset_version_id": str(dataset.dataset_version_id),
                "content_hash": dataset.content_hash,
                "manifest_hash": dataset.bar_frame_manifest_hash,
                "rows": dataset.identity["bar_frame"]["row_count"],
            }
            if len(days) == 1:
                record, manifest = frames[0]
                item.update(
                    source_day_manifest_hash=manifest.manifest_hash,
                    file_identity=dict(record.file_identity),
                )
            else:
                item["day_frames"] = [day_binding(r, m) for r, m in frames]
            evidence.append(item)
        report = {
            "version": "terminal-lite-v1",
            "classification": "REAL_HISTORICAL_T2_EVENT_TIME",
            "datasets": evidence,
            "studies": "UNAVAILABLE_NO_SUPPORTED_OFFLINE_LEDGER_IMPORT",
            "source_verification": "RETAINED_DERIVED_FRAME_HASHES_AND_ARCHIVE_FILE_IDENTITIES; RAW_NOT_REDOWNLOADED",
            "blocked": [
                "research_commands",
                "holdout",
                "paper_execution",
                "brokers",
                "live_trading",
            ],
        }
        if len(days) == 1:
            report["utc_day"] = str(days[0])
        else:
            report.update(
                first_utc_day=str(days[0]), last_utc_day=str(days[-1]), day_count=len(days)
            )
        report["subset_bytes_before_report"] = sum(
            p.stat().st_size for p in staging.rglob("*") if p.is_file()
        )
        (staging / "lite-provenance.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        verify_lite_subset(staging)
        # rename refuses an existing destination; previous roots stay immutable.
        staging.rename(target)
    return report


def subset_path(relative: str) -> Path:
    candidate = (STATE / relative).resolve()
    if STATE.resolve() not in candidate.parents:
        raise ValueError("lite_selection_must_stay_inside_state_directory")
    return candidate


def active_root() -> Path:
    from trade_platform.terminal_lite_evidence_v1 import report_days, verify_lite_subset

    selection = STATE / "active-subset.local.json"
    if not selection.exists():
        return STATE / "evidence"  # legacy fallback, never rewritten or removed
    pointer = json.loads(selection.read_text(encoding="utf-8"))
    root = subset_path(pointer["relative_root"])
    report = verify_lite_subset(root)
    days = report_days(report)
    if (
        pointer["content_hashes"] != {v["symbol"]: v["content_hash"] for v in report["datasets"]}
        or pointer["first_utc_day"] != str(days[0])
        or pointer["last_utc_day"] != str(days[-1])
    ):
        raise ValueError("lite_active_selection_identity_mismatch")
    return root


def activate(relative: str, *, recover: bool = False) -> None:
    from trade_platform.terminal_lite_evidence_v1 import report_days, verify_lite_subset

    root = subset_path(relative)
    report = verify_lite_subset(root)
    try:
        previous = active_root()
        verify_lite_subset(previous)
    except (ValueError, OSError, KeyError, TypeError):
        if not recover:
            raise
        previous = None  # explicit recovery still requires the requested root to verify
    days = report_days(report)
    pointer = {
        "relative_root": str(root.relative_to(STATE.resolve())),
        "previous_relative_root": (
            str(previous.resolve().relative_to(STATE.resolve())) if previous else None
        ),
        "first_utc_day": str(days[0]),
        "last_utc_day": str(days[-1]),
        "content_hashes": {v["symbol"]: v["content_hash"] for v in report["datasets"]},
    }
    temporary = STATE / "active-subset.pending.local.json"
    temporary.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    os.replace(temporary, STATE / "active-subset.local.json")
    print(
        json.dumps(
            {
                "activated": pointer["relative_root"],
                "previous": pointer["previous_relative_root"],
                "first_utc_day": str(days[0]),
                "last_utc_day": str(days[-1]),
            }
        )
    )


def bounded_prepare(first: date, last: date, archive: Path, source: Path) -> None:
    """One numeric thread/core, 512 MiB process admission, 120 s wall deadline."""
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "prepare-window-worker",
        "--from",
        str(first),
        "--to",
        str(last),
        "--archive-root",
        str(archive),
        "--source-root",
        str(source),
    ]
    process = subprocess.Popen(command, env=clean_env(), stdin=subprocess.PIPE, text=True)
    kernel = job = None
    try:
        if os.name == "nt":
            import ctypes

            from scripts.research_performance import _windows_job

            kernel, job = _windows_job(process, memory_bytes=512 * 1024 * 1024)
            mask, system = ctypes.c_size_t(), ctypes.c_size_t()
            kernel.GetProcessAffinityMask.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            if not kernel.GetProcessAffinityMask(
                int(process._handle), ctypes.byref(mask), ctypes.byref(system)
            ):
                raise RuntimeError("lite_cpu_affinity_query_failed")
            if not kernel.SetProcessAffinityMask(int(process._handle), mask.value & -mask.value):
                raise RuntimeError("lite_cpu_affinity_limit_failed")
        process.communicate(input="admitted\n", timeout=120)
        if process.returncode:
            raise RuntimeError(f"lite_bounded_preparation_failed:{process.returncode}")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)
        if kernel is not None and job is not None:
            kernel.CloseHandle(job)


def prepare_window_worker(first: date, last: date, archive: Path, source: Path) -> None:
    if sys.stdin.readline().strip() != "admitted":
        raise ValueError("lite_preparation_requires_supervisor")
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        if not hasattr(os, "sched_setaffinity"):
            raise RuntimeError("lite_bounded_preparation_requires_cpu_affinity_support")
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    import pyarrow as pa

    from scripts.research_data_plane import peak_memory_bytes
    from trade_platform.terminal_lite_evidence_v1 import verify_lite_subset

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    started, cpu = time.perf_counter(), time.process_time()
    target = STATE / "subsets" / f"{first}_{last}"
    report = (
        verify_lite_subset(target)
        if target.exists()
        else prepare_subset(archive, source, target, first, last)
    )
    from trade_platform.terminal_lite_evidence_v1 import report_days

    days = report_days(report)
    if days[0] != first or days[-1] != last:
        raise ValueError("lite_existing_window_does_not_match_selection")
    measurement = {
        "first_utc_day": str(first),
        "last_utc_day": str(last),
        "wall_seconds": time.perf_counter() - started,
        "cpu_seconds": time.process_time() - cpu,
        "peak_rss_bytes": peak_memory_bytes(),
        "memory_limit_bytes": 512 * 1024 * 1024,
        "wall_deadline_seconds": 120,
        "numeric_threads": 1,
        "cpu_cores": 1,
        "evidence_bytes": sum(p.stat().st_size for p in target.rglob("*") if p.is_file()),
        "evidence_limit_bytes": MAX_SUBSET_BYTES,
        "datasets": [
            {k: v for k, v in item.items() if k != "day_frames"} for item in report["datasets"]
        ],
    }
    (STATE / f"preparation-{first}_{last}.local.json").write_text(
        json.dumps(measurement, indent=2), encoding="utf-8"
    )
    print(json.dumps(measurement, indent=2), flush=True)


def prepare_database() -> None:
    data = settings()
    compose("up", "-d", "--wait", "postgres")
    # Construct this target; never use an environment DSN, linked project or dump.
    dsn = f"postgresql://lite_owner:{data['db_password']}@127.0.0.1:5441/trade_terminal_lite"
    initialize_database(dsn, data["reader_password"])


def initialize_database(dsn: str, reader_password: str) -> None:
    """Fresh canonical schema plus SELECT-only login, over a local/SSH loopback.

    Also reusable after owner approval for a separately provisioned Railway DB
    forwarded to localhost:5541. Never opens the original host/port/database.
    """
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict

    identity = conninfo_to_dict(dsn)
    if (
        identity.get("host") != "127.0.0.1"
        or identity.get("port") not in {"5441", "5541"}
        or identity.get("dbname") != "trade_terminal_lite"
        or identity.get("user") != "lite_owner"
    ):
        raise ValueError("lite_bootstrap_requires_dedicated_loopback_target")
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT current_database(), current_user, current_setting('server_version_num')::int"
        ).fetchone()
        if row[:2] != ("trade_terminal_lite", "lite_owner") or not 160000 <= row[2] < 170000:
            raise ValueError("lite_database_identity_mismatch")
    env = clean_env()
    env["POSTGRES_DSN"] = dsn
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=90,
    )
    with psycopg.connect(dsn) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='lite_reader'").fetchone():
            conn.execute(
                sql.SQL(
                    "CREATE ROLE lite_reader LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                ).format(sql.Literal(reader_password))
            )
        conn.execute("REVOKE ALL ON DATABASE trade_terminal_lite FROM PUBLIC")
        conn.execute("GRANT CONNECT ON DATABASE trade_terminal_lite TO lite_reader")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute("GRANT USAGE ON SCHEMA public TO lite_reader")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO lite_reader")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE lite_owner IN SCHEMA public GRANT SELECT ON TABLES TO lite_reader"
        )
        conn.execute("ALTER ROLE lite_reader SET default_transaction_read_only = on")
        conn.execute("ALTER ROLE lite_reader SET statement_timeout = '5s'")


def serve() -> None:
    data = settings()
    env = clean_env()
    env.update(
        LITE_READ_DSN=f"postgresql://lite_reader:{data['reader_password']}@127.0.0.1:5441/trade_terminal_lite",
        LITE_RESEARCH_ROOT=str(active_root()),
        TRADE_PLATFORM_OPERATOR_TOKEN=data["operator_token"],
        TRADE_PLATFORM_LITE="1",
        TRADE_PLATFORM_API_BASE_URL="http://127.0.0.1:8011",
        TRADE_PLATFORM_DASHBOARD_ORIGIN="http://127.0.0.1:3011",
        TRADE_PLATFORM_SESSION_SECRET=data["session_secret"],
        TRADE_PLATFORM_DASHBOARD_VIEW_TOKEN=data["dashboard_password"],
        TRADE_PLATFORM_DASHBOARD_CONFIG_PATH=str(STATE / "dashboard.local.json"),
        NEXT_TELEMETRY_DISABLED="1",
        NODE_OPTIONS="--max-old-space-size=512",
    )
    (STATE / "dashboard.local.json").write_text("{}", encoding="utf-8")
    node = shutil.which("node")
    if not node:
        raise ValueError("node_required")
    for port in (8011, 3011):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(f"lite_port_in_use:{port}")
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "trade_platform.terminal_lite_v1:create_lite_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8011",
            "--workers",
            "1",
            "--limit-concurrency",
            "32",
            "--no-access-log",
        ],
        [
            node,
            str(ROOT / "web/node_modules/next/dist/bin/next"),
            "dev",
            "--hostname",
            "127.0.0.1",
            "--port",
            "3011",
        ],
    ]
    processes = []
    try:
        for command, cwd in zip(commands, (ROOT, ROOT / "web"), strict=True):
            processes.append(subprocess.Popen(command, cwd=cwd, env=env))
        print(
            f"Lite terminal: http://127.0.0.1:3011/terminal\nLogin password is dashboard_password in {STATE / 'settings.local.json'}",
            flush=True,
        )
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
        raise RuntimeError("lite_child_exited_check_service_output")
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    process.terminate()
        for process in processes:
            process.wait(timeout=15)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "prepare-window",
            "prepare-window-worker",
            "activate",
            "serve",
            "verify",
            "stop",
        ),
    )
    parser.add_argument("--from", dest="first", type=date.fromisoformat)
    parser.add_argument("--to", dest="last", type=date.fromisoformat)
    parser.add_argument("--subset", help="Explicit state-relative subset for verify or activation")
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Explicit activation of a verified root when the current selection is broken",
    )
    parser.add_argument("--day", type=date.fromisoformat, default=date(2026, 8, 18))
    parser.add_argument(
        "--archive-root", type=Path, default=Path.home() / ".trade_platform/public-archive"
    )
    parser.add_argument(
        "--source-root", type=Path, default=Path.home() / ".trade_platform/research-data"
    )
    args = parser.parse_args()
    if args.command in {"prepare-window", "prepare-window-worker"}:
        from trade_platform.terminal_lite_evidence_v1 import window_days

        if args.first is None or args.last is None:
            parser.error("prepare-window requires explicit --from and --to; no fallback window")
        if len(window_days(args.first, args.last)) < 7:
            parser.error("multi-day preparation requires at least seven verified days")
        if args.command == "prepare-window":
            bounded_prepare(args.first, args.last, args.archive_root, args.source_root)
        else:
            prepare_window_worker(args.first, args.last, args.archive_root, args.source_root)
    elif args.command == "activate":
        if not args.subset:
            parser.error(
                "activate requires an explicit --subset relative to the Lite state directory"
            )
        activate(args.subset, recover=args.recover)
    elif args.command == "prepare":
        target = STATE / "evidence"
        if not (target / "lite-provenance.json").exists():
            report = prepare_subset(args.archive_root, args.source_root, target, args.day)
            print(json.dumps(report, indent=2))
        else:
            from uuid import UUID

            from trade_platform.public_archive_research_bars_v1 import load_research_bar_dataset_v1
            from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1

            report = json.loads((target / "lite-provenance.json").read_text(encoding="utf-8"))
            if report["utc_day"] != args.day.isoformat():
                raise ValueError("lite_existing_subset_has_different_day")
            for item in report["datasets"]:
                load_research_bar_dataset_v1(
                    ResearchFrameStoreV1(target), UUID(item["dataset_version_id"])
                )
        prepare_database()
    elif args.command == "verify":
        data = settings()
        env = clean_env()
        env.update(
            LITE_TEST_READ_DSN=f"postgresql://lite_reader:{data['reader_password']}@127.0.0.1:5441/trade_terminal_lite",
            LITE_TEST_RESEARCH_ROOT=str(subset_path(args.subset) if args.subset else active_root()),
        )
        for modules in (
            (
                "tests.test_terminal_lite_v1",
                "tests.test_terminal_lite_window_v1",
                "tests.test_instrument_chart_v1",
            ),
            ("tests.terminal_lite_acceptance",),
        ):
            subprocess.run(
                [sys.executable, "-m", "unittest", *modules],
                cwd=ROOT,
                env=env,
                check=True,
                timeout=120,
            )

    elif args.command == "serve":
        serve()
    else:
        compose("stop", "postgres")


if __name__ == "__main__":
    main()
