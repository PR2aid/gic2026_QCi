#!/usr/bin/env python3
"""Reproduce and verify every credential-free Phase 3 result.

This runner executes the IEEE-39 transmission study and the upgraded IEEE-33
feeder study from source.  It intentionally performs no network or hardware
operation.  A nonzero exit means at least one paper claim is not reproduced.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IEEE39 = ROOT / "ieee39_transmission"
IEEE33 = ROOT / "ieee33_feeder"
SUMMARY = ROOT / "results_summary.json"

# The frozen campaign, receipts and raw-response records are bound to shipped
# local payload artifacts. Each is a locally hash-bound source artifact used
# to construct the registered SDK upload object; this does not prove byte-level
# identity with the SDK's transmitted request. Regeneration is numerically
# deterministic but can differ in the last floating-point digits across
# CPU/BLAS implementations. The guarded block below therefore (1) verifies the
# pristine payload set against the release manifest, (2) snapshots those exact
# shipped bytes, (3) proves regenerated content is equivalent under narrowly
# scoped tolerances, and (4) atomically restores the shipped bytes in a finally
# block before any frozen scoring or evidence audit.
PAYLOAD_DIRS = ("ieee39_transmission/qci/ieee39_flagship", "ieee33_feeder/qci_payloads")
PAYLOAD_SNAPSHOT = ROOT / "results" / "_shipped_payload_snapshot"
PAYLOAD_EQUIV_REL_TOL = 1e-9
SOLVER_DIAGNOSTIC_REL_TOL = 1e-4
WINDOW_DIAGNOSTIC_PAYLOAD = (
    "ieee39_transmission/qci/ieee39_flagship/"
    "wdisp_c0_s171_w16_polynomial.json"
)
WINDOW_DIAGNOSTIC_FIELDS = {"energy", "x_device_units"}


def run(cmd: list[str], cwd: Path) -> None:
    print(f"\n$ ({cwd.name}) {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def payload_file_map(
    root: Path = ROOT,
    payload_dirs: tuple[str, ...] = PAYLOAD_DIRS,
) -> dict[str, Path]:
    """Return the exact regular JSON files in the registered payload folders."""
    files: dict[str, Path] = {}
    for rel in payload_dirs:
        directory = root / rel
        if directory.is_symlink() or not directory.is_dir():
            raise SystemExit(f"FAIL: payload directory is missing or unsafe: {rel}")
        for entry in directory.iterdir():
            item_rel = entry.relative_to(root).as_posix()
            if entry.is_symlink() or not entry.is_file() or entry.suffix != ".json":
                raise SystemExit(f"FAIL: unexpected payload entry: {item_rel}")
            files[item_rel] = entry
    return files


def manifest_payload_hashes(
    root: Path = ROOT,
    payload_dirs: tuple[str, ...] = PAYLOAD_DIRS,
) -> dict[str, str]:
    """Read the payload hashes expected by the package release manifest."""
    manifest_path = root / "RELEASE_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"FAIL: cannot read release manifest: {exc}") from exc
    prefixes = tuple(f"Source_Code/{rel}/" for rel in payload_dirs)
    expected: dict[str, str] = {}
    for entry in manifest.get("files", []):
        package_path = entry.get("path", "")
        if package_path.startswith(prefixes):
            expected[package_path.removeprefix("Source_Code/")] = entry["sha256"]
    if not expected:
        raise SystemExit("FAIL: release manifest contains no registered payload files")
    return expected


def verify_pristine_payloads(
    root: Path = ROOT,
    payload_dirs: tuple[str, ...] = PAYLOAD_DIRS,
) -> int:
    """Verify the original payload set and hashes before taking a baseline."""
    actual = payload_file_map(root, payload_dirs)
    expected = manifest_payload_hashes(root, payload_dirs)
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise SystemExit(
            f"FAIL: pristine payload file set differs from release manifest "
            f"(missing={missing}, unexpected={extra})"
        )
    for rel, path in actual.items():
        got = sha256(path)
        if got != expected[rel]:
            raise SystemExit(
                f"FAIL: pristine payload hash mismatch for {rel}: "
                f"expected {expected[rel]}, got {got}"
            )
    return len(actual)


def snapshot_shipped_payloads(
    root: Path = ROOT,
    payload_dirs: tuple[str, ...] = PAYLOAD_DIRS,
    snapshot_root: Path = PAYLOAD_SNAPSHOT,
) -> None:
    """Atomically snapshot pristine payloads without adopting stale state."""
    if snapshot_root.exists() or snapshot_root.is_symlink():
        raise SystemExit(
            f"FAIL: payload snapshot already exists at {snapshot_root}; "
            "use a fresh extraction"
        )
    snapshot_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f".{snapshot_root.name}.building-",
        dir=snapshot_root.parent,
    ))
    try:
        for rel in payload_dirs:
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(root / rel, target, symlinks=True)
        source_map = payload_file_map(root, payload_dirs)
        snapshot_map = payload_file_map(staging, payload_dirs)
        if set(source_map) != set(snapshot_map):
            raise SystemExit("FAIL: payload snapshot file set is incomplete")
        for rel in source_map:
            if sha256(source_map[rel]) != sha256(snapshot_map[rel]):
                raise SystemExit(f"FAIL: payload snapshot hash mismatch for {rel}")
        os.replace(staging, snapshot_root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def collect_deviations(
    shipped,
    regenerated,
    out: dict | None = None,
    *,
    path: tuple[object, ...] = (),
    allow_window_diagnostic: bool = False,
) -> bool:
    """Accumulate finite-float deviations while rejecting structural changes."""
    if out is None:
        out = {"strict": 0.0, "diagnostic": 0.0}
    if type(shipped) is bool or type(regenerated) is bool:
        return type(shipped) is type(regenerated) and shipped == regenerated
    if type(shipped) is int or type(regenerated) is int:
        return type(shipped) is type(regenerated) and shipped == regenerated
    if type(shipped) is float or type(regenerated) is float:
        if type(shipped) is not float or type(regenerated) is not float:
            return False
        if not (math.isfinite(shipped) and math.isfinite(regenerated)):
            return False
        deviation = abs(shipped - regenerated) / max(
            1.0, abs(shipped), abs(regenerated)
        )
        scoped_diagnostic = (
            allow_window_diagnostic
            and len(path) >= 2
            and path[0] == "classical_reference"
            and path[1] in WINDOW_DIAGNOSTIC_FIELDS
        )
        bucket = "diagnostic" if scoped_diagnostic else "strict"
        out[bucket] = max(out[bucket], deviation)
        return True
    if isinstance(shipped, dict) and isinstance(regenerated, dict):
        if set(shipped) != set(regenerated):
            return False
        return all(
            collect_deviations(
                shipped[key],
                regenerated[key],
                out,
                path=path + (key,),
                allow_window_diagnostic=allow_window_diagnostic,
            )
            for key in shipped
        )
    if isinstance(shipped, list) and isinstance(regenerated, list):
        if len(shipped) != len(regenerated):
            return False
        return all(
            collect_deviations(
                left,
                right,
                out,
                path=path + (index,),
                allow_window_diagnostic=allow_window_diagnostic,
            )
            for index, (left, right) in enumerate(zip(shipped, regenerated))
        )
    return type(shipped) is type(regenerated) and shipped == regenerated


def verify_regenerated_payloads(
    root: Path = ROOT,
    payload_dirs: tuple[str, ...] = PAYLOAD_DIRS,
    snapshot_root: Path = PAYLOAD_SNAPSHOT,
) -> dict:
    """Fail closed unless regenerated payloads match the pristine snapshot."""
    if snapshot_root.is_symlink() or not snapshot_root.is_dir():
        raise SystemExit("FAIL: shipped payload snapshot is missing or unsafe")
    shipped_map = payload_file_map(snapshot_root, payload_dirs)
    regenerated_map = payload_file_map(root, payload_dirs)
    if set(shipped_map) != set(regenerated_map):
        missing = sorted(set(shipped_map) - set(regenerated_map))
        extra = sorted(set(regenerated_map) - set(shipped_map))
        raise SystemExit(
            f"FAIL: regenerated payload file set changed "
            f"(missing={missing}, unexpected={extra})"
        )
    worst = {"strict": 0.0, "diagnostic": 0.0}
    worst_file = {"strict": "", "diagnostic": ""}
    for rel in sorted(shipped_map):
        try:
            shipped = json.loads(shipped_map[rel].read_text())
            regenerated = json.loads(regenerated_map[rel].read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"FAIL: invalid regenerated JSON in {rel}: {exc}") from exc
        shipped_reference = (
            shipped.get("classical_reference") if isinstance(shipped, dict) else None
        )
        regenerated_reference = (
            regenerated.get("classical_reference")
            if isinstance(regenerated, dict) else None
        )
        allow_window = (
            rel == WINDOW_DIAGNOSTIC_PAYLOAD
            and isinstance(shipped_reference, dict)
            and isinstance(regenerated_reference, dict)
            and shipped_reference.get("converged") is False
            and regenerated_reference.get("converged") is False
        )
        local = {"strict": 0.0, "diagnostic": 0.0}
        if not collect_deviations(
            shipped,
            regenerated,
            out=local,
            allow_window_diagnostic=allow_window,
        ):
            raise SystemExit(f"FAIL: structural or non-finite payload mismatch in {rel}")
        for bucket in worst:
            if local[bucket] > worst[bucket]:
                worst[bucket] = local[bucket]
                worst_file[bucket] = rel
    if worst["strict"] > PAYLOAD_EQUIV_REL_TOL:
        raise SystemExit(
            f"FAIL: machine-facing payload regeneration deviation "
            f"{worst['strict']:.3e} in {worst_file['strict']} exceeds "
            f"{PAYLOAD_EQUIV_REL_TOL:.0e}"
        )
    if worst["diagnostic"] > SOLVER_DIAGNOSTIC_REL_TOL:
        raise SystemExit(
            f"FAIL: identified window diagnostic regeneration deviation "
            f"{worst['diagnostic']:.3e} in {worst_file['diagnostic']} exceeds "
            f"{SOLVER_DIAGNOSTIC_REL_TOL:.0e}"
        )
    return {
        "compared": len(shipped_map),
        "worst": worst,
        "worst_file": worst_file,
    }


def _prepare_payload_directory(directory: Path) -> None:
    if directory.is_symlink():
        directory.unlink()
    elif directory.exists() and not directory.is_dir():
        directory.unlink()
    directory.mkdir(parents=True, exist_ok=True)


def restore_shipped_payloads(
    root: Path = ROOT,
    payload_dirs: tuple[str, ...] = PAYLOAD_DIRS,
    snapshot_root: Path = PAYLOAD_SNAPSHOT,
) -> None:
    """Atomically restore the complete pristine payload set and prove hashes."""
    snapshot_map = payload_file_map(snapshot_root, payload_dirs)
    expected = set(snapshot_map)
    for rel in payload_dirs:
        directory = root / rel
        _prepare_payload_directory(directory)
        for entry in list(directory.iterdir()):
            item_rel = entry.relative_to(root).as_posix()
            if item_rel not in expected or entry.is_dir():
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    raise SystemExit(f"FAIL: cannot remove unsafe payload entry: {item_rel}")
    for rel, source in snapshot_map.items():
        destination = root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                dir=destination.parent,
                prefix=f".{destination.name}.restore-",
                delete=False,
            ) as handle:
                temp_name = handle.name
                with source.open("rb") as input_handle:
                    shutil.copyfileobj(input_handle, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, source.stat().st_mode & 0o777)
            os.replace(temp_name, destination)
            temp_name = None
        finally:
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)
    restored = payload_file_map(root, payload_dirs)
    if set(restored) != expected:
        raise SystemExit("FAIL: restored payload file set does not match snapshot")
    for rel in expected:
        if sha256(restored[rel]) != sha256(snapshot_map[rel]):
            raise SystemExit(f"FAIL: restored payload hash mismatch for {rel}")
    shutil.rmtree(snapshot_root)


@contextmanager
def preserved_payload_bytes():
    """Restore pristine payload bytes after success or any handled failure."""
    pristine_count = verify_pristine_payloads()
    print(f"pristine payload manifest binding: {pristine_count}/{pristine_count} PASS")
    snapshot_shipped_payloads()
    try:
        yield
    finally:
        restore_shipped_payloads()


def report_payload_equivalence(result: dict) -> None:
    worst = result["worst"]
    print(
        f"payload regeneration equivalence: {result['compared']} files match shipped payloads "
        f"(machine-facing max relative deviation {worst['strict']:.3e} <= "
        f"{PAYLOAD_EQUIV_REL_TOL:.0e}; identified non-converged window SLSQP diagnostic "
        f"{worst['diagnostic']:.3e} <= {SOLVER_DIAGNOSTIC_REL_TOL:.0e}); "
        "shipped payload bytes restored for the hash-bound scoring and audit gates"
    )


def close(got: float, expected: float, *, rel: float = 1e-6) -> bool:
    return abs(float(got) - float(expected)) <= rel * max(
        1.0, abs(float(got)), abs(float(expected))
    )


def full_resolution_audit(terms: list[dict], ratio: float = 200.0) -> dict:
    """Apply both QCi coefficient-resolution checks, including separation."""
    values = sorted({float(t["val"]) for t in terms if float(t["val"]) != 0.0})
    magnitudes = [abs(v) for v in values]
    if not values:
        return {"pass": False, "reason": "no nonzero coefficients"}
    max_abs, min_abs = max(magnitudes), min(magnitudes)
    min_sep = min((b - a for a, b in zip(values, values[1:])), default=None)
    required = max_abs / ratio
    return {
        "pass": bool(max_abs / min_abs <= ratio + 1e-12 and
                     (min_sep is None or min_sep + 1e-12 >= required)),
        "coefficient_spread": max_abs / min_abs,
        "min_distinct_separation": min_sep,
        "required_min_separation": required,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--landscape-check", action="store_true",
        help="also run the slower 121-variable local characterization landscape",
    )
    args = ap.parse_args()
    py = sys.executable
    t0 = time.time()

    # Any handled failure during regeneration or comparison still restores the
    # exact shipped local artifacts before this process exits.
    with preserved_payload_bytes():
        # IEEE-39: public transmission instance and device payload ladder.
        run([py, "scripts/test_conventions.py"], IEEE39)
        cmd = [py, "scripts/run_end_to_end.py"]
        if args.landscape_check:
            cmd += ["--landscape-check", "--relax-restarts", "2"]
        run(cmd, IEEE39)
        run([py, "scripts/summarize_resources.py"], IEEE39)
        run([
            py, "scripts/build_dirac3_payload.py", "--relaxation-schedule", "1",
            "--num-samples", "25",
        ], IEEE39)

        # IEEE-33: exact three-stage planning, baselines, AC, and chronology.
        for script in (
            "run_pipeline.py", "run_stage0.py", "run_baselines.py",
            "run_ac_audit.py", "run_chronology_audit.py",
        ):
            run([py, f"scripts/{script}"], IEEE33)
        comparison = verify_regenerated_payloads()
    report_payload_equivalence(comparison)

    # Credential-free rescoring and independent audits of every immutable raw
    # QCi response.  These commands make no network or hardware call.
    run([py, "run_live_dirac3.py", "--score"], ROOT)
    run([py, "scripts/audit_live_evidence.py"], ROOT)
    run([py, "scripts/audit_live_physics.py"], ROOT)
    # Convex-oracle and receipt-timing certificate for the live window.
    run([py, "scripts/certify_live_results.py"], ROOT)
    run([py, "-m", "unittest", "discover", "-s", "tests", "-v"], IEEE33)
    run([py, "-m", "unittest", "discover", "-s", "tests", "-v"], ROOT)

    checks: list[tuple[str, bool, object, object]] = []

    def numeric(name: str, got: float, expected: float, rel: float = 1e-6) -> None:
        checks.append((name, close(got, expected, rel=rel), got, expected))

    def boolean(name: str, value: bool, expected: object = True) -> None:
        checks.append((name, bool(value), value, expected))

    r39 = json.loads((IEEE39 / "results/end_to_end_case39.json").read_text())
    yes, no = r39["metrics_with_islanding"], r39["metrics_no_islanding"]
    numeric("IEEE39 cost without islanding ($/24h)",
            no["expected_operating_cost_$"], 6_810_970.04)
    numeric("IEEE39 cost with fixed portfolio ($/24h)",
            yes["expected_operating_cost_$"], 5_537_173.34)
    numeric("IEEE39 total annual cost without islanding",
            no["expected_total_annual_cost_$"], 2_486_004_064.60)
    numeric("IEEE39 total annual cost with fixed portfolio",
            yes["expected_total_annual_cost_$"], 2_242_302_119.10)
    numeric("IEEE39 total annual saving fraction",
            yes["expected_total_annual_saving_fraction"],
            0.0980295844927397)
    numeric("IEEE39 max unserved fraction without islanding",
            no["max_system_fraction_unserved_per_hour"], 0.1932)
    numeric("IEEE39 max unserved fraction with fixed portfolio",
            yes["max_system_fraction_unserved_per_hour"], 0.0672)
    numeric("IEEE39 critical outage hours without islanding",
            no["expected_critical_outage_hours"], 0.08)
    numeric("IEEE39 critical outage hours with islanding", yes["expected_critical_outage_hours"], 0.0)
    numeric("IEEE39 simulated-annealing master gap",
            r39["selection_sa_gap"], 0.04656006312735799)
    numeric("IEEE39 HiGHS master gap", r39["selection_milp_gap"], 0.0)
    boolean("IEEE39 fixed portfolio is [0,3]",
            r39["master_fixed_portfolio"] == [0, 3])
    boolean("IEEE39 connected-partition invariants pass",
            r39["partition_invariants_pass"])
    numeric("IEEE39 retained scenario probability",
            r39["retained_probability_sum"], 1.0)
    boolean("IEEE39 every load bus is assigned exactly once",
            set(r39["load_assignment_counts"].values()) == {1})
    numeric("IEEE39 maximum candidate overlap", r39["max_pairwise_overlap_buses"], 0.0)
    numeric("IEEE39 partition load sum equals system load",
            r39["candidate_load_sum_mw"], r39["system_load_mw"])
    boolean("IEEE39 portfolio is frozen across every scenario",
            r39["master_frozen_across_scenarios"]
            and all(row["selected"] == [0, 3] for row in r39["per_scenario"]))
    numeric("IEEE39 selected load-bus coverage",
            yes["portfolio_load_bus_coverage_ratio"], 4.0 / 21.0)
    numeric("IEEE39 selected MW coverage",
            yes["portfolio_load_mw_coverage_ratio"],
            0.27311115836801647)
    boolean("IEEE39 PCC sampling reaches the complete >9 identifier range",
            all(0 <= cid < r39["n_candidates"]
                for cid in r39["pcc_identifiers_observed"])
            and max(r39["pcc_identifiers_observed"]) > 9)

    manifest = json.loads((IEEE39 / "qci/ieee39_flagship/payload_manifest.json").read_text())
    registered_39 = [p for p in manifest["payloads"] if p["family"] in {"hourly_dispatch", "window_dispatch"}]
    audits_39 = []
    for entry in registered_39:
        payload = json.loads((IEEE39 / "qci/ieee39_flagship" / entry["file"]).read_text())
        audits_39.append(full_resolution_audit(payload["polynomial"]))
    boolean("IEEE39 five registered dispatch payloads pass full coefficient resolution",
            len(audits_39) == 5 and all(a["pass"] for a in audits_39))
    probe_entry = next(p for p in manifest["payloads"] if p["family"] == "probe_24h_cubic")
    probe = json.loads((IEEE39 / "qci/ieee39_flagship" / probe_entry["file"]).read_text())
    probe_audit = full_resolution_audit(probe["polynomial"])
    boolean("IEEE39 cubic probe is correctly excluded from in-spec evidence",
            probe["num_variables"] == 121 and not probe_audit["pass"])

    with (IEEE33 / "results/design_pareto.csv").open(newline="") as handle:
        pareto = {row["design_mode"]: row for row in csv.DictReader(handle)}
    expected = {
        "cost_efficient": (13_420_000.0, 21.0, 16.0, 0.34375),
        "balanced_critical": (16_440_000.0, 4.2, 8.0, 0.25),
        "robust_critical": (19_460_000.0, 0.0, 0.0, 0.0),
    }
    for mode, target in expected.items():
        row = pareto[mode]
        numeric(f"IEEE33 {mode} upfront cost", float(row["upfront_cost_usd"]), target[0])
        numeric(f"IEEE33 {mode} expected annual critical ENS", float(row["expected_annual_critical_unserved_mwh"]), target[1])
        numeric(f"IEEE33 {mode} static critical pocket-hours", float(row["critical_unserved_pocket_hours"]), target[2])
        numeric(f"IEEE33 {mode} worst static customer-block unserved fraction", float(row["worst_customer_block_fraction_unserved"]), target[3])

    payloads33 = sorted((IEEE33 / "qci_payloads").glob("*.json"))
    boolean("IEEE33 generated exactly 27 registered payloads", len(payloads33) == 27)
    boolean("IEEE33 all 27 payloads pass full coefficient-resolution audit", all(
        json.loads(path.read_text())["coefficient_resolution_audit_local_only"]["pass"]
        for path in payloads33
    ))
    ac = json.loads((IEEE33 / "results/ac_powerflow/ac_powerflow_summary.json").read_text())
    boolean("IEEE33 nonlinear radial AC screen passes", ac["grid_connected_base_case"]["voltage_within_0p90_1p10"] and all(
        row["all_served_island_ac_checks_pass"] for row in ac["design_scenario_results"]
    ))
    chronology = json.loads((IEEE33 / "results/chronology_audit/chronology_audit_summary.json").read_text())
    boolean("IEEE33 96-case chronology audit has no physical constraint violation",
            chronology["audit_pass"] and chronology["case_count"] == 96 and
            chronology["physical_constraint_violation_count"] == 0)

    strict_live = json.loads((ROOT / "results/live/strict_evidence_audit.json").read_text())
    physical_live = json.loads((ROOT / "results/live/physical_decode_audit.json").read_text())
    certified_live = json.loads((ROOT / "results/live/certified_hardware_analysis.json").read_text())

    all_ok = all(ok for _, ok, _, _ in checks)
    print("\n" + "=" * 78)
    print(f"CONSOLIDATED CLAIM AUDIT: {sum(ok for _, ok, _, _ in checks)}/{len(checks)} PASS")
    print("=" * 78)
    for name, ok, got, expected_value in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {got!r} (expected {expected_value!r})")

    summary = {
        "all_checks_passed": all_ok,
        "checks_passed": sum(ok for _, ok, _, _ in checks),
        "checks_total": len(checks),
        "wall_seconds": round(time.time() - t0, 1),
        "python": sys.version,
        "live_evidence": {
            "protocol_sha256": strict_live["protocol_sha256"],
            "strict_audit_pass": strict_live["strict_audit_pass"],
            "campaign_raw_machine_feasible_samples": (
                strict_live["campaign_raw_feasible_counted_samples"]
            ),
            "campaign_counted_samples": strict_live["campaign_counted_samples"],
            "ieee39_hourly_raw_cap_feasible_samples": (
                physical_live["hourly_raw_cap_feasible_counted_samples"]
            ),
            "ieee39_hourly_counted_samples": physical_live["hourly_counted_samples"],
            "physical_decode_analysis_complete": physical_live["analysis_complete"],
            "campaign_elapsed_wall_s_from_receipts": (
                certified_live["execution_timing"]["campaign_elapsed_wall_s"]
            ),
            "campaign_device_usage_s": (
                certified_live["execution_timing"]["all_campaign_device_usage_s_sum"]
            ),
        },
        "ieee39": {"with_islanding": yes, "without_islanding": no,
                    "registered_dispatch_resolution_audits": audits_39,
                    "probe_resolution_audit": probe_audit},
        "ieee33": {"pareto": pareto, "payload_count": len(payloads33),
                    "ac_screen_pass": True, "chronology_case_count": chronology["case_count"]},
    }
    SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"wrote {SUMMARY} in {summary['wall_seconds']} s")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
