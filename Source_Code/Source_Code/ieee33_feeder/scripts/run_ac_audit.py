#!/usr/bin/env python3
"""Nonlinear radial AC load-flow screening for the Phase-3 feeder.

This credential-free classical gate uses the public MATPOWER ``case33bw``
constant-PQ loads and active radial branches. A backward/forward sweep solves
the balanced single-phase equivalent in per unit for the grid-connected feeder
and for each submitted island rooted at its local grid-forming DER. It checks
convergence, 0.90--1.10 p.u. voltage limits, real losses, and whether product
nameplate active power covers the solved source injection (load plus losses).

This is an electrical feasibility screen, not an AC-OPF, protection,
frequency/transient, harmonic, or inverter-Q-capability certificate.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qci_phase3.microgrid import (
    CONTINGENCIES,
    build_stage1_design_hamiltonian,
    build_stage2_islanding_hamiltonian,
    decode_design,
    decode_islanding,
)

BASE_MVA = 10.0
BASE_KV = 12.66
VMIN_PU = 0.90
VMAX_PU = 1.10

# Public MATPOWER case33bw data: bus -> (kW, kVAr).
LOAD_KW_KVAR = {
    1: (0, 0), 2: (100, 60), 3: (90, 40), 4: (120, 80), 5: (60, 30),
    6: (60, 20), 7: (200, 100), 8: (200, 100), 9: (60, 20),
    10: (60, 20), 11: (45, 30), 12: (60, 35), 13: (60, 35),
    14: (120, 80), 15: (60, 10), 16: (60, 20), 17: (60, 20),
    18: (90, 40), 19: (90, 40), 20: (90, 40), 21: (90, 40),
    22: (90, 40), 23: (90, 50), 24: (420, 200), 25: (420, 200),
    26: (60, 25), 27: (60, 25), 28: (60, 20), 29: (120, 70),
    30: (200, 600), 31: (150, 70), 32: (210, 100), 33: (60, 40),
}

# Active radial branches only: (from bus, to bus, r ohm, x ohm).
BRANCHES = [
    (1, 2, .0922, .0470), (2, 3, .4930, .2511), (3, 4, .3660, .1864),
    (4, 5, .3811, .1941), (5, 6, .8190, .7070), (6, 7, .1872, .6188),
    (7, 8, .7114, .2351), (8, 9, 1.0300, .7400), (9, 10, 1.0440, .7400),
    (10, 11, .1966, .0650), (11, 12, .3744, .1238), (12, 13, 1.4680, 1.1550),
    (13, 14, .5416, .7129), (14, 15, .5910, .5260), (15, 16, .7463, .5450),
    (16, 17, 1.2890, 1.7210), (17, 18, .7320, .5740),
    (2, 19, .1640, .1565), (19, 20, 1.5042, 1.3554),
    (20, 21, .4095, .4784), (21, 22, .7089, .9373),
    (3, 23, .4512, .3083), (23, 24, .8980, .7091), (24, 25, .8960, .7011),
    (6, 26, .2030, .1034), (26, 27, .2842, .1447), (27, 28, 1.0590, .9337),
    (28, 29, .8042, .7006), (29, 30, .5075, .2585), (30, 31, .9744, .9630),
    (31, 32, .3105, .3619), (32, 33, .3410, .5302),
]

SECTIONS = {
    "MG_trunk_1_17": {"root": 2, "nodes": list(range(2, 19))},
    "MG_lateral_18_21": {"root": 19, "nodes": list(range(19, 23))},
    "MG_lateral_22_24": {"root": 23, "nodes": list(range(23, 26))},
    "MG_lateral_25_32": {"root": 26, "nodes": list(range(26, 34))},
}


def solve_radial(nodes: list[int], root: int, tol: float = 1e-11,
                 max_iter: int = 1000) -> dict:
    node_set = set(nodes)
    adjacency: dict[int, list[tuple[int, complex]]] = {n: [] for n in nodes}
    zbase_ohm = (BASE_KV * 1e3) ** 2 / (BASE_MVA * 1e6)
    for a, b, r, x in BRANCHES:
        if a in node_set and b in node_set:
            z = complex(r, x) / zbase_ohm
            adjacency[a].append((b, z)); adjacency[b].append((a, z))

    parent: dict[int, int | None] = {root: None}
    z_to_parent: dict[int, complex] = {}
    order = [root]
    for u in order:
        for v, z in adjacency[u]:
            if v not in parent:
                parent[v] = u
                z_to_parent[v] = z
                order.append(v)
    if set(order) != node_set:
        raise RuntimeError(f"section rooted at bus {root} is disconnected")

    children = {n: [] for n in nodes}
    for n, p in parent.items():
        if p is not None:
            children[p].append(n)

    s_load = {n: complex(*LOAD_KW_KVAR[n]) / 1000.0 / BASE_MVA for n in nodes}
    v = {n: 1.0 + 0.0j for n in nodes}
    branch_i: dict[int, complex] = {}
    converged = False
    residual = float("inf")
    for iteration in range(1, max_iter + 1):
        i_total = {n: np.conj(s_load[n] / v[n]) for n in nodes}
        for n in reversed(order[1:]):
            branch_i[n] = i_total[n]
            p = parent[n]
            assert p is not None
            i_total[p] += branch_i[n]
        new_v = {root: 1.0 + 0.0j}
        for n in order[1:]:
            p = parent[n]
            assert p is not None
            new_v[n] = new_v[p] - z_to_parent[n] * branch_i[n]
        residual = max(abs(new_v[n] - v[n]) for n in nodes)
        v = new_v
        if residual <= tol:
            converged = True
            break

    # Recompute currents at the converged voltage for consistent source/losses.
    i_total = {n: np.conj(s_load[n] / v[n]) for n in nodes}
    for n in reversed(order[1:]):
        branch_i[n] = i_total[n]
        p = parent[n]
        assert p is not None
        i_total[p] += branch_i[n]
    source_s_mva = v[root] * np.conj(i_total[root]) * BASE_MVA
    losses_mw = sum((abs(branch_i[n]) ** 2 * z_to_parent[n]).real * BASE_MVA
                    for n in order[1:])
    vm = {n: abs(v[n]) for n in nodes}
    min_bus = min(vm, key=vm.get)
    return {
        "converged": converged,
        "iterations": iteration,
        "fixed_point_residual": residual,
        "root_bus": root,
        "num_buses": len(nodes),
        "load_mw": sum(LOAD_KW_KVAR[n][0] for n in nodes) / 1000.0,
        "load_mvar": sum(LOAD_KW_KVAR[n][1] for n in nodes) / 1000.0,
        "source_mw": float(source_s_mva.real),
        "source_mvar": float(source_s_mva.imag),
        "real_loss_mw": float(losses_mw),
        "min_voltage_pu": float(vm[min_bus]),
        "min_voltage_bus": int(min_bus),
        "max_voltage_pu": float(max(vm.values())),
        "voltage_within_0p90_1p10": bool(min(vm.values()) >= VMIN_PU - 1e-9
                                         and max(vm.values()) <= VMAX_PU + 1e-9),
    }


def main() -> int:
    out_dir = REPO / "results" / "ac_powerflow"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = solve_radial(list(range(1, 34)), 1)
    section_results = {sid: solve_radial(cfg["nodes"], cfg["root"])
                       for sid, cfg in SECTIONS.items()}

    rows = []
    design_summaries = []
    for mode in ("cost_efficient", "balanced_critical", "robust_critical"):
        h1, items, _ = build_stage1_design_hamiltonian(mode)
        design = decode_design(h1.exact_solve_binary()["x"], items)
        scenario_rows = []
        for scen in CONTINGENCIES:
            h2, selected, _ = build_stage2_islanding_hamiltonian(design, scen, mode)
            islanding = decode_islanding(h2.exact_solve_binary()["x"], selected, scen)
            service_checks = []
            for item in islanding["active_islands"]:
                sid = item["service_id"]
                ac = section_results[sid]
                margin = item["product_power_mw"] - ac["source_mw"]
                service_checks.append({
                    "service_id": sid,
                    "active_candidate": item["candidate"],
                    "role": item["role"],
                    "selected_nameplate_power_mw": item["product_power_mw"],
                    "planning_load_capacity_mw_after_1p06_derating": item["capacity_mw_loss_derated"],
                    "capacity_margin_after_ac_losses_mw": margin,
                    **ac,
                })
                rows.append({
                    "design_mode": mode, "scenario": scen.name, "service_id": sid,
                    "active_candidate": item["candidate"], "role": item["role"],
                    "converged": ac["converged"], "min_voltage_pu": round(ac["min_voltage_pu"], 8),
                    "min_voltage_bus": ac["min_voltage_bus"],
                    "real_loss_kw": round(1000 * ac["real_loss_mw"], 6),
                    "source_mw": round(ac["source_mw"], 8),
                    "nameplate_margin_after_ac_losses_mw": round(margin, 8),
                    "voltage_within_0p90_1p10": ac["voltage_within_0p90_1p10"],
                    "active_power_nameplate_ok": margin >= -1e-9,
                })
            scenario_rows.append({
                "scenario": scen.name,
                "active_services": [c["service_id"] for c in service_checks],
                "all_ac_checks_pass": all(c["converged"] and c["voltage_within_0p90_1p10"]
                                          and c["capacity_margin_after_ac_losses_mw"] >= -1e-9
                                          for c in service_checks),
                "worst_min_voltage_pu": min((c["min_voltage_pu"] for c in service_checks), default=None),
                "total_real_loss_kw": 1000 * sum(c["real_loss_mw"] for c in service_checks),
                "service_checks": service_checks,
            })
        design_summaries.append({
            "design_mode": mode,
            "all_served_island_ac_checks_pass": all(s["all_ac_checks_pass"] for s in scenario_rows),
            "scenarios": scenario_rows,
        })

    summary = {
        "method": "nonlinear radial backward/forward sweep, balanced constant-PQ case33bw",
        "source": {
            "dataset": "MATPOWER case33bw / Baran-Wu 33-bus public test feeder",
            "url": "https://github.com/MATPOWER/matpower/blob/master/data/case33bw.m",
            "data_note": "Loads and the 32 normally closed radial branches are transcribed above for an offline, dependency-free audit.",
        },
        "limits": ("Classical voltage/loss/capacity screen only; not AC-OPF, inverter reactive-capability, "
                   "protection, frequency/transient, harmonics, SOC chronology, or restoration certification."),
        "voltage_limits_pu": [VMIN_PU, VMAX_PU],
        "grid_connected_base_case": base,
        "standalone_section_results": section_results,
        "design_scenario_results": design_summaries,
    }
    (out_dir / "ac_powerflow_summary.json").write_text(json.dumps(summary, indent=2))
    with (out_dir / "ac_powerflow_summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print(f"grid-connected case: Vmin={base['min_voltage_pu']:.5f} p.u., "
          f"loss={1000*base['real_loss_mw']:.2f} kW")
    for d in design_summaries:
        print(f"{d['design_mode']}: all served-island AC screens pass="
              f"{d['all_served_island_ac_checks_pass']}")
    print(f"wrote {out_dir / 'ac_powerflow_summary.json'}")
    print(f"wrote {out_dir / 'ac_powerflow_summary.csv'}")
    all_pass = (base["converged"] and base["voltage_within_0p90_1p10"]
                and all(d["all_served_island_ac_checks_pass"] for d in design_summaries))
    if not all_pass:
        print("AC screening gate FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
