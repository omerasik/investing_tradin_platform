"""Opt-in isolated Windows acceptance orchestration over the existing harness.

No local execution, service, acquisition, new data authority or numeric engine.
The constants are witnesses from the retained P2B.1 reference, not substitutes
for the original verifier (which every reference/scope evaluation still calls).
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import research_loading_performance as benchmark

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".tmp/hosted-p2b1"
FIXTURE = OUTPUT / "fixture"
DATASET = "54cca16c-3a48-5873-bbd3-95e40bc2a2ae"
CONTENT = "01b455262792b31497ea62b04328432562c6ca44f3f72bb9d1ff69c3e3b80dd4"  # pragma: allowlist secret - recorded public SHA-256, not a credential
MANIFEST = "5c3516312ba8cd9634c8bf34ad25ba54fb2b1ad483016974e16f0aee89e92428"  # pragma: allowlist secret - recorded public SHA-256, not a credential
LOGICAL = "6b68b9c9cdfe95b715a893f0ac51017c074d1ec4230b1ee9c95df08d498122fd"  # pragma: allowlist secret - recorded public SHA-256, not a credential
OBJECT = "0834f116467294f612ce2ab2e93c62185e2869874ac745846b725ca6894260c7"  # pragma: allowlist secret - recorded public SHA-256, not a credential
SDK_BYTES = "8bd080a85710d25281bdc20d34e9cc58090979c319e95388fe362e55e3774e1e"  # pragma: allowlist secret - recorded public SHA-256, not a credential
INPUT = "89530b0783d90753b89a3978abf7a44ad688844a65498fd480b62d683f86b993"  # pragma: allowlist secret - recorded public SHA-256, not a credential
REFERENCE_CHAIN = "5d18ea4a6b235dc2003f729df8c4a4e357f7ad84edbbdf8459f3edebae970b1d"  # pragma: allowlist secret - recorded public SHA-256, not a credential


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def fixture_evidence(root: Path) -> dict[str, Any]:
    catalog = root / "v1/datasets/public-archive-bars" / f"{DATASET}.json"
    if not catalog.exists():
        candidates = list(catalog.parent.glob("*.json"))
        if len(candidates) != 1:
            raise ValueError("fixture_catalog_missing_or_ambiguous")
        catalog, = candidates  # Observe actual identity, never accept a substitute.
    c = json.loads(catalog.read_bytes())
    manifest_hash = c["bar_frame_manifest_hash"]
    if len(manifest_hash) != 64 or any(x not in "0123456789abcdef" for x in manifest_hash):
        raise ValueError("invalid_fixture_manifest_path")
    manifest = root / "v1/manifests" / f"{manifest_hash}.json"
    m = json.loads(manifest.read_bytes())
    object_hash = m["objects"][0]["sha256"]
    if len(object_hash) != 64 or any(x not in "0123456789abcdef" for x in object_hash):
        raise ValueError("invalid_fixture_object_path")
    obj = root / "v1/objects" / object_hash[:2] / f"{object_hash}.parquet"
    actual = {"dataset_id": c["dataset_version_id"], "content_hash": c["content_hash"],
              "manifest_hash": c["bar_frame_manifest_hash"], "logical_hash": m["logical_content_hash"],
              "catalog_logical_hash": c["identity"]["bar_frame"]["logical_content_hash"],
              "rows": m["row_count"], "objects": m["objects"], "object_sha256": sha(obj),
              "object_bytes": obj.stat().st_size, "writer_pyarrow": m["writer"]["pyarrow"],
              "catalog_bytes_sha256": sha(catalog), "manifest_bytes_sha256": sha(manifest)}
    expected = {"dataset_id": DATASET, "content_hash": CONTENT, "manifest_hash": MANIFEST,
                "logical_hash": LOGICAL, "catalog_logical_hash": LOGICAL, "rows": 86400,
                "objects": [{"sha256": OBJECT, "bytes": 2048325, "rows": 86400}],
                "object_sha256": OBJECT, "object_bytes": 2048325, "writer_pyarrow": "25.0.1",
                "catalog_bytes_sha256": "265ecdffcfb401de94d78f969e095667fbafb3d638e7cb84f77f898c05091670",  # pragma: allowlist secret - recorded public SHA-256, not a credential
                "manifest_bytes_sha256": "acebb3c6f3d6c7aef833444f407f7f24ab8b16af7e926ab8f68ce5c378e28775"}  # pragma: allowlist secret - recorded public SHA-256, not a credential
    return {"actual": actual, "expected": expected, "matches": actual == expected,
            "mismatches": [key for key in expected if actual[key] != expected[key]]}


def validate_report(report: dict[str, Any], tasks: int, implementation: str,
                    oracle: dict[str, Any] | None = None, *, distinct: bool = False) -> list[dict[str, Any]]:
    limits = report["limits"]
    if (report["implementation"] != implementation or report["evaluation_tasks"] != tasks
            or (distinct and report.get("distinct_configurations") is not True)
            or len(report["runs"]) != 3 or report.get("real_development_loader_only") is not False
            or limits["admission_policy"] != "github-hosted-windows-p2b1-v1"
            or any(limits[k] != v for k, v in {"process_memory_bytes": 512 * 1024 ** 2,
                "working_set_bytes": 512 * 1024 ** 2, "wall_seconds_per_run": 120,
                "workers": 1, "numeric_threads": 1, "host_reserve_bytes": 2 * 1024 ** 3,
                "disk_floor_bytes": 4 * 1024 ** 3, "runtime_commit_floor_bytes": 2 * 1024 ** 3}.items())):
        raise ValueError("acceptance_report_methodology_mismatch")
    results = []
    for run in report["runs"]:
        if run["status"] != "COMPLETE" or run["wall_seconds"] > 120:
            raise ValueError("acceptance_incomplete_or_late_run")
        r = run["result"]
        if (r["completed"] != tasks or r["failed"] != 0 or r["excluded"] != 0
                or r["unique_configurations_evaluated"] != (tasks if distinct else 40)
                or r["declared_unique_configurations"] != (684 if distinct else 40)
                or len(r["unique_results"]) != (tasks if distinct else 40)
                or r["registered_studies"] != 0 or r["dataset_content_hash"] != CONTENT
                or r["manifest_hash"] != MANIFEST or r["elapsed_seconds"] > 120
                or r["after"]["peak_commit_bytes"] > 512 * 1024 ** 2
                or r["after"]["peak_rss_bytes"] > 512 * 1024 ** 2
                or len(r["input_fingerprints"]) != 3
                or set(r["input_fingerprints"].values()) != {INPUT}
                or r["stages"]["verification"]["count"] != (tasks if implementation == "reference" else 3)):
            raise ValueError("acceptance_result_or_resource_mismatch")
        if tasks == 40 and r["result_chain"] != REFERENCE_CHAIN:
            raise ValueError("recorded_reference40_identity_changed")
        reference = oracle or (results[0] if results else r)
        compared = ({key: r["unique_results"].get(key) for key in reference["unique_results"]}
                    if distinct else r["unique_results"])
        if compared != reference["unique_results"] or r["input_fingerprints"] != reference["input_fingerprints"]:
            raise ValueError("reference_result_or_input_mismatch")
        if distinct:
            validate_distinct_catalog(r, tasks)
            if oracle is not None and (r["reference_comparisons"] != 12 or
                    r["configuration_catalog"] != oracle["configuration_catalog"]):
                raise ValueError("distinct_reference_coverage_mismatch")
            if results and any(r[k] != results[0][k] for k in ("unique_results", "configuration_catalog")):
                raise ValueError("non_deterministic_distinct_results")
        if results and r["result_chain"] != results[0]["result_chain"]:
            raise ValueError("non_deterministic_repetition")
        results.append(r)
    return results


def validate_distinct_catalog(result: dict[str, Any], tasks: int) -> None:
    """Check every admitted/rejected point and retained canonical identity."""
    expected = {"trend_ma_cross": (480, 380, {"fast_not_shorter_than_slow": 100}),
                "mean_reversion_z": (240, 240, {}),
                "breakout_channel": (84, 64, {"exit_channel_not_shorter": 20})}
    catalogs = result["configuration_catalog"]
    if (len(catalogs) != 3 or {c["family"] for c in catalogs} != set(expected)
            or result["planned_parameter_points"] != 804 or result["rejected_parameter_points"] != 120
            or result["configuration_catalog_hash"] != hashlib.sha256(
                json.dumps(catalogs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()):
        raise ValueError("distinct_catalog_count_or_hash_mismatch")
    admitted, parameters = {}, set()
    for catalog in catalogs:
        raw, valid, reasons = expected[catalog["family"]]
        points = catalog["points"]
        if (catalog["raw_points"] != raw or catalog["planned_trial_count"] != raw
                or catalog["admissible_points"] != valid or catalog["rejected_points"] != raw - valid
                or catalog["rejection_reasons"] != reasons or len(points) != raw
                or [p["space_index"] for p in points] != list(range(raw))
                or [p["ordinal"] for p in points] != list(range(raw))):
            raise ValueError("distinct_family_accounting_mismatch")
        actual_reasons: dict[str, int] = {}
        accepted = []
        for point in points:
            key = json.dumps([catalog["family"], point["parameters"]], sort_keys=True)
            if key in parameters:
                raise ValueError("duplicate_parameter_point")
            parameters.add(key)
            if point["rejected_reason"] is not None:
                reason = point["rejected_reason"]
                actual_reasons[reason] = actual_reasons.get(reason, 0) + 1
            else:
                accepted.append(point)
        if actual_reasons != reasons or len(accepted) != valid:
            raise ValueError("distinct_rejection_reason_mismatch")
        chosen = benchmark.representative_trials(accepted) if tasks == 12 else accepted
        for point in chosen:
            key = point["trial_hash"]
            if key in admitted:
                raise ValueError("duplicate_canonical_trial_hash")
            admitted[key] = {"family": catalog["family"], "study_id": catalog["study_id"],
                "study_hash": catalog["study_hash"], **{k: point[k] for k in (
                    "parameters", "ordinal", "space_index", "trial_id", "trial_hash")}}
    if set(admitted) != set(result["unique_results"]):
        raise ValueError("distinct_results_incomplete_or_unexpected")
    for key, identities in admitted.items():
        item = result["unique_results"][key]
        if any(item[k] != value for k, value in identities.items()) or item["outcome"] != "EVALUATED":
            raise ValueError("distinct_trial_identity_or_outcome_mismatch")


def run(output: Path, *arguments: str) -> dict[str, Any]:
    command = [sys.executable, str(ROOT / "scripts/research_loading_performance.py"),
               "--hosted-windows-acceptance", "--working-set-mib", "512",
               "--root", str(FIXTURE), "--output", str(output), *arguments]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    # Preserve the original negative receipt and fail, never continue/retry it.
    report = json.loads(output.read_bytes())
    if completed.returncode != 0:
        raise ValueError(f"bounded_workload_failed:{output.name}:{[r['status'] for r in report['runs']]}")
    return report


def storage() -> dict[str, Any]:
    from ctypes import wintypes as wt

    class FileInfo(ctypes.Structure):
        _fields_ = [("allocated", ctypes.c_longlong), ("size", ctypes.c_longlong),
                    ("links", wt.DWORD), ("delete", wt.BOOLEAN), ("directory", wt.BOOLEAN)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
    kernel.CreateFileW.restype = wt.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    files = {}
    for path in OUTPUT.rglob("*"):
        if not path.is_file():
            continue
        handle = kernel.CreateFileW(str(path), 0, 7, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = FileInfo()
            if not kernel.GetFileInformationByHandleEx(handle, 1, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            files[str(path.relative_to(OUTPUT))] = {"logical_bytes": info.size, "allocated_bytes": info.allocated}
        finally:
            kernel.CloseHandle(handle)
    roots = {"workspace": str(ROOT), "temp": str(Path(os.environ["TEMP"])),
             "system": os.environ["SystemDrive"] + "/"}
    return {"files": files, "total_logical_bytes": sum(x["logical_bytes"] for x in files.values()),
            "total_allocated_bytes": sum(x["allocated_bytes"] for x in files.values()),
            "volume_free_bytes": {key: shutil.disk_usage(value).free for key, value in roots.items()},
            "roots": roots}


def main() -> int:
    global OUTPUT, FIXTURE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distinct-configurations", action="store_true")
    options = parser.parse_args()
    if options.distinct_configurations:
        OUTPUT = ROOT / ".tmp/hosted-p2b21"
        FIXTURE = OUTPUT / "fixture"
    # The harness independently validates this context before each child.
    args = type("Admission", (), {"hosted_windows_acceptance": True, "working_set_mib": 512})()
    policy = benchmark.resource_policy(args)
    OUTPUT.mkdir(parents=True, exist_ok=False)
    summary: dict[str, Any] = {"status": "INCOMPLETE", "policy": policy, "environment": {
        "python": sys.version, "platform": platform.platform(), "machine": platform.machine(),
        "cpu_count": os.cpu_count(), "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "runner": {k: os.environ.get(k) for k in ("RUNNER_ENVIRONMENT", "RUNNER_OS", "RUNNER_ARCH",
            "ImageOS", "ImageVersion", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")},
        "packages": {k: importlib.metadata.version(k) for k in ("numpy", "pyarrow", "duckdb")}},
        "storage_before": storage(), "host_before": benchmark.sampled_host(policy)}
    try:
        summary["sdk_bytes_sha256"] = sha(ROOT / "src/trade_platform/strategy_sdk_v1.py")
        summary["expected_sdk_bytes_sha256"] = SDK_BYTES
        if summary["sdk_bytes_sha256"] != SDK_BYTES:
            raise ValueError("recorded_windows_sdk_bytes_changed")
        run(OUTPUT / "preparation.json", "--prepare-fixture", "--repeats", "1")
        evidence = fixture_evidence(FIXTURE)
        write(OUTPUT / "fixture-identity.json", evidence)
        if not evidence["matches"]:
            raise ValueError("fixture_identity_not_portable:" + ",".join(evidence["mismatches"]))
        distinct = options.distinct_configurations
        summary["workload"] = "684 distinct configurations" if distinct else "1000 repetitions of 40 configurations"
        common = ("--dataset", DATASET, "--expected", CONTENT, "--repeats", "3",
                  *(("--distinct-configurations",) if distinct else ()))
        reference_tasks = 12 if distinct else 40
        reference_path = OUTPUT / f"reference{reference_tasks}.json"
        reference = run(reference_path, "--implementation", "reference", "--evaluation-tasks", str(reference_tasks), *common)
        oracle, *_ = validate_report(reference, reference_tasks, "reference", distinct=distinct)
        for tasks in ((12, 684) if distinct else (40, 1000)):
            report = run(OUTPUT / f"optimized{tasks}.json", "--implementation", "optimized",
                "--evaluation-tasks", str(tasks), "--reference-receipt", str(reference_path), *common)
            validate_report(report, tasks, "optimized", oracle, distinct=distinct)
            if any(r["result"]["reference_receipt_sha256"] != sha(reference_path)
                   for r in report["runs"]):
                raise ValueError("reference_receipt_binding_changed")
        final = fixture_evidence(FIXTURE)
        if final != evidence:
            raise ValueError("fixture_changed_during_acceptance")
        summary["status"] = "PASS"
    except Exception as error:  # noqa: BLE001 - preserve fail-closed evidence before job fails
        summary.update(error_type=type(error).__name__, error=str(error))
    finally:
        summary["host_after"] = benchmark.sampled_host(policy)
        summary["storage_after"] = storage()
        summary["receipt_sha256"] = {p.name: sha(p) for p in OUTPUT.glob("*.json")}
        write(OUTPUT / "acceptance-summary.json", summary)
    print(json.dumps({"status": summary["status"], "error": summary.get("error"), "output": str(OUTPUT)}))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
