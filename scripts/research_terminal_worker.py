"""Research terminal command worker: carries out queued terminal commands through existing authorities.

    set TRADE_PLATFORM_RESEARCH_DSN=postgresql://...
    python scripts/research_terminal_worker.py [--data-root ...] [--archive-root ...] \\
        [--capture-root ~/.trade_platform/capture-universe-r1b] [--once]

One process, started by the operator like the recorders. It claims the oldest
REQUESTED command (row lock, so a second worker can never run the same one),
re-checks its gate against activation readiness, and runs it with the same
functions the operator CLIs use: ``strategy_lab.py`` (search/freeze/rerun), the
R6 registry (preregistration, one-shot opening, holdout validation), and the R8 /
R10 runners (``live_signals.py`` / ``paper_incubation.py``) as child processes it
tracks. Every one-shot command runs in its own child process under a per-kind
wall-clock bound (:data:`COMMAND_TIMEOUT_SECONDS`); a child that exceeds it is
killed with its process tree and the command records ``FAILED`` with
``COMMAND_TIMEOUT``. Every outcome is an append-only event. The DSN is read from
``TRADE_PLATFORM_RESEARCH_DSN`` only, never from argv. Capture has priority: no
heavy command is claimed while free disk is below the research guard (at least
24 GiB; the recorders stop at 20 GiB). No order, broker or account call exists here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from trade_platform.activation_readiness_v1 import (
    read_activation_readiness_v1,
    read_window_catalogue_v1,
)
from trade_platform.persistence import PostgresDatabase
from trade_platform.research_terminal_commands_v1 import (
    INLINE_KINDS,
    INPUT_MODELS,
    CommandView,
    PostgresTerminalCommandLedgerV1,
    TerminalCommandError,
    gate_reasons_v1,
    readiness_cycle_v1,
)

RESEARCH_DISK_GUARD_GIB = 24.0
#: Engineering bound on concurrent runner children (each is one symbol's live/paper loop).
MAX_RUNNERS = 6
RUNNER_KINDS = frozenset({"RESEARCH_WATCH_START", "PAPER_INCUBATION_START"})
WORKER_KINDS = frozenset(INPUT_MODELS) - INLINE_KINDS
HEAVY_KINDS = frozenset({"STRATEGY_SEARCH", "CANDIDATE_FREEZE", "DECIMAL_RERUN", "HOLDOUT_VALIDATE"}) | RUNNER_KINDS
#: Engineering wall-clock bound per one-shot kind. Search and rerun resume from their
#: leased ledgers, so a timed-out command is requested again rather than lost.
COMMAND_TIMEOUT_SECONDS: dict[str, int] = {
    "STRATEGY_SEARCH": 12 * 3600,
    "CANDIDATE_FREEZE": 3600,
    "DECIMAL_RERUN": 12 * 3600,
    "PREREGISTRATION_RECORD": 600,
    "HOLDOUT_OPEN": 600,
    "HOLDOUT_VALIDATE": 6 * 3600,
}
DSN_ENV = "TRADE_PLATFORM_RESEARCH_DSN"
ROOT = Path(__file__).resolve().parents[1]


def _kill_tree(child: subprocess.Popen[bytes]) -> None:
    """Kill a child and everything it started (the search spawns its own worker pool)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        import signal

        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child.wait(timeout=30)


