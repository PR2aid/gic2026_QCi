#!/usr/bin/env python3
"""Certify the live IEEE-39 window result without changing frozen evidence.

The submitted four-hour window objective is a separable convex quadratic on
the non-negative simplex.  Its pre-run SLSQP reference is deliberately not
used because that record is marked ``converged: false``.  This script instead
solves the identical polynomial by its KKT conditions, verifies the raw QCi
vectors and immutable hashes, and writes a deterministic analysis sidecar.

No credential, network access, projection, repair, or hardware submission is
used.  The frozen payload, protocol, receipts, raw response, and scorer remain
byte-for-byte unchanged.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_REL = Path(
    "ieee39_transmission/qci/ieee39_flagship/"
    "wdisp_c0_s171_w16_polynomial.json"
)
SUMMARY_REL = PAYLOAD_REL.with_name("wdisp_c0_s171_w16_summary.json")
FROZEN_REL = Path("results/live_protocol/frozen_campaign.json")
HARDWARE_REL = Path("results/live/hardware_summary.json")
OUTPUT_REL = Path("results/live/certified_hardware_analysis.json")
RUN_ID = "ieee39_window_h16_h19"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate(terms: list[dict], x: list[float], constant: float = 0.0) -> float:
    value = float(constant)
    for term in terms:
        product = 1.0
        for index in term["idx"]:
            if int(index) > 0:
                product *= float(x[int(index) - 1])
        value += float(term["val"]) * product
    return float(value)


def diagonal_coefficients(payload: dict) -> tuple[list[float], list[float]]:
    """Return q,b for sum(q_i*x_i^2 + b_i*x_i); reject other terms."""
    n = int(payload["num_variables"])
    if int(payload["degree"]) != 2:
        raise ValueError("certified oracle requires a rank-2 polynomial")
    q = [0.0] * n
    b = [0.0] * n
    for term in payload["polynomial"]:
        indices = [int(i) for i in term["idx"] if int(i) > 0]
        coefficient = float(term["val"])
        if len(indices) == 1:
            b[indices[0] - 1] += coefficient
        elif len(indices) == 2 and indices[0] == indices[1]:
            q[indices[0] - 1] += coefficient
        else:
            raise ValueError(f"non-separable term prevents KKT oracle: {term}")
    if any(value < 0.0 for value in q):
        raise ValueError("quadratic is not convex")
    if not any(value > 0.0 for value in q):
        raise ValueError("quadratic has no strictly convex coordinate")
    return q, b


def solve_simplex_kkt(q: list[float], b: list[float], budget: float) -> dict:
    """Solve a non-negative separable convex quadratic on sum(x)=budget."""
    if budget <= 0.0:
        raise ValueError("simplex budget must be positive")

    def total(multiplier: float) -> float:
        return sum(
            max(0.0, -(b_i + multiplier) / (2.0 * q_i))
            for q_i, b_i in zip(q, b)
            if q_i > 0.0
        )

    scale = max(
        1.0,
        max(abs(value) for value in b),
        max((2.0 * value * budget for value in q), default=1.0),
    )
    low, high = -scale, scale
    while total(low) <= budget:
        low *= 2.0
    while total(high) >= budget:
        high *= 2.0
    for _ in range(300):
        middle = (low + high) / 2.0
        if total(middle) > budget:
            low = middle
        else:
            high = middle
    multiplier = (low + high) / 2.0
    x = [
        max(0.0, -(b_i + multiplier) / (2.0 * q_i)) if q_i > 0.0 else 0.0
        for q_i, b_i in zip(q, b)
    ]
    residual = budget - sum(x)

    # A zero-curvature coordinate can carry residual only when its reduced
    # cost is zero.  The submitted instance instead has an inactive, zero-cost
    # shedding slack and a strictly positive multiplier.
    zero_curvature_reduced = [b_i + multiplier for q_i, b_i in zip(q, b) if q_i == 0.0]
    if residual > 1e-9:
        candidates = [i for i, (q_i, b_i) in enumerate(zip(q, b))
                      if q_i == 0.0 and abs(b_i + multiplier) <= 1e-9]
        if not candidates:
            raise ValueError("KKT solution cannot place the simplex residual")
        x[candidates[0]] += residual
    if any(value < -1e-10 for value in zero_curvature_reduced):
        raise ValueError("zero-curvature coordinate violates dual feasibility")

    active_residuals = [
        abs(2.0 * q_i * x_i + b_i + multiplier)
        for q_i, b_i, x_i in zip(q, b, x)
        if x_i > 0.0
    ]
    inactive_reduced = [
        b_i + multiplier
        for q_i, b_i, x_i in zip(q, b, x)
        if x_i == 0.0
    ]
    return {
        "x": x,
        "multiplier": multiplier,
        "simplex_residual": abs(sum(x) - budget),
        "max_active_stationarity_residual": max(active_residuals, default=0.0),
        "min_inactive_reduced_cost": min(inactive_reduced, default=0.0),
    }


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


def certify_receipt_timing(hardware: dict, protocol_sha256: str) -> dict:
    """Derive auditable wall times from immutable UTC receipt timestamps.

    These intervals include API, queue, and polling time and are therefore
    deliberately reported separately from QCi's ``device_usage_s`` field.
    """
    records = hardware.get("records") or []
    if len(records) != 10:
        raise ValueError("timing certificate requires all ten campaign records")
    rows = []
    for row in records:
        result_path = ROOT / row["result_file"]
        response = json.loads(result_path.read_text())
        local = response.get("_local_submission_record") or {}
        if local.get("protocol_sha256") != protocol_sha256:
            raise ValueError(f"timing receipt protocol mismatch: {row['id']}")
        if local.get("job_id") != row.get("job_id"):
            raise ValueError(f"timing receipt job ID mismatch: {row['id']}")
        submitted = datetime.fromisoformat(local["submitted_utc"])
        collected = datetime.fromisoformat(local["collected_utc"])
        wall_s = (collected - submitted).total_seconds()
        if wall_s < 0.0:
            raise ValueError(f"negative receipt interval: {row['id']}")
        rows.append({
            "run_id": row["id"],
            "evidence_class": row["evidence_class"],
            "submitted_utc": local["submitted_utc"],
            "collected_utc": local["collected_utc"],
            "submit_to_collect_wall_s": wall_s,
            "device_usage_s": float(row["device_usage_s"]),
        })
    evidence = [
        row for row in rows if row["evidence_class"] == "REGISTERED_EVIDENCE"
    ]
    first = min(datetime.fromisoformat(row["submitted_utc"]) for row in rows)
    last = max(datetime.fromisoformat(row["collected_utc"]) for row in rows)
    return {
        "definition": (
            "Wall intervals are derived from immutable client UTC receipt "
            "timestamps and include API, queue, and polling time; device "
            "seconds are the QCi-reported usage field."
        ),
        "campaign_first_submitted_utc": first.isoformat(),
        "campaign_last_collected_utc": last.isoformat(),
        "campaign_elapsed_wall_s": (last - first).total_seconds(),
        "all_job_submit_to_collect_wall_s_sum": sum(
            row["submit_to_collect_wall_s"] for row in rows
        ),
        "registered_evidence_submit_to_collect_wall_s_sum": sum(
            row["submit_to_collect_wall_s"] for row in evidence
        ),
        "registered_evidence_device_usage_s_sum": sum(
            row["device_usage_s"] for row in evidence
        ),
        "all_campaign_device_usage_s_sum": sum(
            row["device_usage_s"] for row in rows
        ),
        "per_job": rows,
    }


def build_analysis() -> dict:
    payload_path = ROOT / PAYLOAD_REL
    summary_path = ROOT / SUMMARY_REL
    frozen_path = ROOT / FROZEN_REL
    hardware_path = ROOT / HARDWARE_REL
    payload = json.loads(payload_path.read_text())
    companion = json.loads(summary_path.read_text())
    frozen = json.loads(frozen_path.read_text())
    hardware = json.loads(hardware_path.read_text())

    frozen_job = next(row for row in frozen["jobs"] if row["id"] == RUN_ID)
    hardware_row = next(row for row in hardware["records"] if row["id"] == RUN_ID)
    if not hardware_row.get("audit_pass") or hardware_row.get("status") != "COMPLETED":
        raise ValueError("window hardware record is not a completed audited result")
    if frozen_job["payload_sha256"] != sha256(payload_path):
        raise ValueError("window payload hash does not match the frozen protocol")

    result_path = ROOT / hardware_row["result_file"]
    response = json.loads(result_path.read_text())
    local = response.get("_local_submission_record", {})
    if local.get("protocol_sha256") != frozen.get("protocol_sha256"):
        raise ValueError("raw response protocol hash mismatch")
    if local.get("payload_sha256") != sha256(payload_path):
        raise ValueError("raw response payload hash mismatch")
    if local.get("job_id") != hardware_row.get("job_id"):
        raise ValueError("raw response job ID mismatch")

    q, b = diagonal_coefficients(payload)
    budget = float(payload["job_params"]["sum_constraint"])
    oracle = solve_simplex_kkt(q, b, budget)
    constant = float(companion.get("constant_offset", 0.0))
    oracle_energy = evaluate(payload["polynomial"], oracle["x"], constant)

    results = response.get("results") or {}
    solutions = results.get("solutions") or []
    counts = results.get("counts") or [1] * len(solutions)
    if len(solutions) != len(counts):
        raise ValueError("raw solutions/counts length mismatch")
    energies: list[float] = []
    balance_residuals: list[float] = []
    for solution, count in zip(solutions, counts):
        if len(solution) != int(payload["num_variables"]):
            raise ValueError("raw solution length mismatch")
        if any(float(value) < -1e-8 for value in solution):
            raise ValueError("raw solution has a negative coordinate")
        residual = abs(sum(float(value) for value in solution) - budget)
        if residual > 1e-3 * max(1.0, budget):
            raise ValueError("raw solution violates the registered simplex tolerance")
        value = evaluate(payload["polynomial"], solution, constant)
        energies.extend([value] * int(count))
        balance_residuals.extend([residual] * int(count))
    if sum(int(value) for value in counts) != int(hardware_row["total_samples_counted"]):
        raise ValueError("raw sample total differs from the audited summary")

    best = min(energies)
    gap = best - oracle_energy
    scale = max(1.0, abs(oracle_energy))
    relative_gaps = [(value - oracle_energy) / scale for value in energies]
    stored = payload.get("classical_reference") or {}
    stored_x = [float(value) for value in stored.get("x_device_units", [])]
    certification_pass = bool(
        oracle["simplex_residual"] <= 1e-12
        and oracle["max_active_stationarity_residual"] <= 1e-8
        and oracle["min_inactive_reduced_cost"] >= -1e-8
        and gap >= -1e-7
        and len(energies) == 25
    )
    execution_timing = certify_receipt_timing(
        hardware, frozen["protocol_sha256"]
    )
    return {
        "analysis_version": "qpr_qci_phase3_window_kkt_and_timing_v2",
        "certification_pass": certification_pass,
        "evidence_class": "POST_RUN_DETERMINISTIC_ANALYSIS_OF_FROZEN_EVIDENCE",
        "protocol_sha256": frozen["protocol_sha256"],
        "run_id": RUN_ID,
        "job_id": hardware_row["job_id"],
        "payload": PAYLOAD_REL.as_posix(),
        "payload_sha256": sha256(payload_path),
        "raw_result": hardware_row["result_file"],
        "raw_result_sha256": sha256(result_path),
        "method": (
            "Analytic KKT/water-filling solution of the identical separable "
            "convex quadratic over the registered non-negative simplex"
        ),
        "proof_conditions": {
            "degree": int(payload["degree"]),
            "num_variables": int(payload["num_variables"]),
            "separable": True,
            "minimum_quadratic_coefficient": min(q),
            "strictly_convex_coordinate_count": sum(value > 0.0 for value in q),
            "zero_curvature_coordinate_count": sum(value == 0.0 for value in q),
            "sum_constraint": budget,
        },
        "oracle": {
            "energy": oracle_energy,
            "x_device_units": oracle["x"],
            "lagrange_multiplier": oracle["multiplier"],
            "simplex_residual": oracle["simplex_residual"],
            "max_active_stationarity_residual": oracle["max_active_stationarity_residual"],
            "min_inactive_reduced_cost": oracle["min_inactive_reduced_cost"],
        },
        "superseded_nonconverged_slsqp_diagnostic": {
            "stored_converged": bool(stored.get("converged")),
            "stored_energy": stored.get("energy"),
            "rounded_vector_sum": sum(stored_x) if stored_x else None,
            "reason_not_used": (
                "The stored pre-run SLSQP record is marked non-converged; its "
                "rounded vector is not exactly on the simplex."
            ),
        },
        "hardware": {
            "counted_samples": len(energies),
            "returned_unique_states": len(solutions),
            "all_raw_states_feasible": True,
            "maximum_simplex_residual": max(balance_residuals, default=0.0),
            "best_energy": best,
            "absolute_gap": gap,
            "relative_gap": gap / scale,
            "energy_quantiles_q0_q25_q50_q75_q100": [
                quantile(energies, probability) for probability in (0.0, 0.25, 0.5, 0.75, 1.0)
            ],
            "relative_gap_quantiles_q0_q25_q50_q75_q100": [
                quantile(relative_gaps, probability)
                for probability in (0.0, 0.25, 0.5, 0.75, 1.0)
            ],
            "device_usage_s": hardware_row["device_usage_s"],
        },
        "execution_timing": execution_timing,
    }


def _max_relative_deviation(existing, recomputed):
    """Max relative float deviation between two JSON trees; None if the trees
    differ structurally or in any non-numeric value (fail-closed)."""
    if type(existing) is bool or type(recomputed) is bool:
        return 0.0 if type(existing) is type(recomputed) and existing == recomputed else None
    if type(existing) is int or type(recomputed) is int:
        return 0.0 if type(existing) is type(recomputed) and existing == recomputed else None
    if type(existing) is float or type(recomputed) is float:
        if type(existing) is not float or type(recomputed) is not float:
            return None
        a, b = existing, recomputed
        if not (math.isfinite(a) and math.isfinite(b)):
            return None
        return abs(a - b) / max(1.0, abs(a), abs(b))
    if isinstance(existing, dict) and isinstance(recomputed, dict):
        if set(existing) != set(recomputed):
            return None
        worst = 0.0
        for key in existing:
            dev = _max_relative_deviation(existing[key], recomputed[key])
            if dev is None:
                return None
            worst = max(worst, dev)
        return worst
    if isinstance(existing, list) and isinstance(recomputed, list):
        if len(existing) != len(recomputed):
            return None
        worst = 0.0
        for left, right in zip(existing, recomputed):
            dev = _max_relative_deviation(left, right)
            if dev is None:
                return None
            worst = max(worst, dev)
        return worst
    return 0.0 if type(existing) is type(recomputed) and existing == recomputed else None


# Recomputation is numerically deterministic but not bit-identical across
# CPU/BLAS microarchitectures (last-ULP reduction differences in derived
# floats).  An existing certificate is accepted, and deliberately left
# byte-unchanged, when the recomputation matches it within this relative
# tolerance; any structural or larger difference still fails closed.
CERTIFICATE_EQUIV_REL_TOL = 1e-9


def main() -> int:
    analysis = build_analysis()
    if not analysis["certification_pass"]:
        raise SystemExit("FAIL: deterministic live-result certification did not pass")
    output = ROOT / OUTPUT_REL
    serialized = json.dumps(analysis, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if output.exists() and output.read_text() != serialized:
        deviation = _max_relative_deviation(json.loads(output.read_text()), analysis)
        if deviation is None or deviation > CERTIFICATE_EQUIV_REL_TOL:
            raise SystemExit(f"FAIL: refusing to overwrite differing analysis: {OUTPUT_REL}")
        print(
            "PASS certified IEEE-39 window: "
            f"gap={analysis['hardware']['relative_gap']:.8%}, "
            f"samples={analysis['hardware']['counted_samples']}"
        )
        print(
            f"existing {OUTPUT_REL} matches recomputation within "
            f"{deviation:.3e} relative deviation (<= {CERTIFICATE_EQUIV_REL_TOL:.0e}); "
            "packaged certificate left byte-unchanged"
        )
        return 0
    output.write_text(serialized)
    print(
        "PASS certified IEEE-39 window: "
        f"gap={analysis['hardware']['relative_gap']:.8%}, "
        f"samples={analysis['hardware']['counted_samples']}"
    )
    print(f"wrote {OUTPUT_REL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
