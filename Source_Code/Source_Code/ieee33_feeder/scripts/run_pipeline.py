#!/usr/bin/env python3
"""Generate exact local oracles, metrics, tables and 27 Dirac-3 payloads."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qci_phase3.microgrid import (  # noqa: E402
    CONTINGENCIES, CRITICAL_SERVICE_IDS, DESIGN_LABELS,
    DESIGN_RISK_VALUE_USD_PER_MWH, PRIMARY_CANDIDATES,
    build_stage1_design_hamiltonian, build_stage2_islanding_hamiltonian,
    build_stage3b_exact_balance_hamiltonian,
    build_stage3s_sumconstraint_hamiltonian, critical_only_baseline,
    decode_design, decode_islanding, decode_stage3b, decode_stage3s,
    exact_solve_stage3s, two_unit_pockets,
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def payload(poly, name: str, metadata: dict, exact: dict,
            *, variables: list[dict] | None = None,
            job_hint: dict | None = None, decoded: dict | None = None) -> dict:
    record = poly.to_qci_polynomial_file(name)
    record["metadata"] = metadata
    record["local_exact_ground_state"] = exact
    if variables is not None:
        record["variable_map"] = variables
    if job_hint is not None:
        record["job_params_hint"] = job_hint
    if decoded is not None:
        record["local_decoded_solution"] = decoded
    if not record["coefficient_resolution_audit_local_only"]["pass"]:
        raise RuntimeError(f"recommended payload {name} fails coefficient resolution")
    return record


def main() -> int:
    payload_dir = ROOT / "qci_payloads"
    result_dir = ROOT / "results"
    payload_dir.mkdir(parents=True, exist_ok=True)
    for old in payload_dir.glob("*.json"):
        old.unlink()

    all_runs = []
    pareto_rows, scenario_rows, stage3b_rows, stage3s_rows = [], [], [], []
    metric_rows, resource_rows = [], []

    for mode in DESIGN_RISK_VALUE_USD_PER_MWH:
        h1, items1, meta1 = build_stage1_design_hamiltonian(mode)
        oracle1 = h1.exact_solve_binary()
        design = decode_design(oracle1["x"], items1)
        p1 = payload(
            h1, f"stage1_{mode}_design", meta1,
            {k: v for k, v in oracle1.items() if k != "x"},
            variables=items1,
            job_hint={"job_type": "sample-hamiltonian-integer",
                      "device_type": "dirac-3", "num_levels": [2] * h1.nvars},
            decoded=design,
        )
        write_json(payload_dir / f"stage1_{mode}_design.json", p1)
        resource_rows.append({
            "design_mode": mode, "objective": "stage1_design",
            "variables": h1.nvars, "terms": len(h1.terms), "rank": h1.max_degree,
            "domain": f"binary; {2*h1.nvars} total levels",
            "coefficient_spread": round(h1.coefficient_resolution_audit()["coefficient_spread"], 6),
            "min_distinct_separation": round(h1.coefficient_resolution_audit()["min_pairwise_distinct_separation"], 6),
            "resolution_pass": True,
        })

        scenarios = []
        stage3b = []
        stage3s = []
        for contingency in CONTINGENCIES:
            h2, selected, meta2 = build_stage2_islanding_hamiltonian(design, contingency, mode)
            oracle2 = h2.exact_solve_binary()
            islanding = decode_islanding(oracle2["x"], selected, contingency)
            p2 = payload(
                h2, f"stage2_{mode}_{contingency.name}", meta2,
                {k: v for k, v in oracle2.items() if k != "x"},
                job_hint={"job_type": "sample-hamiltonian-integer",
                          "device_type": "dirac-3", "num_levels": [2] * h2.nvars},
                decoded=islanding,
            )
            write_json(payload_dir / f"stage2_{mode}_{contingency.name}.json", p2)
            pocket_hours = sum(contingency.duration_h for service in CRITICAL_SERVICE_IDS
                               if service not in islanding["active_service_ids"])
            scenario_record = {
                "design_mode": mode, "scenario": contingency.name,
                "event_rate_per_year": contingency.event_rate_per_year,
                "active_islands": len(islanding["active_islands"]),
                "available_selected_options": len(selected),
                "customer_fraction_served": islanding["customer_fraction_served"],
                "max_customer_fraction_unserved": islanding["max_customer_fraction_unserved"],
                "load_fraction_served": islanding["load_fraction_served"],
                "load_fraction_unserved": islanding["load_fraction_unserved"],
                "critical_fraction_served": islanding["critical_fraction_served"],
                "critical_unserved_mwh": islanding["critical_unserved_mwh"],
                "critical_unserved_pocket_hours": pocket_hours,
                "ground_energy": oracle2["energy"],
            }
            scenarios.append(scenario_record); scenario_rows.append(scenario_record)
            audit2 = h2.coefficient_resolution_audit()
            resource_rows.append({
                "design_mode": mode, "objective": f"stage2_{contingency.name}",
                "variables": h2.nvars, "terms": len(h2.terms), "rank": h2.max_degree,
                "domain": f"binary; {2*h2.nvars} total levels",
                "coefficient_spread": round(audit2["coefficient_spread"], 6),
                "min_distinct_separation": round(audit2["min_pairwise_distinct_separation"], 6),
                "resolution_pass": audit2["pass"],
            })

            for pocket in two_unit_pockets(design, contingency):
                h3b, vars3b, meta3b, levels3b = build_stage3b_exact_balance_hamiltonian(
                    pocket, contingency, mode)
                oracle3b = h3b.exact_solve_integer(levels3b)
                decoded3b = decode_stage3b(oracle3b["solution"], vars3b)
                stem3b = f"stage3b_{mode}_{contingency.name}_{pocket['service_id']}"
                p3b = payload(
                    h3b, stem3b, meta3b,
                    {k: v for k, v in oracle3b.items() if k != "solution"},
                    job_hint={"job_type": "sample-hamiltonian-integer",
                              "device_type": "dirac-3", "num_levels": levels3b},
                    decoded=decoded3b,
                )
                write_json(payload_dir / f"{stem3b}.json", p3b)
                primary_share = next(v["share_of_load"] for v in
                                     decoded3b["dispatch_by_unit"].values()
                                     if v["unit_kind"].startswith("primary"))
                audit3b = h3b.coefficient_resolution_audit()
                rec3b = {
                    "design_mode": mode, "scenario": contingency.name,
                    "service_id": pocket["service_id"], "pocket_load_mw": pocket["load_mw"],
                    "variables": 1, "num_levels": 3, "rank": 3,
                    "linear_coefficient": h3b.terms[(0,)],
                    "quadratic_coefficient": h3b.terms[(0, 0)],
                    "cubic_coefficient": h3b.terms[(0, 0, 0)],
                    "coefficient_spread": audit3b["coefficient_spread"],
                    "min_distinct_separation": audit3b["min_pairwise_distinct_separation"],
                    "optimal_level": oracle3b["solution"][0],
                    "primary_share": primary_share,
                    "balance_residual_mw": decoded3b["balance_residual_mw"],
                    "all_units_within_capacity": decoded3b["all_units_within_capacity"],
                }
                stage3b.append(rec3b); stage3b_rows.append(rec3b)
                resource_rows.append({
                    "design_mode": mode, "objective": stem3b,
                    "variables": 1, "terms": len(h3b.terms), "rank": 3,
                    "domain": "one integer qudit; 3 levels",
                    "coefficient_spread": round(audit3b["coefficient_spread"], 6),
                    "min_distinct_separation": round(audit3b["min_pairwise_distinct_separation"], 6),
                    "resolution_pass": audit3b["pass"],
                })

                h3s, vars3s, meta3s = build_stage3s_sumconstraint_hamiltonian(
                    pocket, contingency, mode)
                oracle3s = exact_solve_stage3s(h3s)
                decoded3s = decode_stage3s(oracle3s["solution"], vars3s)
                stem3s = f"stage3s_{mode}_{contingency.name}_{pocket['service_id']}"
                p3s = payload(
                    h3s, stem3s, meta3s, oracle3s,
                    job_hint={"job_type": "sample-hamiltonian",
                              "device_type": "dirac-3", "sum_constraint": 1.0},
                    decoded=decoded3s,
                )
                write_json(payload_dir / f"{stem3s}.json", p3s)
                primary_share_s = next(v["share_of_load"] for v in
                                       decoded3s["dispatch_by_unit"].values()
                                       if v["unit_kind"].startswith("primary"))
                audit3s = h3s.coefficient_resolution_audit()
                rec3s = {
                    "design_mode": mode, "scenario": contingency.name,
                    "service_id": pocket["service_id"], "pocket_load_mw": pocket["load_mw"],
                    "variables": 2, "rank": 3, "sum_constraint": 1.0,
                    "coefficient_spread": audit3s["coefficient_spread"],
                    "min_distinct_separation": audit3s["min_pairwise_distinct_separation"],
                    "primary_share": primary_share_s,
                    "balance_residual_mw": decoded3s["balance_residual_mw"],
                    "ground_energy": oracle3s["energy"],
                }
                stage3s.append(rec3s); stage3s_rows.append(rec3s)
                resource_rows.append({
                    "design_mode": mode, "objective": stem3s,
                    "variables": 2, "terms": len(h3s.terms), "rank": 3,
                    "domain": "continuous; native sum(u)=1",
                    "coefficient_spread": round(audit3s["coefficient_spread"], 6),
                    "min_distinct_separation": round(audit3s["min_pairwise_distinct_separation"], 6),
                    "resolution_pass": audit3s["pass"],
                })

        expected_critical = sum(r["event_rate_per_year"] * r["critical_unserved_mwh"]
                                for r in scenarios)
        audit_critical = sum(r["critical_unserved_mwh"] for r in scenarios)
        pocket_hours = sum(r["critical_unserved_pocket_hours"] for r in scenarios)
        worst_customer = max(r["max_customer_fraction_unserved"] for r in scenarios)
        worst_load = max(r["load_fraction_unserved"] for r in scenarios)
        compound = next(r for r in scenarios
                        if r["scenario"] == "compound_two_lateral_fault")
        row = {
            "design_mode": mode, "design_label": DESIGN_LABELS[mode],
            "risk_value_beta_usd_per_mwh": DESIGN_RISK_VALUE_USD_PER_MWH[mode],
            "upfront_cost_usd": design["upfront_cost_usd"],
            "annualized_cost_usd_per_year": design["annualized_cost_usd_yr"],
            "selected_blackstart_overlaps": design["selected_blackstart_overlap_count"],
            "worst_customer_block_fraction_unserved": worst_customer,
            "worst_load_fraction_unserved_sensitivity": worst_load,
            "audit_set_critical_unserved_mwh": audit_critical,
            "expected_annual_critical_unserved_mwh": expected_critical,
            "critical_unserved_pocket_hours": pocket_hours,
            "compound_customer_fraction_served": compound["customer_fraction_served"],
            "compound_critical_fraction_served": compound["critical_fraction_served"],
        }
        pareto_rows.append(row)
        for key, value in row.items():
            if key not in {"design_mode", "design_label"}:
                metric_rows.append({"design_mode": mode, "metric": key, "value": value})
        all_runs.append({
            "design_mode": mode, "stage1": {"oracle": oracle1, "decoded": design,
                                               "metadata": meta1},
            "stage2": scenarios, "stage3b": stage3b, "stage3s": stage3s,
        })

    baseline = critical_only_baseline()
    write_csv(result_dir / "design_pareto.csv", pareto_rows)
    write_csv(result_dir / "stage2_scenarios.csv", scenario_rows)
    write_csv(result_dir / "stage3b_exact_balance.csv", stage3b_rows)
    write_csv(result_dir / "stage3s_sumconstraint.csv", stage3s_rows)
    write_csv(result_dir / "resource_ladder.csv", resource_rows)
    write_csv(result_dir / "headline_metrics.csv", metric_rows)
    write_json(result_dir / "local_exact_results.json", {
        "status": "LOCAL_EXACT_ORACLE_NOT_HARDWARE",
        "public_data": "MATPOWER/Baran-Wu case33bw section aggregates",
        "synthetic_inputs": ("critical labels, prices, event frequencies, resource costs "
                             "and operating curves"),
        "design_runs": all_runs,
        "pareto": pareto_rows,
        "critical_only_baseline": baseline,
        "payload_files": sorted(path.name for path in payload_dir.glob("*.json")),
    })
    count = len(list(payload_dir.glob("*.json")))
    if count != 27:
        raise RuntimeError(f"expected 27 recommended payloads, generated {count}")
    print("Exact Pareto:")
    for row in pareto_rows:
        print(f"  {row['design_mode']}: ${row['upfront_cost_usd']/1e6:.2f}M; "
              f"expected critical {row['expected_annual_critical_unserved_mwh']:.1f} MWh/year; "
              f"{row['critical_unserved_pocket_hours']:.0f} pocket-hours")
    print(f"Generated {count} resolution-certified Dirac-3 payloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
