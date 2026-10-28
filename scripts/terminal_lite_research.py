"""Explicit, offline BTC Lite pilot; never imported by the browser-facing API.

Run `python scripts/terminal_lite_research.py run` on the admitted local Windows
host. The canonical Strategy Lab command drains one unchanged 84-point study
in the supervised process; subsequent invocation is an audited no-op. No DSN,
family, dataset, retry, promotion or execution override is exposed.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

from scripts.terminal_lite import STATE, active_root, clean_env, settings

DATASET_ID = UUID("e5942fd6-42f4-5ad2-8b34-09bae2996db6")
DATASET_HASH = "6368e6460d97cd07471098f32afbf3bd82ffb477d26fa7042e5e0a9576c9aa1e"  # pragma: allowlist secret
STUDY_ID = UUID("020fe0da-b880-54fb-be1b-52cee81bbd7f")
MEMORY_BYTES = 512 * 1024**2
DISK_FLOOR = 24 * 1024**3
DATABASE_CEILING = 64 * 1024**2
DEADLINE = 120
TABLES = (
    "strategy_lab_studies", "strategy_lab_trials", "strategy_lab_trial_queue",
    "strategy_lab_trial_results", "strategy_lab_trial_events", "strategy_lab_study_manifests",
)


def checked_writer_dsn(dsn: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    fields = conninfo_to_dict(dsn)
    if any(fields.get(k) != v for k, v in {
        "host": "127.0.0.1", "port": "5441", "dbname": "trade_terminal_lite", "user": "lite_owner",
    }.items()) or set(fields) - {"host", "port", "dbname", "user", "password", "connect_timeout", "options"}:
        raise ValueError("lite_offline_writer_target_forbidden")
    return dsn


def checked_study(root: Path):
    from scripts.strategy_lab import build_study
    from trade_platform.terminal_lite_evidence_v1 import report_days, verify_lite_subset

    report = verify_lite_subset(root)
    days = report_days(report)
    btc = next(item for item in report["datasets"] if item["symbol"] == "BTCUSDT")
    if (str(days[0]), str(days[-1]), btc["dataset_version_id"], btc["content_hash"], btc["rows"]) != (
        "2026-08-01", "2026-08-14", str(DATASET_ID), DATASET_HASH, 20160,
    ):
        raise ValueError("lite_pilot_requires_exact_verified_btc_window")
    study = build_study("breakout_channel", DATASET_ID, root)
    if study.study_id != STUDY_ID or study.planned_trial_count != 84:
        raise ValueError("lite_pilot_canonical_study_changed")
    return study


def container_identity() -> dict[str, str]:
    container = json.loads(subprocess.check_output(
        ["docker", "inspect", "trade-terminal-lite-postgres-1"], text=True, timeout=10,
    ))[0]
    labels, config = container["Config"]["Labels"], container["HostConfig"]
    networks = list(container["NetworkSettings"]["Networks"].values())
    if (labels.get("com.docker.compose.project") != "trade-terminal-lite"
            or labels.get("com.docker.compose.service") != "postgres"
            or config["PortBindings"] != {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5441"}]}
            or config["Memory"] != 256 * 1024**2 or config["NanoCpus"] != 500000000
            or not container["State"]["Running"] or len(networks) != 1 or not networks[0]["IPAddress"]):
        raise ValueError("lite_pilot_requires_isolated_capped_container")
    return {"container_id": container["Id"], "server_address": networks[0]["IPAddress"]}


def database_proof(database: Any, expected: dict[str, str], study: Any) -> dict[str, Any]:
    """Only SELECTs in a server-enforced read-only transaction, before any write."""
    with database.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute(
            "SELECT current_database(), current_user, current_setting('server_version_num')::int, "
            "host(inet_server_addr()), system_identifier::text FROM pg_control_system()"
        ).fetchone()
        if (row[0:2] != ("trade_terminal_lite", "lite_owner") or not 160000 <= row[2] < 170000
                or row[3] != expected["server_address"]):
            raise ValueError("lite_pilot_server_identity_mismatch")
        if conn.execute("SELECT version_num FROM alembic_version").fetchall() != [("20261020_0067",)]:
            raise ValueError("lite_pilot_schema_mismatch")
        reader = conn.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolconfig "
            "FROM pg_roles WHERE rolname='lite_reader'"
        ).fetchone()
        if (reader is None or any(reader[:4]) or "default_transaction_read_only=on" not in (reader[4] or [])
                or conn.execute(
                    "SELECT has_database_privilege('lite_reader',current_database(),'CREATE') OR "
                    "has_schema_privilege('lite_reader','public','CREATE') OR EXISTS "
                    "(SELECT 1 FROM information_schema.role_table_grants WHERE grantee='lite_reader' "
                    "AND table_schema='public' AND privilege_type<>'SELECT') OR EXISTS "
                    "(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
                    "WHERE r.rolname='lite_reader')"
                ).fetchone() != (False,)):
            raise ValueError("lite_pilot_reader_must_remain_select_only")
        studies = conn.execute("SELECT study_id,content_hash,identity FROM strategy_lab_studies").fetchall()
        if studies and studies != [(study.study_id, study.content_hash, study.identity())]:
            raise ValueError("lite_pilot_refuses_another_study")
        if studies:
            persisted = conn.execute(
                "SELECT trial_id,content_hash,ordinal,space_index,parameters FROM strategy_lab_trials "
                "WHERE study_id=%s ORDER BY ordinal", (study.study_id,),
            ).fetchall()
            canonical = [(t.trial_id, t.content_hash, t.ordinal, t.space_index, dict(t.parameters))
                         for t in study.trials()]
            if persisted != canonical:
                raise ValueError("lite_pilot_persisted_trial_identity_mismatch")
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        if size > DATABASE_CEILING:
            raise ValueError("lite_pilot_database_exceeds_budget")
    proof = {**expected, "database": row[0], "writer": row[1], "postgres_version": row[2],
             "system_identifier": row[4], "database_bytes": size}
    pin = STATE / "research-cluster.local.json"
    identity = {k: proof[k] for k in ("database", "writer", "system_identifier")}
    if pin.exists() and json.loads(pin.read_text()) != identity:
        raise ValueError("lite_pilot_cluster_pin_mismatch")
    if not pin.exists():
        with pin.open("x", encoding="utf-8") as output:
            json.dump(identity, output)
    return proof


def snapshot(database: Any) -> dict[str, int]:
    from psycopg import sql

    with database.transaction() as conn:
        return {name: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))).fetchone()[0]
                for name in TABLES}


def execute_pilot(database: Any, dsn: str, root: Path, study: Any, expected: dict[str, str]) -> dict[str, Any]:
    from scripts.strategy_lab import run_strategy_lab_command
    from trade_platform.strategy_lab_ledger_v1 import (
        PostgresStrategyLabLedgerV1,
        result_content_hash_v1,
    )
    from trade_platform.strategy_lab_manifest_v1 import build_study_manifest_v1

    proof = database_proof(database, expected, study)
    before = snapshot(database)
    output = run_strategy_lab_command("search", study=study, dsn=dsn, database=database,
                                      data_root=root, workers=1, in_process=True)
    ledger = PostgresStrategyLabLedgerV1(database)
    trials = {trial.trial_id: trial for trial in study.trials()}
    results = ledger.results(study.study_id)
    for result in results:
        trial = trials[result.trial_id]
        if result.result_content_hash != result_content_hash_v1(
            trial_content_hash=trial.content_hash, outcome=result.outcome, metrics=result.metrics,
            cost_policy_slot=study.cost_policy_slot,
        ):
            raise ValueError("lite_pilot_persisted_result_hash_mismatch")
    after = snapshot(database)
    manifest = build_study_manifest_v1(ledger, study) if output["finished"] else None
    if (after["strategy_lab_studies"] != 1 or after["strategy_lab_trials"] != 84
            or sum(output["states"].values()) != 84):
        raise ValueError("lite_pilot_trial_accounting_mismatch")
    first = next((r for r in results if r.outcome.value == "EVALUATED"), None)
    final_proof = database_proof(database, expected, study)
    return {**output, "study_content_hash": study.content_hash, "database_proof": proof,
            "database_bytes_after": final_proof["database_bytes"], "before": before, "after": after,
            "idempotent_noop": bool(before["strategy_lab_studies"] and before == after),
            "manifest": dict(manifest.identity) if manifest else None,
            "first_evaluated_trial": None if first is None else {
                "trial_id": str(first.trial_id), "ordinal": trials[first.trial_id].ordinal,
                "parameters": dict(trials[first.trial_id].parameters), "metrics": dict(first.metrics),
                "result_content_hash": first.result_content_hash},
            "limitations": ["14_DAY_DEVELOPMENT_SAMPLE", "SEARCH_NON_AUTHORITATIVE", "GROSS_NON_PROMOTABLE",
                            "TRADING_COSTS_UNVERIFIED", "PROFITABILITY_NOT_VALIDATED",
                            "NO_HOLDOUT_OR_EXECUTION", "NO_CANDIDATE_SELECTION_OR_AUTHORITY_RERUN"]}


def worker(expected: dict[str, str]) -> dict[str, Any]:
    import pyarrow as pa

    from scripts.research_performance import _resources
    from trade_platform.persistence import PostgresDatabase

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    started, cpu = time.perf_counter(), time.process_time()
    root = active_root()
    study = checked_study(root)
    config = settings()
    dsn = checked_writer_dsn(
        f"postgresql://lite_owner:{config['db_password']}@127.0.0.1:5441/trade_terminal_lite"
        "?options=-c%20statement_timeout%3D5000%20-c%20lock_timeout%3D2000"
    )
    database = PostgresDatabase(dsn)
    try:
        result = execute_pilot(database, dsn, root, study, expected)
    finally:
        database.close()
    return {**result, "wall_seconds": time.perf_counter() - started, "cpu_seconds": time.process_time() - cpu,
            "resources": _resources()}


def admitted(host: dict[str, int], *, initial: bool) -> bool:
    reserve = MEMORY_BYTES * (2 if initial else 1)
    return (host["available_physical_bytes"] >= reserve and host["commit_headroom_bytes"] >= 1024**3
            and host["disk_free_bytes"] >= DISK_FLOOR)


def run() -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("lite_pilot_bounded_supervisor_requires_windows")
    from scripts.research_loading_performance import host_memory
    from scripts.research_performance import _windows_job

    before = host_memory()
    if not admitted(before, initial=True):
        raise RuntimeError("lite_pilot_host_resource_admission_refused")
    expected = container_identity()
    # Use the base interpreter to avoid Windows venv launcher children: the Job
    # Object admits exactly one process, with one core and 512 MiB commit/WS caps.
    env = clean_env()
    env["__PYVENV_LAUNCHER__"] = sys.executable
    child = subprocess.Popen([sys._base_executable, str(Path(__file__).resolve()), "worker"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env, cwd=ROOT)
    kernel = job = None
    started = time.monotonic()
    minimum = dict(before)
    log = STATE / "research-pilot"
    log.mkdir(exist_ok=True)
    stamp = str(time.time_ns())
    try:
        kernel, job = _windows_job(child, memory_bytes=MEMORY_BYTES)
        handle = ctypes.c_void_p(int(child._handle))
        kernel.SetProcessWorkingSetSizeEx.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_ulong]
        if not kernel.SetProcessWorkingSetSizeEx(handle, 16 * 1024**2, MEMORY_BYTES, 0x4 | 0x2):
            raise RuntimeError("lite_pilot_working_set_cap_failed")
        mask, system = ctypes.c_size_t(), ctypes.c_size_t()
        kernel.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        if (not kernel.GetProcessAffinityMask(handle, ctypes.byref(mask), ctypes.byref(system))
                or not kernel.SetProcessAffinityMask(handle, mask.value & -mask.value)):
            raise RuntimeError("lite_pilot_single_core_cap_failed")
        token: str | None = json.dumps(expected) + "\n"
        while True:
            host = host_memory()
            minimum = {key: min(value, host[key]) for key, value in minimum.items()}
            if not admitted(host, initial=False) or time.monotonic() - started > DEADLINE:
                raise RuntimeError("lite_pilot_host_guard_or_wall_deadline")
            try:
                stdout, stderr = child.communicate(input=token, timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                token = None
        (log / f"{stamp}.log").write_text(stdout + stderr, encoding="utf-8")
        if child.returncode:
            raise RuntimeError(f"lite_pilot_failed_see_private_log:{stamp}")
        result = json.loads(stdout.splitlines()[-1])
        result.update(supervisor_wall_seconds=time.monotonic() - started, host_minimum=minimum,
                      limits={"memory_bytes": MEMORY_BYTES, "seconds": DEADLINE, "workers": 1,
                              "cores": 1, "numeric_threads": 1, "disk_floor": DISK_FLOOR,
                              "database_ceiling": DATABASE_CEILING})
        (log / f"{stamp}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    except Exception as error:
        if child.poll() is None:
            child.kill()
        stdout, stderr = child.communicate(timeout=10)
        (log / f"{stamp}.log").write_text(stdout + stderr, encoding="utf-8")
        (log / f"{stamp}.json").write_text(json.dumps({
            "status": "STOPPED_OR_FAILED", "reason": str(error), "host_minimum": minimum,
            "wall_seconds": time.monotonic() - started,
            "disposition": "PERSISTED_TRIALS_RETAINED_NO_AUTOMATIC_RETRY",
        }, indent=2), encoding="utf-8")
        raise
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        if kernel is not None and job is not None:
            kernel.CloseHandle(job)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "worker"))
    args = parser.parse_args()
    if args.command == "worker":
        expected = json.loads(sys.stdin.readline())
        if set(expected) != {"container_id", "server_address"}:
            raise ValueError("lite_pilot_requires_supervisor")
        result = worker(expected)
    else:
        result = run()
        result = {key: value for key, value in result.items() if key != "manifest"}
    print(json.dumps(result, default=str), flush=True)


if __name__ == "__main__":
    main()
