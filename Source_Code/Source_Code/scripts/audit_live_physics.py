#!/usr/bin/env python3
"""Decode the IEEE-39 raw Dirac states against physical dispatch limits.

The registered continuous machine domain is a non-negative simplex.  Source
capacity limits are represented by calibrated quadratic objective walls, not
hard machine constraints.  This credential-free analysis therefore reports
equipment-cap and per-hour-balance diagnostics separately from the immutable
hardware score.  No state is projected or repaired for evidence credit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_live_dirac3 as frozen_runner  # noqa: E402


HARDWARE = ROOT / "results/live/hardware_summary.json"
STRICT = ROOT / "results/live/strict_evidence_audit.json"
OUTPUT = ROOT / "results/live/physical_decode_audit.json"


def companion_for(payload_path: Path) -> Path:
    if not payload_path.name.endswith("_polynomial.json"):
        raise ValueError(f"unexpected IEEE-39 payload name: {payload_path.name}")
    return payload_path.with_name(payload_path.name.replace("_polynomial.json", "_summary.json"))


def physical_state(meta: dict, names: list[str], solution: list[float]) -> dict[str, float]:
    unit = float(meta["u_mw_per_unit"])
    return {name: float(value) * unit for name, value in zip(names, solution)}


def cap_overruns(physical: dict[str, float], caps: dict[str, float]) -> dict[str, float]:
    overruns = {}
    for name, value in physical.items():
        source = name.split("[")[0]
        if source in caps:
            overruns[name] = max(0.0, value - float(caps[source]))
    return overruns


def local_energy(payload: dict, solution: list[float]) -> float:
    return frozen_runner.evaluate(payload["terms"], solution, payload["constant"])


def audit_hour(descriptor: dict, row: dict) -> dict:
    payload = frozen_runner.normalized_payload(descriptor)
    companion = json.loads(companion_for(payload["path"]).read_text())
    meta = companion["meta"]
    names = companion["var_names"]
    caps = {key: float(value) for key, value in meta["caps_mw"].items()}
    response = json.loads((ROOT / row["result_file"]).read_text())
    solutions = response["results"]["solutions"]
    counts = [int(value) for value in response["results"]["counts"]]
    decoded = []
    for index, (solution, count) in enumerate(zip(solutions, counts)):
        physical = physical_state(meta, names, solution)
        overruns = cap_overruns(physical, caps)
        maximum = max(overruns.values(), default=0.0)
        energy = local_energy(payload, solution)
        decoded.append({
            "index": index,
            "count": count,
            "energy": energy,
            "physical_mw": physical,
            "cap_overrun_mw": overruns,
            "maximum_cap_overrun_mw": maximum,
            "cap_feasible": maximum <= 1e-6,
            "raw_shed_mwh": physical.get("s_shed", 0.0),
            "raw_hourly_balance_residual_mw": abs(
                sum(physical.values()) - float(meta["net_load_mw"])
            ),
        })
    machine_best = decoded[int(row["best_feasible_index"])]
    cap_feasible = [record for record in decoded if record["cap_feasible"]]
    cap_best = min(cap_feasible, key=lambda record: record["energy"]) if cap_feasible else None
    reference = float(row["classical_reference_energy"])

    def selected(record: dict | None) -> dict | None:
        if record is None:
            return None
        return {
            "index": record["index"],
            "energy_identical_machine_objective": record["energy"],
            "relative_gap_identical_machine_objective": (
                record["energy"] - reference
            ) / max(1.0, abs(reference)),
            "physical_mw": record["physical_mw"],
            "cap_overrun_mw": record["cap_overrun_mw"],
            "maximum_cap_overrun_mw": record["maximum_cap_overrun_mw"],
            "raw_shed_mwh": record["raw_shed_mwh"],
            "raw_hourly_balance_residual_mw": record["raw_hourly_balance_residual_mw"],
        }

    return {
        "run_id": descriptor["id"],
        "job_id": row["job_id"],
        "net_load_mw": float(meta["net_load_mw"]),
        "source_caps_mw": caps,
        "returned_unique_states": len(decoded),
        "counted_samples": sum(counts),
        "raw_cap_feasible_unique_states": len(cap_feasible),
        "raw_cap_feasible_counted_samples": sum(
            record["count"] for record in cap_feasible
        ),
        "machine_objective_best_state": selected(machine_best),
        "cap_feasible_best_state": selected(cap_best),
    }


def audit_window(descriptor: dict, row: dict) -> dict:
    payload = frozen_runner.normalized_payload(descriptor)
    companion = json.loads(companion_for(payload["path"]).read_text())
    meta = companion["meta"]
    names = companion["var_names"]
    caps = {key: float(value) for key, value in meta["caps_mw"].items()}
    response = json.loads((ROOT / row["result_file"]).read_text())
    solution = response["results"]["solutions"][int(row["best_feasible_index"])]
    physical = physical_state(meta, names, solution)
    overruns = cap_overruns(physical, caps)
    per_hour = []
    for hour, net in enumerate(meta["hourly_net_mw"]):
        dispatch = {
            source: physical.get(f"{source}[{hour}]", 0.0)
            for source in caps
        }
        per_hour.append({
            "hour_offset": hour,
            "net_load_mw": float(net),
            "raw_dispatch_mw": dispatch,
            "raw_dispatch_minus_net_mw": sum(dispatch.values()) - float(net),
        })
    total_net = sum(float(value) for value in meta["hourly_net_mw"])
    total_raw = sum(physical.values())
    return {
        "run_id": descriptor["id"],
        "job_id": row["job_id"],
        "evidence_boundary": (
            "The machine enforces only total-window energy adequacy. The "
            "aggregate shed slack is not assigned to individual hours."
        ),
        "machine_objective_best_index": int(row["best_feasible_index"]),
        "total_window_net_load_mwh": total_net,
        "raw_total_dispatch_plus_aggregate_shed_mwh": total_raw,
        "aggregate_simplex_residual_mwh": abs(total_raw - total_net),
        "aggregate_raw_shed_mwh": physical.get("s_shed", 0.0),
        "cap_overrun_mw": overruns,
        "maximum_cap_overrun_mw": max(overruns.values(), default=0.0),
        "per_hour_diagnostic_before_any_repair": per_hour,
        "maximum_absolute_per_hour_dispatch_minus_net_mw": max(
            abs(record["raw_dispatch_minus_net_mw"]) for record in per_hour
        ),
    }


def main() -> int:
    hardware = json.loads(HARDWARE.read_text())
    strict = json.loads(STRICT.read_text())
    if not strict.get("strict_audit_pass"):
        raise SystemExit("strict machine-domain evidence audit must pass first")
    rows = {row["id"]: row for row in hardware["records"]}
    hourly = []
    for hour in range(16, 20):
        run_id = f"ieee39_hour_h{hour}"
        descriptor = next(row for row in frozen_runner.CAMPAIGN if row["id"] == run_id)
        hourly.append(audit_hour(descriptor, rows[run_id]))
    window_id = "ieee39_window_h16_h19"
    window_descriptor = next(
        row for row in frozen_runner.CAMPAIGN if row["id"] == window_id
    )
    window = audit_window(window_descriptor, rows[window_id])
    report = {
        "analysis_version": "qpr_qci_phase3_ieee39_physical_decode_v1",
        "evidence_class": "POST_RUN_PHYSICAL_DIAGNOSTIC_OF_RAW_MACHINE_STATES",
        "protocol_sha256": hardware["protocol_sha256"],
        "interpretation": (
            "Machine-domain feasibility is not physical feasibility. Hourly "
            "simplex balance is exact, but calibrated capacity walls are soft "
            "objective terms. No projection, clamp, or redispatch changes the "
            "immutable hardware score."
        ),
        "hourly": hourly,
        "hourly_returned_unique_states": sum(
            row["returned_unique_states"] for row in hourly
        ),
        "hourly_raw_cap_feasible_unique_states": sum(
            row["raw_cap_feasible_unique_states"] for row in hourly
        ),
        "hourly_counted_samples": sum(row["counted_samples"] for row in hourly),
        "hourly_raw_cap_feasible_counted_samples": sum(
            row["raw_cap_feasible_counted_samples"] for row in hourly
        ),
        "hourly_machine_best_states_cap_feasible": sum(
            row["machine_objective_best_state"]["maximum_cap_overrun_mw"] <= 1e-6
            for row in hourly
        ),
        "window": window,
        "analysis_complete": (
            len(hourly) == 4
            and sum(row["counted_samples"] for row in hourly) == 100
            and window["aggregate_simplex_residual_mwh"] <= 1e-2
        ),
    }
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        "IEEE-39 physical decode: hourly cap-feasible raw samples "
        f"{report['hourly_raw_cap_feasible_counted_samples']}/"
        f"{report['hourly_counted_samples']}; best states cap-feasible "
        f"{report['hourly_machine_best_states_cap_feasible']}/4"
    )
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0 if report["analysis_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
