#!/usr/bin/env python3
"""Create the final no-cover markdown only from fully audited live results."""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/Phase3_Writeup_HARDWARE_PENDING_NO_COVER.md"
TARGET = ROOT / "docs/Phase3_Writeup_FINAL_NO_COVER.md"
SUMMARY = ROOT / "results/live/hardware_summary.json"
CERTIFIED = ROOT / "results/live/certified_hardware_analysis.json"
STRICT = ROOT / "results/live/strict_evidence_audit.json"
PHYSICAL = ROOT / "results/live/physical_decode_audit.json"


def measured_usage(records: list[dict]) -> tuple[float, int, float]:
    """Return total, measured-job count, and mean without inventing usage.

    A characterization payload can be rejected before the API creates a job;
    such an audited terminal outcome legitimately has no device_usage_s.
    """
    values = [float(row["device_usage_s"]) for row in records
              if row.get("device_usage_s") is not None]
    total = sum(values)
    return total, len(values), total / len(values) if values else 0.0


def finite_number(value: object, label: str) -> float:
    """Return a finite float or stop final-manuscript generation."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"refusing final manuscript: {label} is not numeric") from exc
    if not math.isfinite(number):
        raise SystemExit(f"refusing final manuscript: {label} is not finite")
    return number


def require_close(value: object, expected: float, label: str, tolerance: float = 1e-9) -> float:
    """Require a deterministic audited value within a stated absolute tolerance."""
    number = finite_number(value, label)
    if abs(number - expected) > tolerance:
        raise SystemExit(
            f"refusing final manuscript: {label}={number!r}, expected {expected!r}"
        )
    return number


def main() -> int:
    data = json.loads(SUMMARY.read_text())
    certified = json.loads(CERTIFIED.read_text())
    strict = json.loads(STRICT.read_text())
    physical = json.loads(PHYSICAL.read_text())
    records = data["records"]
    protocol = data.get("protocol_sha256")
    if data.get("planned_jobs") != 10 or data.get("valid_audited_jobs") != 10:
        raise SystemExit("refusing final manuscript: live audit is not 10/10 valid")
    evidence = [row for row in records if row["evidence_class"] == "REGISTERED_EVIDENCE"]
    if len(evidence) != 9 or any(row.get("status") != "COMPLETED" for row in evidence):
        raise SystemExit("refusing final manuscript: at least one in-spec evidence job is not COMPLETED")
    probe = next(row for row in records if row["evidence_class"].startswith("CHARACTERIZATION"))
    if probe.get("status") not in {"COMPLETED", "ERRORED", "CANCELLED", "SUBMISSION_REJECTED"}:
        raise SystemExit("refusing final manuscript: characterization probe has no concrete terminal outcome")
    window = next(row for row in records if row["id"] == "ieee39_window_h16_h19")
    if (not certified.get("certification_pass")
            or certified.get("protocol_sha256") != data.get("protocol_sha256")
            or certified.get("job_id") != window.get("job_id")):
        raise SystemExit("refusing final manuscript: certified window analysis is missing or mismatched")
    timing = certified.get("execution_timing")
    if not isinstance(timing, dict):
        raise SystemExit("refusing final manuscript: immutable receipt timing is missing")
    evidence_wall_s = require_close(
        timing.get("registered_evidence_submit_to_collect_wall_s_sum"),
        138.330434,
        "registered-evidence submit-to-collect wall seconds",
        tolerance=1e-6,
    )
    campaign_wall_s = require_close(
        timing.get("campaign_elapsed_wall_s"),
        427.613396,
        "campaign first-submit-to-last-collect wall seconds",
        tolerance=1e-6,
    )
    campaign_device_s = require_close(
        timing.get("all_campaign_device_usage_s_sum"),
        320.0,
        "campaign device seconds",
        tolerance=1e-9,
    )
    if any(row.get("feasible_unique_states") != row.get("returned_unique_states")
           for row in records):
        raise SystemExit("refusing final manuscript: at least one raw returned state is infeasible")
    if (strict.get("strict_audit_pass") is not True
            or strict.get("protocol_sha256") != protocol
            or strict.get("campaign_jobs_audited") != 10
            or strict.get("campaign_counted_samples") != 250
            or strict.get("campaign_raw_feasible_counted_samples") != 250
            or strict.get("all_remote_configurations_match") is not True
            or strict.get("all_returned_states_raw_feasible") is not True):
        raise SystemExit("refusing final manuscript: strict remote/raw audit is incomplete or mismatched")
    strict_records = strict.get("records")
    if (not isinstance(strict_records, list)
            or len(strict_records) != 10
            or any(row.get("strict_audit_pass") is not True for row in strict_records)):
        raise SystemExit("refusing final manuscript: strict per-job audit is not 10/10")
    summary_jobs = {(row["id"], row["job_id"]) for row in records}
    strict_jobs = {(row.get("run_id"), row.get("job_id")) for row in strict_records}
    if strict_jobs != summary_jobs:
        raise SystemExit("refusing final manuscript: strict-audit job IDs do not match the live ledger")
    smoke = strict.get("smoke")
    if (not isinstance(smoke, dict)
            or smoke.get("strict_audit_pass") is not True
            or strict.get("smoke_counted_samples") != 3):
        raise SystemExit("refusing final manuscript: isolated smoke audit is missing or invalid")

    if (physical.get("analysis_complete") is not True
            or physical.get("protocol_sha256") != protocol
            or physical.get("hourly_counted_samples") != 100
            or physical.get("hourly_raw_cap_feasible_counted_samples") != 72
            or physical.get("hourly_machine_best_states_cap_feasible") != 1):
        raise SystemExit("refusing final manuscript: physical-decode audit is incomplete or mismatched")
    hourly_physical = physical.get("hourly")
    expected_hourly_ids = {f"ieee39_hour_h{hour}" for hour in range(16, 20)}
    if (not isinstance(hourly_physical, list)
            or len(hourly_physical) != 4
            or {row.get("run_id") for row in hourly_physical} != expected_hourly_ids
            or sum(int(row.get("counted_samples", -1)) for row in hourly_physical) != 100
            or sum(int(row.get("raw_cap_feasible_counted_samples", -1))
                   for row in hourly_physical) != 72):
        raise SystemExit("refusing final manuscript: physical hourly records are incomplete")
    physical_by_id = {row["run_id"]: row for row in hourly_physical}
    h16_overrun = require_close(
        physical_by_id["ieee39_hour_h16"]["machine_objective_best_state"][
            "maximum_cap_overrun_mw"
        ],
        0.0,
        "h16 best-state cap overrun",
    )
    h17_overrun = require_close(
        physical_by_id["ieee39_hour_h17"]["machine_objective_best_state"][
            "cap_overrun_mw"
        ]["p_dis"],
        17.903369494160387,
        "h17 storage-discharge overrun",
    )
    h18_overrun = require_close(
        physical_by_id["ieee39_hour_h18"]["machine_objective_best_state"][
            "cap_overrun_mw"
        ]["p_lg"],
        2.127329197735662,
        "h18 legacy-generation overrun",
    )
    h19_overrun = require_close(
        physical_by_id["ieee39_hour_h19"]["machine_objective_best_state"][
            "cap_overrun_mw"
        ]["p_mt"],
        2.1017508268787424,
        "h19 microturbine overrun",
    )
    window_physical = physical.get("window")
    if not isinstance(window_physical, dict) or window_physical.get("job_id") != window.get("job_id"):
        raise SystemExit("refusing final manuscript: physical window audit is missing or mismatched")
    window_aggregate_residual = require_close(
        window_physical.get("aggregate_simplex_residual_mwh"),
        0.00014754167204955593,
        "window aggregate simplex residual",
        tolerance=1e-10,
    )
    window_overrun = require_close(
        window_physical.get("maximum_cap_overrun_mw"),
        45.425363813862646,
        "window maximum cap overrun",
        tolerance=1e-8,
    )
    window_hour_mismatch = require_close(
        window_physical.get("maximum_absolute_per_hour_dispatch_minus_net_mw"),
        300.30509965998044,
        "window per-hour dispatch mismatch",
        tolerance=1e-8,
    )
    # The public release intentionally excludes the pre-hardware manuscript.
    # In that final-state tree, retain this script as an evidence verifier:
    # rerunning it rechecks every hardware binding above and then confirms that
    # the already-integrated manuscript carries the audited values.  The clean
    # foundation still follows the original pending-template generation path.
    if not SOURCE.exists():
        if not TARGET.exists():
            raise SystemExit(
                "refusing final manuscript: neither pending nor final manuscript exists"
            )
        final_text = TARGET.read_text()
        required_final_text = (
            "250/250",
            "72/100",
            f"{evidence_wall_s:.3f}",
            f"{campaign_wall_s:.3f}",
            "gate-model qubit count, circuit depth, and shots are not applicable",
        )
        missing = [
            value for value in required_final_text
            if value.casefold() not in final_text.casefold()
        ]
        forbidden = (
            "hardware pending",
            "review copy",
            "[[STRICT_PHYSICAL_AUDIT_INSERT]]",
        )
        present = [
            value for value in forbidden
            if value.casefold() in final_text.casefold()
        ]
        if missing or present:
            raise SystemExit(
                "refusing final manuscript: already-integrated text does not "
                f"match audited evidence (missing={missing}, forbidden={present})"
            )
        print(f"verified already-integrated {TARGET}")
        return 0

    text = SOURCE.read_text()
    total_samples = sum(int(row["total_samples_counted"]) for row in records)
    total_seconds, measured_jobs, _ = measured_usage(records)
    evidence_seconds, evidence_jobs, _ = measured_usage(evidence)
    valid_evidence = sum(row["audit_pass"] and row["evidence_class"] == "REGISTERED_EVIDENCE"
                         for row in records)
    text = text.replace(
        "A frozen 10-job, 250-sample Dirac-3 campaign is supplied; this review copy reports no hardware result before execution.",
        (f"The frozen Dirac-3 campaign returned {total_samples}/{total_samples} requested samples "
         f"across 10/10 audited jobs. All {valid_evidence}/9 in-spec jobs passed immutable configuration "
         f"and raw-feasibility checks, producing 225 evidence samples in {evidence_seconds:.0f} measured "
         "device seconds. Exact classical optima were recovered for IEEE-33 Stages 1 and 2 and the "
         "native cubic-qudit Stage 3; the matched continuous gap was 0.00378%. IEEE-39 best gaps were "
         "0.00606%-1.728% hourly and 1.039% for the certified 13-variable window. The separately "
         "labelled 121-variable probe returned samples but remains outside coefficient-resolution "
         "guidance and is not optimization evidence."),
    )
    text = text.replace(
        "device results will be compared with exact enumeration, HiGHS MILP, deterministic one-dimensional optimization, SLSQP on the identical simplex polynomial, and seeded simulated annealing.",
        "device results are compared with exact enumeration, HiGHS MILP, deterministic one-dimensional optimization, an analytic convex-simplex KKT oracle, and seeded simulated annealing.",
    )
    text = text.replace("| Live evidence group | Jobs | Samples/job | Evidence class | Review-copy status |",
                        "| Live evidence group | Jobs | Samples/job | Evidence class | Measured result |")

    for group in dict.fromkeys(row["group"] for row in records):
        subset = [row for row in records if row["group"] == group]
        valid = sum(bool(row["audit_pass"]) for row in subset)
        samples = sum(int(row["total_samples_counted"]) for row in subset)
        seconds = sum(float(row.get("device_usage_s") or 0.0) for row in subset)
        gaps = [float(row["relative_gap"]) for row in subset if row.get("relative_gap") is not None]
        if group == "IEEE39 4-hour window":
            gaps = [float(certified["hardware"]["relative_gap"])]
        if group == "IEEE39 coefficient-resolution probe":
            measured = f"{valid}/{len(subset)} valid; n={samples}; {seconds:.0f} s; out-of-guidance"
        else:
            gap = (f"; best gap {100*min(gaps):.5g}%"
                   + (f"-{100*max(gaps):.5g}%"
                      if len(gaps) > 1 and max(gaps) - min(gaps) > 1e-15 else "")) if gaps else ""
            measured = f"{valid}/{len(subset)} valid; n={samples}; {seconds:.0f} s{gap}"
        rows = {
            "IEEE39 hourly h16-h19": "IEEE-39 hourly dispatch h16-h19",
            "IEEE39 4-hour window": "IEEE-39 four-hour window",
            "IEEE39 coefficient-resolution probe": "IEEE-39 cubic coefficient probe",
            "IEEE33 Stages 1-2": "IEEE-33 Stages 1-2",
            "IEEE33 matched Stage 3": "IEEE-33 matched Stage 3",
        }
        label = rows[group]
        old_lines = [line for line in text.splitlines() if line.startswith(f"| {label} |")]
        if len(old_lines) != 1:
            raise RuntimeError(f"could not find unique table row for {label}")
        parts = [part.strip() for part in old_lines[0].strip("|").split("|")]
        parts[-1] = measured
        text = text.replace(old_lines[0], "| " + " | ".join(parts) + " |")

    replacement = (
        f"All {total_samples} requested campaign samples were returned and every raw state passed its "
        f"registered domain and balance check. The nine in-spec jobs used {evidence_seconds:.0f} measured "
        "device seconds. IEEE-33 Stages 1, 2, and cubic-qudit Stage 3 reached their exact ground states; "
        "the matched continuous Stage-3 gap was 3.78e-5. The four IEEE-39 hourly best gaps ranged from "
        "6.06e-5 to 1.728e-2. For the 13-variable window, an analytic KKT/water-filling oracle supersedes "
        f"the non-converged pre-run SLSQP record and certifies a {100*certified['hardware']['relative_gap']:.3f}% "
        f"best gap over 25 feasible samples. The 121-variable cubic probe returned 25 samples in "
        f"{float(probe['device_usage_s']):.0f} s, but its 5.22e8 coefficient spread fails the documented "
        "resolution guidance; it is hardware-boundary characterization, not solution-quality evidence. "
        f"Immutable receipt timestamps give {evidence_wall_s:.3f} s summed submit-to-collect wall time "
        f"for the nine evidence jobs and {campaign_wall_s:.3f} s from first submission to final collection "
        f"for the sequential ten-job campaign; QCi reports {evidence_seconds:.0f} and "
        f"{campaign_device_s:.0f} device seconds, respectively. "
        f"The completed ledger, raw QCi responses, job IDs, and deterministic certification are packaged "
        "under results/live."
    )
    text = text.replace(
        "After execution, a machine-generated table will replace the status column and report job IDs, valid sample counts, locally evaluated energy gaps, and measured seconds. An API rejection of the probe is itself a concrete characterization result, not a missing row.",
        replacement,
    )
    physical_replacement = (
        "The strict post-run audit independently re-read the immutable responses, receipts, and returned "
        f"remote configurations: all {strict['campaign_counted_samples']}/250 campaign samples pass their "
        "registered machine domains. That result is deliberately separated from physical dispatch. Across "
        "the four IEEE-39 hourly objectives, "
        f"{physical['hourly_raw_cap_feasible_counted_samples']}/100 raw samples are within calibrated "
        f"source caps, and only {physical['hourly_machine_best_states_cap_feasible']}/4 "
        "machine-objective best states is cap-compliant. Best-state overruns are "
        f"{h17_overrun:.3f} MW (h17 storage discharge), {h18_overrun:.3f} MW "
        f"(h18 legacy generation), and {h19_overrun:.3f} MW (h19 microturbine); "
        f"h16 has {h16_overrun:.0f} MW. The 13-variable window enforces only total four-hour energy "
        f"adequacy (aggregate residual {window_aggregate_residual:.6f} MWh): its best state has a "
        f"{window_overrun:.3f} MW maximum cap overrun and a {window_hour_mismatch:.3f} MW maximum "
        "pre-repair per-hour dispatch-minus-load mismatch. These diagnostics never project, repair, or "
        "replace a state for hardware credit."
    )
    if text.count("[[STRICT_PHYSICAL_AUDIT_INSERT]]") != 1:
        raise RuntimeError("could not find the unique strict/physical-audit insertion marker")
    text = text.replace("[[STRICT_PHYSICAL_AUDIT_INSERT]]", physical_replacement)
    text = text.replace(
        "Hardware values, the official unmodified cover, and the registered public repository URL remain mandatory before submission.",
        "The results show reproducible hardware compatibility and measured solution quality, not quantum speedup or asymptotic scaling.",
    )
    if ("hardware pending" in text.lower()
            or "review copy" in text.lower()
            or "[[STRICT_PHYSICAL_AUDIT_INSERT]]" in text):
        raise RuntimeError("final manuscript still contains a hardware-pending marker")
    if TARGET.exists() and TARGET.read_text() != text:
        raise SystemExit(f"refusing to overwrite differing final manuscript: {TARGET}")
    TARGET.write_text(text)
    print(f"verified {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