class Blocked(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__(",".join(reasons))
        self.reasons = reasons


class Worker:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.database = PostgresDatabase(args.dsn)
        self.ledger = PostgresTerminalCommandLedgerV1(self.database)
        self.name = f"{socket.gethostname()}:research-terminal-worker:{os.getpid()}"
        self.children: dict[UUID, subprocess.Popen[bytes]] = {}
        self.child_kinds: dict[UUID, str] = {}

    # -- gates -------------------------------------------------------------
    def readiness(self, cycle_id: str | None = None) -> Any:
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            return read_activation_readiness_v1(cursor, windows=read_window_catalogue_v1(self.args.data_root),
                                                cycle_id=cycle_id)

    def check_gate(self, command: CommandView) -> None:
        try:
            readiness = self.readiness(readiness_cycle_v1(command.kind, command.inputs))
        except ValueError as error:
            raise Blocked([str(error)]) from error
        reasons = gate_reasons_v1(command.kind, command.inputs, readiness)
        if reasons:
            raise Blocked(reasons)

    # -- executors ---------------------------------------------------------
    def study(self, inputs: dict[str, Any]) -> Any:
        from strategy_lab import build_study

        return build_study(inputs["family"], UUID(inputs["dataset_version_id"]), self.args.data_root)

    def strategy_lab(self, command: CommandView, verb: str) -> dict[str, Any]:
        from strategy_lab import run_strategy_lab_command

        inputs = command.inputs
        study = self.study(inputs)
        if verb != "search":
            from trade_platform.strategy_lab_ledger_v1 import PostgresStrategyLabLedgerV1

            if not PostgresStrategyLabLedgerV1(self.database).progress(study.study_id).finished:
                raise Blocked([f"STUDY_SEARCH_NOT_FINISHED:{study.study_id}"])
        out = run_strategy_lab_command(verb, study=study, dsn=self.args.dsn, database=self.database,
                                       data_root=self.args.data_root, workers=inputs.get("workers", 1),
                                       metric=inputs.get("metric"), direction=inputs.get("direction"),
                                       top_k=inputs.get("top_k"))
        return json.loads(json.dumps(out, default=str))

    def packet(self, inputs: dict[str, Any]) -> Any:
        """The R6 preregistration a PREREGISTRATION_RECORD's inputs declare (deterministic rebuild)."""
        from trade_platform.strategy_lab_authority_rerun_v1 import PostgresAuthorityRerunStoreV1
        from trade_platform.strategy_lab_validation_v1 import (
            CURRENT_CYCLE_V1,
            CriterionV1,
            PostgresHoldoutRegistryV1,
            PreregistrationV1,
        )

        study = self.study(inputs)
        reruns = [r for r in PostgresAuthorityRerunStoreV1(self.database).for_study(study.study_id)
                  if r.rerun_hash == inputs["rerun_hash"]]
        if not reruns:
            raise Blocked(["PREREGISTRATION_RERUN_NOT_FOUND_FOR_THIS_STUDY"])
        cycle = (CURRENT_CYCLE_V1 if inputs["cycle_id"] == CURRENT_CYCLE_V1.cycle_id
                 else PostgresHoldoutRegistryV1(self.database).cycle(inputs["cycle_id"]))
        end = inputs.get("holdout_end_exclusive")
        return PreregistrationV1(
            study=study, rerun=reruns[0], symbol=inputs["symbol"], cycle=cycle,
            holdout_end_exclusive=None if end is None else datetime.combine(date.fromisoformat(end),
                                                                             datetime.min.time(), UTC),
            acceptance_criteria=tuple(CriterionV1(c["metric"], c["comparator"], c["threshold"])
                                      for c in inputs.get("acceptance_criteria", [])),
            minimum_trades=inputs.get("minimum_trades"), cost_policy=inputs.get("cost_policy"),
            incubation_days=inputs.get("incubation_days"), authorized_by=inputs.get("authorized_by"),
            authorized_on=inputs.get("authorized_on"))

    def recorded_packet(self, preregistration_hash: str) -> Any:
        """Rebuild a packet recorded through the terminal and prove it is byte-for-byte the stored one."""
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.inputs FROM research_terminal_commands c JOIN research_terminal_command_events e "
                "ON e.command_id = c.command_id WHERE c.kind='PREREGISTRATION_RECORD' AND e.state='SUCCEEDED' "
                "AND e.detail->>'preregistration_hash' = %s ORDER BY e.occurred_at DESC LIMIT 1",
                (preregistration_hash,))
            row = cursor.fetchone()
        if row is None:
            raise Blocked(["PREREGISTRATION_NOT_RECORDED_THROUGH_THE_TERMINAL"])
        packet = self.packet(row[0] if isinstance(row[0], dict) else json.loads(row[0]))
        if packet.content_hash != preregistration_hash:
            raise Blocked(["PREREGISTRATION_DOES_NOT_REBUILD_TO_ITS_RECORDED_HASH"])
        return packet

    def preregistration(self, command: CommandView) -> dict[str, Any]:
        from trade_platform.activation_readiness_v1 import _PREREGISTRATION_GATES
        from trade_platform.strategy_lab_validation_v1 import PostgresHoldoutRegistryV1

        packet = self.packet(command.inputs)
        newly = PostgresHoldoutRegistryV1(self.database).record_preregistration(packet)
        return {"preregistration_hash": packet.content_hash, "status": packet.status, "newly_recorded": newly,
                "unresolved": [f"{_PREREGISTRATION_GATES.get(r, 'ENGINEERING')}:{r}" for r in packet.unresolved]}

    def holdout_open(self, command: CommandView) -> dict[str, Any]:
        from trade_platform.strategy_lab_validation_v1 import PostgresHoldoutRegistryV1

        packet = self.recorded_packet(command.inputs["preregistration_hash"])
        if packet.cycle.cycle_id != command.inputs["confirm_cycle_id"]:
            raise Blocked(["HOLDOUT_CONFIRMATION_DOES_NOT_NAME_THE_PACKETS_CYCLE"])
        opening = PostgresHoldoutRegistryV1(self.database).open_holdout(packet, opened_by=command.inputs["opened_by"])
        return {"cycle_id": opening.cycle_id, "holdout_end_exclusive": opening.holdout_end_exclusive.isoformat(),
                "preregistration_hash": opening.preregistration_hash}

    def holdout_validate(self, command: CommandView) -> dict[str, Any]:
        from trade_platform.public_archive_research_bars_v1 import (
            acquire_and_derive_day_v1,
            build_research_bar_dataset_v1,
        )
        from trade_platform.research_data_plane_v1 import ResearchFrameStoreV1
        from trade_platform.strategy_lab_validation_v1 import (
            PostgresHoldoutRegistryV1,
            validate_on_holdout_v1,
        )

        packet = self.recorded_packet(command.inputs["preregistration_hash"])
        registry = PostgresHoldoutRegistryV1(self.database)
        opening = registry.opening(packet.cycle.cycle_id)
        if opening is None or opening.preregistration_hash != packet.content_hash:
            raise Blocked(["HOLDOUT_NOT_OPENED_FOR_THIS_PREREGISTRATION"])
        store = ResearchFrameStoreV1(self.args.data_root)
        first = opening.holdout_start.date()
        last = opening.holdout_end_exclusive.date() - timedelta(days=1)
        day = first
        while day <= last:
            acquire_and_derive_day_v1(self.args.archive_root, packet.symbol, day, store=store, evict=True,
                                      holdout_opening=opening)
            day += timedelta(days=1)
        holdout = build_research_bar_dataset_v1(self.args.archive_root, packet.symbol, first, last, store=store,
                                                holdout_opening=opening)
        run = validate_on_holdout_v1(packet, opening, holdout, store=store)
        registry.record_validation(run)
        return {"validation_hash": run.content_hash,
                "states": {item["trial_id"]: item["state"] for item in run.identity["candidates"]}}

    def start_runner(self, command: CommandView) -> dict[str, Any]:
        inputs = command.inputs
        same = {k: inputs.get(k) for k in ("family", "dataset_version_id", "symbol", "watchlist_id")}
        for view in self.ledger.running(RUNNER_KINDS):
            if view.kind == command.kind and {k: view.inputs.get(k) for k in same} == same:
                raise Blocked([f"RUNNER_ALREADY_RUNNING:{view.command_id}"])
        if len(self.children) >= MAX_RUNNERS:
            raise Blocked([f"RUNNER_LIMIT_REACHED:{MAX_RUNNERS}"])
        script = "live_signals.py" if command.kind == "RESEARCH_WATCH_START" else "paper_incubation.py"
        # The DSN travels in the child's environment, never in its (world-readable) argv.
        argv = [sys.executable, "-u", str(ROOT / "scripts" / script), "run",
                "--family", inputs["family"], "--dataset", inputs["dataset_version_id"], "--symbol", inputs["symbol"]]
        if inputs.get("watchlist_id"):
            argv += ["--watchlist", inputs["watchlist_id"]]
        if self.args.data_root:
            argv += ["--data-root", str(self.args.data_root)]
        if self.args.capture_root:
            argv += ["--capture-root", str(self.args.capture_root)]
        logs = Path.home() / ".trade_platform" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"terminal-{command.kind.lower()}-{command.command_id}.log"
        handle = log.open("ab")
        env = {**os.environ, "TRADE_PLATFORM_RESEARCH_DSN": self.args.dsn}
        child = subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT, cwd=ROOT, env=env)
        self.children[command.command_id] = child
        self.child_kinds[command.command_id] = command.kind
        return {"pid": child.pid, "log": str(log), "worker": self.name}

    def stop_runner(self, command: CommandView) -> dict[str, Any]:
        start_id = UUID(command.inputs["start_command_id"])
        expected = command.kind.replace("_STOP", "_START")
        child = self.children.get(start_id)
        if child is None:
            raise Blocked(["RUNNER_NOT_OWNED_BY_THIS_WORKER_OR_NOT_RUNNING"])
        if self.child_kinds.get(start_id) != expected:
            raise Blocked([f"STOP_KIND_DOES_NOT_MATCH_THE_RUNNER:{self.child_kinds.get(start_id)}"])
        child.terminate()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=30)
        self.children.pop(start_id)
        self.child_kinds.pop(start_id, None)
        self.ledger.record(start_id, "STOPPED", {"by_command": str(command.command_id), "returncode": child.returncode},
                           actor=self.name)
        return {"stopped": str(start_id), "returncode": child.returncode}

    # -- loop --------------------------------------------------------------
    def handlers(self) -> dict[str, Any]:
        return {
            "STRATEGY_SEARCH": lambda c: self.strategy_lab(c, "search"),
            "CANDIDATE_FREEZE": lambda c: self.strategy_lab(c, "freeze"),
            "DECIMAL_RERUN": lambda c: self.strategy_lab(c, "rerun"),
            "PREREGISTRATION_RECORD": self.preregistration,
            "HOLDOUT_OPEN": self.holdout_open,
            "HOLDOUT_VALIDATE": self.holdout_validate,
            "RESEARCH_WATCH_START": self.start_runner,
            "PAPER_INCUBATION_START": self.start_runner,
            "RESEARCH_WATCH_STOP": self.stop_runner,
            "PAPER_INCUBATION_STOP": self.stop_runner,
        }

    @staticmethod
    def outcome(command: CommandView, run: Any) -> tuple[str, dict[str, Any]]:
        """(state, detail) of one handler call; every refusal or failure is an outcome, never swallowed."""
        try:
            result = run(command)
        except Blocked as blocked:
            return "BLOCKED", {"reasons": blocked.reasons, "at": "claim"}
        except Exception as error:  # noqa: BLE001
            detail = re.sub(r"postgres(?:ql)?://\S+", "<dsn>", str(error))[:2000]
            return "FAILED", {"error": type(error).__name__, "detail": detail}
        return ("RUNNING" if command.kind in RUNNER_KINDS else "SUCCEEDED"), result

    def execute(self, command: CommandView) -> None:
        # Readiness is re-read at execution, not trusted from the request.
        state, detail = self.outcome(command, self.check_gate)
        if state in {"SUCCEEDED", "RUNNING"}:  # the gate passed
            if command.kind in COMMAND_TIMEOUT_SECONDS:
                state, detail = self.execute_bounded(command)
            else:
                state, detail = self.outcome(command, self.handlers()[command.kind])
        self.ledger.record(command.command_id, state, detail, actor=self.name)

    def execute_bounded(self, command: CommandView) -> tuple[str, dict[str, Any]]:
        """Run one one-shot command in a child process under its wall-clock bound."""
        timeout = COMMAND_TIMEOUT_SECONDS[command.kind]
        with tempfile.TemporaryDirectory(prefix="terminal-command-") as scratch:
            result_file = Path(scratch) / "result.json"
            argv = [sys.executable, "-u", str(Path(__file__).resolve()), "--execute-command", str(command.command_id),
                    "--result-file", str(result_file), "--archive-root", str(self.args.archive_root)]
            if self.args.data_root:
                argv += ["--data-root", str(self.args.data_root)]
            env = {**os.environ, DSN_ENV: self.args.dsn}
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            child = subprocess.Popen(argv, cwd=ROOT, env=env, creationflags=flags,
                                     start_new_session=os.name != "nt")
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_tree(child)
                return "FAILED", {"error": "COMMAND_TIMEOUT", "timeout_seconds": timeout,
                                  "detail": "killed with its process tree; request again to resume"}
            if not result_file.exists():
                return "FAILED", {"error": "COMMAND_CHILD_EXITED_WITHOUT_RESULT", "returncode": child.returncode}
            recorded = json.loads(result_file.read_text(encoding="utf-8"))
        return str(recorded["state"]), dict(recorded["detail"])

    def run_one(self, command_id: UUID, result_file: Path) -> None:
        """Child side of :meth:`execute_bounded`: run the handler, hand the outcome to the parent."""
        command = self.ledger.get(command_id)
        if command.state != "CLAIMED" or command.kind not in COMMAND_TIMEOUT_SECONDS:
            state, detail = "FAILED", {"error": "COMMAND_NOT_CLAIMED_FOR_EXECUTION", "state": command.state}
        else:
            state, detail = self.outcome(command, self.handlers()[command.kind])
        result_file.write_text(json.dumps({"state": state, "detail": detail}, default=str), encoding="utf-8")
        self.database.close()

    def reap(self) -> None:
        for start_id, child in list(self.children.items()):
            if child.poll() is not None:
                self.children.pop(start_id)
                self.child_kinds.pop(start_id, None)
                self.ledger.record(start_id, "EXITED", {"returncode": child.returncode}, actor=self.name)

    def single_instance(self) -> None:
        """One worker per database: a session advisory lock held for this process's life."""
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtext('research_terminal_worker_v1'))")
            row = cursor.fetchone()
        if not row or not row[0]:
            raise SystemExit("another research terminal worker holds the lock for this database")

    def kinds_now(self) -> frozenset[str]:
        if shutil.disk_usage(Path.home()).free < self.args.disk_guard_gib * (1 << 30):
            return WORKER_KINDS - HEAVY_KINDS  # capture first: heavy work waits for disk
        return WORKER_KINDS

    def run(self) -> None:
        self.single_instance()  # so every older claim below is from a worker that is gone
        prefix = f"{socket.gethostname()}:research-terminal-worker:"
        abandoned = self.ledger.abandon_stale_claims(worker_prefix=prefix, actor=self.name)
        for view in self.ledger.running(RUNNER_KINDS):
            if str(view.detail.get("worker", "")).startswith(prefix):
                self.ledger.record(view.command_id, "EXITED",
                                   {"reason": "WORKER_RESTARTED_RUNNER_NOT_TRACKED", "pid": view.detail.get("pid")},
                                   actor=self.name)
        print(json.dumps({"worker": self.name, "abandoned_claims": abandoned}), flush=True)
        try:
            while True:
                self.reap()
                command = self.ledger.claim(worker=self.name, kinds=self.kinds_now())
                if command is not None:
                    print(json.dumps({"claimed": str(command.command_id), "kind": command.kind}), flush=True)
                    self.execute(command)
                    continue
                if self.args.once:
                    return
                time.sleep(self.args.poll_seconds)
        finally:
            for start_id, child in self.children.items():
                child.terminate()
                try:
                    self.ledger.record(start_id, "STOPPED", {"reason": "WORKER_SHUTDOWN"}, actor=self.name)
                except TerminalCommandError:
                    pass
            self.database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--archive-root", type=Path, default=Path.home() / ".trade_platform" / "public-archive")
    parser.add_argument("--capture-root", type=Path, default=None)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="drain the queue once and exit (tests, manual runs)")
    parser.add_argument("--disk-guard-gib", type=float, default=RESEARCH_DISK_GUARD_GIB,
                        help="heavy commands wait while free disk is below this (capture keeps priority); >= 24")
    parser.add_argument("--execute-command", type=UUID, help=argparse.SUPPRESS)  # bounded child, internal
    parser.add_argument("--result-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.dsn = os.environ.get(DSN_ENV, "")
    if not args.dsn:
        raise SystemExit(f"{DSN_ENV} is not set (the DSN is never taken from argv)")
    if args.disk_guard_gib < RESEARCH_DISK_GUARD_GIB:
        raise SystemExit(f"--disk-guard-gib may not go below the {RESEARCH_DISK_GUARD_GIB:g} GiB research guard")
    if args.execute_command is not None:
        Worker(args).run_one(args.execute_command, args.result_file)
        return
    Worker(args).run()


if __name__ == "__main__":
    main()
