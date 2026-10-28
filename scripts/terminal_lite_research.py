"""Explicit, offline BTC/ETH/SOL Lite studies; never imported by the browser API.

Run `python scripts/terminal_lite_research.py run` on the admitted local Windows
host. The canonical Strategy Lab command drains one unchanged 84-point study
in the supervised process; subsequent invocation is an audited no-op. No DSN,
family, retry, promotion or execution override is exposed. Dataset selection
is restricted to the three verified August 1..14 windows.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
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
BTC_MANIFEST = "67e4c918caa21cebab952f94d8829de0b164e856616b6bd6763c9b18f201b795"
DATASETS = {
    DATASET_ID: ("BTCUSDT", DATASET_HASH),
    UUID("d3436b40-3d8b-58e1-987f-82dc0d08c95f"): (
        "ETHUSDT", "9446e31832cee97f7caac798b0aeb3713468b375d72981c8303bfeeb00e09f81"),
    UUID("04b8964d-5053-5783-b390-93d916e0a06e"): (
        "SOLUSDT", "a03e9e5a8888a5f3144041ec067bb942014ee72fc99b0b5241e5e998c8f8e2b7"),
}
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


def checked_studies(root: Path) -> dict[UUID, Any]:
    from scripts.strategy_lab import build_study
    from trade_platform.terminal_lite_evidence_v1 import report_days, verify_lite_subset

    report = verify_lite_subset(root)
    days = report_days(report)
    actual = [(item["dataset_version_id"], item["symbol"], item["content_hash"], item["rows"])
              for item in report["datasets"]]
    canonical = [(str(key), symbol, digest, 20160) for key, (symbol, digest) in DATASETS.items()]
    if (str(days[0]), str(days[-1])) != ("2026-08-01", "2026-08-14") or sorted(actual) != sorted(canonical):
        raise ValueError("lite_pilot_requires_exact_verified_three_market_window")
    studies = {key: build_study("breakout_channel", key, root) for key in DATASETS}
    for key, study in studies.items():
        if (study.planned_trial_count != 84 or len(study.datasets) != 1
                or study.datasets[0].dataset_version_id != key
                or study.datasets[0].content_hash != DATASETS[key][1]
                or (key == DATASET_ID and study.study_id != STUDY_ID)):
            raise ValueError("lite_pilot_canonical_study_changed")
    return studies


def checked_study(root: Path, dataset_id: UUID = DATASET_ID):
    if dataset_id not in DATASETS:
        raise ValueError("lite_pilot_dataset_not_allowlisted")
    return checked_studies(root)[dataset_id]


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


def database_proof(database: Any, expected: dict[str, str], allowed: tuple[Any, ...]) -> dict[str, Any]:
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
        by_id = {s.study_id: s for s in allowed}
        for stored in studies:
            study = by_id.get(stored[0])
            if study is None or stored != (study.study_id, study.content_hash, study.identity()):
                raise ValueError("lite_pilot_refuses_unexpected_or_conflicting_study")
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
    if not pin.exists() or json.loads(pin.read_text()) != identity:
        raise ValueError("lite_pilot_cluster_pin_mismatch")
    # Audit *all* admitted studies before the worker may register/claim anything.
    for stored in studies:
        verify_results(database, by_id[stored[0]])
    return proof


def verify_results(database: Any, study: Any, *, complete: bool = False):
    from trade_platform.strategy_lab_ledger_v1 import (
        PostgresStrategyLabLedgerV1,
        result_content_hash_v1,
    )
    from trade_platform.strategy_lab_manifest_v1 import build_study_manifest_v1

    ledger = PostgresStrategyLabLedgerV1(database)
    if ledger.stored_study(study.study_id) != (study.content_hash, study.planned_trial_count):
        raise ValueError("lite_pilot_persisted_study_plan_mismatch")
    trials = {t.trial_id: t for t in study.trials()}
    results = ledger.results(study.study_id)
    for result in results:
        trial = trials.get(result.trial_id)
        if (trial is None or result.numeric_tier != "SEARCH_NON_AUTHORITATIVE"
                or result.result_content_hash != result_content_hash_v1(
                    trial_content_hash=trial.content_hash, outcome=result.outcome,
                    metrics=result.metrics, cost_policy_slot=study.cost_policy_slot)):
            raise ValueError("lite_pilot_persisted_result_hash_mismatch")
    progress = ledger.progress(study.study_id)
    if (sum(progress.states.values()) != study.planned_trial_count or progress.results != len(results)
            or progress.states.get("SUCCEEDED", 0) != len(results)):
        raise ValueError("lite_pilot_trial_accounting_mismatch")
    manifest = build_study_manifest_v1(ledger, study) if progress.finished else None
    with database.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        stored = conn.execute(
            "SELECT manifest_hash,identity FROM strategy_lab_study_manifests WHERE study_id=%s",
            (study.study_id,),
        ).fetchall()
    if stored and (manifest is None or stored != [(manifest.manifest_hash, dict(manifest.identity))]):
        raise ValueError("lite_pilot_persisted_manifest_mismatch")
    if (complete or study.study_id == STUDY_ID) and (
            progress.states != {"SUCCEEDED": study.planned_trial_count}
            or len(results) != study.planned_trial_count or manifest is None
            or stored != [(manifest.manifest_hash, dict(manifest.identity))]):
        raise ValueError("lite_pilot_requires_complete_study_and_manifest")
    if study.study_id == STUDY_ID and manifest.manifest_hash != BTC_MANIFEST:
        raise ValueError("lite_pilot_original_btc_manifest_changed")
    return results, manifest


def study_digest(database: Any, study_id: UUID) -> str:
    """Includes every persisted column, timestamp and event; detects changes on replay."""
    from psycopg import sql

    digest = hashlib.sha256()
    with database.transaction() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        for name in TABLES:
            rows = conn.execute(sql.SQL(
                "SELECT to_jsonb(t)::text FROM {} t WHERE study_id=%s ORDER BY to_jsonb(t)::text"
            ).format(sql.Identifier(name)), (study_id,)).fetchall()
            digest.update(json.dumps([name, rows], separators=(",", ":")).encode())
    return digest.hexdigest()


def snapshot(database: Any) -> dict[str, int]:
    from psycopg import sql

    with database.transaction() as conn:
        return {name: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))).fetchone()[0]
                for name in TABLES}


def execute_pilot(database: Any, dsn: str, root: Path, study: Any, expected: dict[str, str]) -> dict[str, Any]:
    from scripts.strategy_lab import run_strategy_lab_command

    allowed = tuple(checked_studies(root).values())
    if not any((study.study_id, study.content_hash, study.identity()) ==
               (s.study_id, s.content_hash, s.identity()) for s in allowed):
        raise ValueError("lite_pilot_selected_study_not_allowlisted")
    proof = database_proof(database, expected, allowed)
    # This phase extends the completed BTC pilot; it must never reconstruct it
    # into a replacement or emptied database, even one with the expected schema.
    verify_results(database, allowed[0], complete=True)
    # An accepted supervisor receipt and a fresh full integrity audit gate each
    # successor. This does not launch a campaign or automatically retry a study.
    index = next(i for i, s in enumerate(allowed) if s.study_id == study.study_id)
    for predecessor in allowed[:index]:
        verify_results(database, predecessor, complete=True)
        accepted_predecessor(predecessor)
    preserved = {str(s.study_id): study_digest(database, s.study_id) for s in allowed}
    before = snapshot(database)
    output = run_strategy_lab_command("search", study=study, dsn=dsn, database=database,
                                      data_root=root, workers=1, in_process=True)
    trials = {trial.trial_id: trial for trial in study.trials()}
    results, manifest = verify_results(database, study, complete=True)
    after = snapshot(database)
    if (after["strategy_lab_trials"] != after["strategy_lab_studies"] * 84
            or sum(output["states"].values()) != 84 or not output["finished"]):
        raise ValueError("lite_pilot_trial_accounting_mismatch")
    digests = {str(s.study_id): study_digest(database, s.study_id) for s in allowed}
    if any(preserved[str(s.study_id)] != digests[str(s.study_id)] for s in allowed if s.study_id != study.study_id):
        raise ValueError("lite_pilot_other_study_changed")
    if before == after and preserved != digests:
        raise ValueError("lite_pilot_replay_changed_persisted_rows")
    first = next((r for r in results if r.outcome.value == "EVALUATED"), None)
    final_proof = database_proof(database, expected, allowed)
    return {**output, "study_content_hash": study.content_hash, "database_proof": proof,
            "dataset_id": str(study.datasets[0].dataset_version_id),
            "outcomes": dict(manifest.identity["outcomes"]), "study_digests": digests,
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


def accepted_predecessor(study: Any) -> None:
    for path in (STATE / "research-pilot").glob("*.json"):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if (receipt.get("study_id") == str(study.study_id)
                and receipt.get("study_content_hash") == study.content_hash
                and receipt.get("finished") is True and receipt.get("states") == {"SUCCEEDED": 84}
                and receipt.get("supervisor_wall_seconds", DEADLINE + 1) <= DEADLINE
                and receipt.get("limits", {}).get("memory_bytes") == MEMORY_BYTES
                and receipt.get("limits", {}).get("workers") == 1
                and receipt.get("limits", {}).get("cores") == 1
                and receipt.get("database_bytes_after", DATABASE_CEILING + 1) <= DATABASE_CEILING
                and admitted(receipt.get("host_minimum", {}), initial=False)
                and all(receipt.get("resources", {}).get(k, MEMORY_BYTES + 1) <= MEMORY_BYTES
                        for k in ("peak_rss_bytes", "peak_commit_bytes"))):
            return
    raise ValueError("lite_pilot_predecessor_resource_acceptance_required")


def worker(expected: dict[str, str], dataset_id: UUID) -> dict[str, Any]:
    import pyarrow as pa

    from scripts.research_performance import _resources
    from trade_platform.persistence import PostgresDatabase

    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    started, cpu = time.perf_counter(), time.process_time()
    root = active_root()
    study = checked_study(root, dataset_id)
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
    return (host.get("available_physical_bytes", 0) >= reserve and host.get("commit_headroom_bytes", 0) >= 1024**3
            and host.get("disk_free_bytes", 0) >= DISK_FLOOR)


def run(dataset_id: UUID = DATASET_ID) -> dict[str, Any]:
    if dataset_id not in DATASETS:
        raise ValueError("lite_pilot_dataset_not_allowlisted")
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
    child = subprocess.Popen([sys._base_executable, str(Path(__file__).resolve()), "worker",
                              "--dataset-id", str(dataset_id)],
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
    parser.add_argument("--dataset-id", type=UUID, choices=tuple(DATASETS), default=DATASET_ID)
    args = parser.parse_args()
    if args.command == "worker":
        expected = json.loads(sys.stdin.readline())
        if set(expected) != {"container_id", "server_address"}:
            raise ValueError("lite_pilot_requires_supervisor")
        result = worker(expected, args.dataset_id)
    else:
        result = run(args.dataset_id)
        result = {key: value for key, value in result.items() if key != "manifest"}
    print(json.dumps(result, default=str), flush=True)


if __name__ == "__main__":
    main()
