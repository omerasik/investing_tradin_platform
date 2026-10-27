"""Prepare/serve a disposable BTC/ETH/SOL research terminal; no original DB access.

  .venv/Scripts/python scripts/terminal_lite.py prepare
  .venv/Scripts/python scripts/terminal_lite.py serve
  .venv/Scripts/python scripts/terminal_lite.py stop

Only local existing evidence is read. No acquisition, SQL dump/copy, study
generation, Railway mutation or external data transfer. Local state and secrets
are stored outside the repository in ~/.trade_platform/terminal-lite.
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
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
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


def prepare_subset(archive: Path, source: Path, target: Path, day: date) -> dict:
    from trade_platform.bybit_public_archive_v1 import bybit_public_trade_archive_contract_v1
    from trade_platform.public_archive_research_bars_v1 import (
        _paths,
        build_research_bar_dataset_v1,
        load_day_bars_v1,
        load_file_manifest_v1,
        load_research_bar_dataset_v1,
    )
    from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
    from trade_platform.strategy_lab_study_v1 import UNTOUCHED_HOLDOUT_BOUNDARY_V1

    if (
        datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        >= UNTOUCHED_HOLDOUT_BOUNDARY_V1
    ):
        raise ValueError("lite_holdout_forbidden")
    if any(
        target.resolve() == original.resolve()
        or original.resolve() in target.resolve().parents
        or target.resolve() in original.resolve().parents
        for original in (source, archive)
    ):
        raise ValueError("lite_subset_must_be_separate_from_source")
    src, dst = ResearchFrameStoreV1(source), ResearchFrameStoreV1(target)
    contract = bybit_public_trade_archive_contract_v1()
    selected = []
    total = 0
    for symbol in SYMBOLS:
        record = load_day_bars_v1(archive, symbol, day)
        file = load_file_manifest_v1(archive, symbol, day)
        if (
            record is None
            or record.file_identity != file.identity()
            or record.symbol != symbol
            or record.utc_day != day.isoformat()
        ):
            raise ValueError("lite_day_provenance_mismatch")
        if (
            file.source_id != str(contract.source_id)
            or file.url != contract.url(symbol, day)
            or file.symbol != symbol
            or file.utc_day != day.isoformat()
        ):
            raise ValueError("lite_source_contract_mismatch")
        manifest = src.load_manifest(record.bar_frame_manifest_hash)
        if (
            manifest.row_count > 1440
            or manifest.row_count < 1
            or manifest.total_bytes > MAX_SUBSET_BYTES
        ):
            raise ValueError("lite_day_resource_bound_exceeded")
        if (
            manifest.logical_content_hash != record.bar_logical_content_hash
            or manifest.row_count != record.bar_count
        ):
            raise ValueError("lite_day_frame_mismatch")
        if (
            manifest.lineage.get("source_file_sha256") != file.sha256
            or manifest.lineage.get("symbol") != symbol
        ):
            raise ValueError("lite_frame_lineage_mismatch")
        src.verify(manifest)
        total += manifest.total_bytes
        if total > MAX_SUBSET_BYTES:
            raise ValueError("lite_subset_resource_bound_exceeded")
        selected.append((symbol, record, manifest))
    # All three proven before writing; no partial catalogue is presented as ready.
    target.mkdir(parents=True, exist_ok=True)
    if (target / "v1" / "datasets").exists():
        raise ValueError("lite_target_already_prepared_use_existing_or_new_directory")
    evidence = []
    for symbol, record, manifest in selected:
        for obj in manifest.objects:
            destination = dst.object_path(obj)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src.object_path(obj), destination)
        destination = dst.manifest_path(manifest.manifest_hash)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src.manifest_path(manifest.manifest_hash), destination)
        original = _paths(archive, symbol, day)
        copied = _paths(target / "archive-provenance", symbol, day)
        for kind in ("manifest", "day_bars", "retention"):
            if original[kind].is_file():
                copied[kind].parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original[kind], copied[kind])
        dataset = build_research_bar_dataset_v1(
            target / "archive-provenance", symbol, day, day, store=dst, require_continuous=True
        )
        verified = load_research_bar_dataset_v1(dst, dataset.dataset_version_id)
        # A one-day window has exactly the original day rows and Decimal digest.
        if (
            verified.identity["bar_frame"]["logical_content_hash"]
            != record.bar_logical_content_hash
        ):
            raise ValueError("lite_window_changed_source_rows")
        evidence.append(
            {
                "symbol": symbol,
                "dataset_version_id": str(dataset.dataset_version_id),
                "content_hash": dataset.content_hash,
                "manifest_hash": dataset.bar_frame_manifest_hash,
                "source_day_manifest_hash": manifest.manifest_hash,
                "rows": manifest.row_count,
                "file_identity": dict(record.file_identity),
            }
        )
    report = {
        "version": "terminal-lite-v1",
        "classification": "REAL_HISTORICAL_T2_EVENT_TIME",
        "utc_day": day.isoformat(),
        "datasets": evidence,
        "studies": "UNAVAILABLE_NO_SUPPORTED_OFFLINE_LEDGER_IMPORT",
        "source_verification": "RETAINED_DERIVED_FRAME_HASHES_AND_ARCHIVE_FILE_IDENTITIES; RAW_NOT_REDOWNLOADED",
        "blocked": ["research_commands", "holdout", "paper_execution", "brokers", "live_trading"],
    }
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    if size > MAX_SUBSET_BYTES:
        raise ValueError("lite_final_subset_resource_bound_exceeded")
    report["subset_bytes_before_report"] = size
    (target / "lite-provenance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


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
        LITE_RESEARCH_ROOT=str(STATE / "evidence"),
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
    parser.add_argument("command", choices=("prepare", "serve", "verify", "stop"))
    parser.add_argument("--day", type=date.fromisoformat, default=date(2026, 8, 18))
    parser.add_argument(
        "--archive-root", type=Path, default=Path.home() / ".trade_platform/public-archive"
    )
    parser.add_argument(
        "--source-root", type=Path, default=Path.home() / ".trade_platform/research-data"
    )
    args = parser.parse_args()
    if args.command == "prepare":
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
            LITE_TEST_RESEARCH_ROOT=str(STATE / "evidence"),
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "tests.test_terminal_lite_v1",
                "tests.terminal_lite_acceptance",
                "tests.test_instrument_chart_v1",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=60,
        )
    elif args.command == "serve":
        serve()
    else:
        compose("stop", "postgres")


if __name__ == "__main__":
    main()
