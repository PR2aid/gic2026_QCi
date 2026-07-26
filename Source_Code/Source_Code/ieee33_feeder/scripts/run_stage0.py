#!/usr/bin/env python3
"""Stage 0 — exact candidate identification for challenge stage 1a.

The challenge asks teams to *analyze* the legacy grid to identify islanding
candidates. This script makes that analysis exact and reproducible instead of
asserted: a dynamic program over ALL connected partitions of the radial feeder
tree computes the minimum-cost sectionalization as a function of a disclosed
islanding reserve margin rho (an island is admissible for a product when
load * (1 + rho) <= loss-derated product capacity).

Outputs (results/stage0_sectionalization/):
  margin_frontier.csv     optimal cost, section count, and structure vs rho
  stage0_summary.json     breakpoints, optimal partitions, and the exact
                          optimality band of the submitted four-section Plan A
It also reports the planner break-even critical-risk valuations at which the
robust plan's black-start additions pay for themselves.

This is classical, exact pre-analysis (the challenge's stage 1a); the Stage-1
Hamiltonian then optimizes product selection on the identified candidates.
"""
from __future__ import annotations

import csv
import json
import sys
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qci_phase3.microgrid import (
    PRIMARY_CANDIDATES as CANDIDATES,
    CONTINGENCIES,
    PRODUCTS,
    OVERLAP_CANDIDATES as ROBUST_OVERLAP_CANDIDATES,
    DESIGN_RISK_VALUE_USD_PER_MWH,
    SWITCH_UPGRADE_COST_USD as SWITCH_UPGRADE_COST,
    BLACKSTART_UPGRADE_COST_USD as BLACKSTART_UPGRADE_COST,
    CRF,
)

# Renumbered feeder tree (load buses 1..32 = case33bw buses 2..33).
# Loads in MW, matching the aggregated section loads used by Stage 1.
LOADS = {
    1: 0.100, 2: 0.090, 3: 0.120, 4: 0.060, 5: 0.060, 6: 0.200, 7: 0.200,
    8: 0.060, 9: 0.060, 10: 0.045, 11: 0.060, 12: 0.060, 13: 0.120,
    14: 0.060, 15: 0.060, 16: 0.060, 17: 0.090,
    18: 0.090, 19: 0.090, 20: 0.090, 21: 0.090,
    22: 0.090, 23: 0.420, 24: 0.420,
    25: 0.060, 26: 0.060, 27: 0.060, 28: 0.120, 29: 0.200, 30: 0.150,
    31: 0.210, 32: 0.060,
}
EDGES = (
    [(i, i + 1) for i in range(1, 17)]            # trunk 1..17
    + [(1, 18), (18, 19), (19, 20), (20, 21)]     # lateral A
    + [(2, 22), (22, 23), (23, 24)]               # lateral B (critical)
    + [(5, 25)] + [(i, i + 1) for i in range(25, 32)]  # lateral C (critical)
)
SCALE = 200  # 5 kW integer load units for exact DP states

SUBMITTED_SECTIONS = {
    "trunk_1_17": tuple(range(1, 18)),
    "lateral_18_21": (18, 19, 20, 21),
    "lateral_22_24": (22, 23, 24),
    "lateral_25_32": tuple(range(25, 33)),
}


def check_consistency() -> None:
    sums = {name: round(sum(LOADS[b] for b in buses), 6) for name, buses in SUBMITTED_SECTIONS.items()}
    expected = {c.name: c.load_mw for c in CANDIDATES}
    pairs = {
        "trunk_1_17": "MG_trunk_1_17", "lateral_18_21": "MG_lateral_18_21",
        "lateral_22_24": "MG_lateral_22_24", "lateral_25_32": "MG_lateral_25_32",
    }
    for k, cname in pairs.items():
        assert abs(sums[k] - expected[cname]) < 1e-9, (k, sums[k], expected[cname])


def build_children() -> dict[int, list[int]]:
    adj: dict[int, list[int]] = {}
    for a, b in EDGES:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    children: dict[int, list[int]] = {n: [] for n in LOADS}
    seen = {1}
    stack = [1]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                children[u].append(v)
                stack.append(v)
    return children


def part_cost(load_units: int, rho: float) -> float:
    """Cheapest upfront cost (product + one switch) admissible for the part."""
    load = load_units / SCALE
    best = float("inf")
    for p in PRODUCTS:
        if load * (1.0 + rho) <= p.island_capacity_mw + 1e-12:
            best = min(best, p.capex_usd + SWITCH_UPGRADE_COST)
    return best


def optimal_partition(rho: float) -> dict:
    """Exact min-cost connected partition via tree DP.

    State: after processing node u's subtree, a map {open-part load -> (cost of
    all closed parts, choice trace)} where the open part still contains u.
    For each child edge: either cut (close the child's open part, paying its
    cheapest product) or keep (merge child's open load into ours).
    """
    children = build_children()
    max_units = 0
    for p in PRODUCTS:
        max_units = max(max_units, int((p.island_capacity_mw / (1.0 + rho)) * SCALE + 1e-9))

    def solve(u: int) -> dict[int, tuple[float, tuple]]:
        base_units = int(round(LOADS[u] * SCALE))
        states: dict[int, tuple[float, tuple]] = {base_units: (0.0, ())}
        for v in children[u]:
            child_states = solve(v)
            merged: dict[int, tuple[float, tuple]] = {}
            for cu_load, (cu_cost, cu_trace) in states.items():
                for cv_load, (cv_cost, cv_trace) in child_states.items():
                    # Option 1: cut edge (u, v) — close the child's open part.
                    close = part_cost(cv_load, rho)
                    if close < float("inf"):
                        key = cu_load
                        cost = cu_cost + cv_cost + close
                        trace = cu_trace + (("cut", v, cv_load, cv_trace),)
                        if key not in merged or cost < merged[key][0]:
                            merged[key] = (cost, trace)
                    # Option 2: keep the edge — merge child's open part.
                    key = cu_load + cv_load
                    if key <= max_units:
                        cost = cu_cost + cv_cost
                        trace = cu_trace + (("keep", v, cv_load, cv_trace),)
                        if key not in merged or cost < merged[key][0]:
                            merged[key] = (cost, trace)
            states = merged
            if not states:
                return {}
        return states

    root_states = solve(1)
    best_total = float("inf")
    best = None
    for load_units, (cost, trace) in root_states.items():
        close = part_cost(load_units, rho)
        total = cost + close
        if total < best_total:
            best_total = total
            best = (load_units, trace)
    if best is None:
        return {"rho": rho, "feasible": False}

    # Reconstruct sections from the trace.
    sections: list[list[int]] = []

    def collect(u: int, trace: tuple, current: list[int]) -> None:
        current.append(u)
        for action, v, _load, sub in trace:
            if action == "keep":
                collect(v, sub, current)
            else:
                part: list[int] = []
                collect(v, sub, part)
                sections.append(part)

    root_part: list[int] = []
    collect(1, best[1], root_part)
    sections.append(root_part)
    sections = [sorted(s) for s in sections]
    sections.sort(key=lambda s: s[0])
    section_loads = [round(sum(LOADS[b] for b in s), 6) for s in sections]
    return {
        "rho": rho,
        "feasible": True,
        "optimal_upfront_cost_usd": round(best_total, 2),
        "optimal_annualized_cost_usd_yr": round(CRF * best_total, 2),
        "num_sections": len(sections),
        "sections": sections,
        "section_loads_mw": section_loads,
        "min_headroom_fraction": round(min(
            min(p.island_capacity_mw for p in PRODUCTS
                if load * (1.0 + rho) <= p.island_capacity_mw + 1e-12) / load - 1.0
            for load in section_loads), 6),
    }


def policy_optimum(rho: float) -> dict:
    """Optimal sectionalization under the two disclosed operational policies.

    Policy P1 (dedicated critical islands): each critical lateral (B: 22-24,
    C: 25-32) is its own island, so critical service never competes with firm
    trunk load inside one islanded balance and black-start overlays attach to a
    dedicated pocket.  Policy P2 (lateral-head switching): sectionalizing
    switches sit only at the three lateral tie points, the standard recloser
    locations; no mid-trunk sectionalizing.  Under P1+P2 the only freedom is
    whether lateral A merges with the trunk.
    """
    blocks = {
        "T": [n for n in range(1, 18)],
        "A": [18, 19, 20, 21],
        "B": [22, 23, 24],
        "C": list(range(25, 33)),
    }
    def cost_of(parts: list[list[int]]) -> float:
        total = 0.0
        for part in parts:
            c = part_cost(int(round(sum(LOADS[b] for b in part) * SCALE)), rho)
            if c == float("inf"):
                return float("inf")
            total += c
        return total
    options = {
        "four_sections_T_A_B_C": [blocks["T"], blocks["A"], blocks["B"], blocks["C"]],
        "three_sections_TA_B_C": [blocks["T"] + blocks["A"], blocks["B"], blocks["C"]],
    }
    best_name, best_cost, best_parts = None, float("inf"), None
    for name, parts in options.items():
        c = cost_of(parts)
        if c < best_cost:
            best_name, best_cost, best_parts = name, c, parts
    if best_parts is None:
        return {"rho": rho, "feasible": False}
    loads = [round(sum(LOADS[b] for b in p), 6) for p in best_parts]
    return {
        "rho": rho,
        "feasible": True,
        "policy_optimal_structure": best_name,
        "policy_optimal_upfront_cost_usd": round(best_cost, 2),
        "policy_num_sections": len(best_parts),
        "policy_section_loads_mw": loads,
    }


def breakeven_thresholds() -> list[dict]:
    """Critical-risk valuation at which each black-start addition pays off."""
    out = []
    for backup in ROBUST_OVERLAP_CANDIDATES:
        product = min((p for p in PRODUCTS if p.island_capacity_mw >= backup.load_mw),
                      key=lambda p: p.capex_usd)
        annual_cost = CRF * (product.capex_usd + BLACKSTART_UPGRADE_COST)
        scenarios = [s for s in CONTINGENCIES
                     if any(c.service_id == backup.service_id
                            and s.name in c.unavailable_in_contingencies for c in CANDIDATES)]
        total_mwh_at_risk = sum(backup.critical_load_mw * s.duration_h
                                * s.event_rate_per_year for s in scenarios)
        threshold = annual_cost / total_mwh_at_risk
        out.append({
            "backup": backup.name,
            "service_id": backup.service_id,
            "annualized_cost_usd_yr": round(annual_cost, 2),
            "expected_critical_mwh_at_risk_per_year": round(total_mwh_at_risk, 6),
            "breakeven_risk_value_usd_per_mwh": round(threshold, 2),
            "selected_in_balanced_plan_beta_30000": threshold < DESIGN_RISK_VALUE_USD_PER_MWH["balanced_critical"],
            "selected_in_robust_plan_beta_75000": threshold < DESIGN_RISK_VALUE_USD_PER_MWH["robust_critical"],
        })
    return out


def main() -> int:
    check_consistency()
    out_dir = REPO / "results" / "stage0_sectionalization"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for i in range(0, 1201):  # rho from 0% to 12.00% in 0.01% steps
        rho = i / 10_000.0
        res = optimal_partition(rho)
        res.update(policy_optimum(rho))
        rows.append(res)

    # Collapse to cost plateaus (breakpoints of both margin frontiers).
    plateaus = []
    for res in rows:
        key = (res.get("optimal_upfront_cost_usd"), res.get("num_sections"),
               res.get("policy_optimal_upfront_cost_usd"), res.get("policy_optimal_structure"))
        if not plateaus or plateaus[-1]["_key"] != key:
            plateaus.append({
                "_key": key,
                "rho_from": res["rho"],
                "rho_to": res["rho"],
                "unconstrained_optimal_upfront_cost_usd": res.get("optimal_upfront_cost_usd"),
                "unconstrained_num_sections": res.get("num_sections"),
                "unconstrained_sections": res.get("sections"),
                "unconstrained_section_loads_mw": res.get("section_loads_mw"),
                "unconstrained_min_headroom_fraction": res.get("min_headroom_fraction"),
                "policy_optimal_upfront_cost_usd": res.get("policy_optimal_upfront_cost_usd"),
                "policy_optimal_structure": res.get("policy_optimal_structure"),
                "policy_num_sections": res.get("policy_num_sections"),
                "price_of_policy_usd": (round(res["policy_optimal_upfront_cost_usd"]
                                              - res["optimal_upfront_cost_usd"], 2)
                                        if res.get("feasible") else None),
                "feasible": res.get("feasible"),
            })
        else:
            plateaus[-1]["rho_to"] = res["rho"]
    for p in plateaus:
        p.pop("_key", None)

    submitted_sections = sorted([sorted(b) for b in SUBMITTED_SECTIONS.values()], key=lambda s: s[0])
    submitted_band = None
    for p in plateaus:
        if (p["policy_optimal_structure"] == "four_sections_T_A_B_C"
                and abs((p["policy_optimal_upfront_cost_usd"] or 0) - 13_420_000.0) < 1.0):
            if submitted_band is None:
                submitted_band = [p["rho_from"], p["rho_to"]]
            else:
                submitted_band[1] = p["rho_to"]

    with (out_dir / "margin_frontier.csv").open("w", newline="") as f:
        fieldnames = ["rho_from", "rho_to",
                      "unconstrained_optimal_upfront_cost_usd", "unconstrained_num_sections",
                      "unconstrained_min_headroom_fraction", "unconstrained_section_loads_mw",
                      "policy_optimal_upfront_cost_usd", "policy_optimal_structure",
                      "policy_num_sections", "price_of_policy_usd"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for p in plateaus:
            w.writerow({k: (json.dumps(p[k]) if isinstance(p[k], list) else p[k]) for k in fieldnames})

    summary = {
        "method": ("exact dynamic program over all connected partitions of the 32-load-bus radial "
                   "feeder tree; admissibility: section load * (1 + rho) <= loss-derated product "
                   "capacity; cost: cheapest admissible product + one switching upgrade per section. "
                   "The policy frontier additionally enforces two disclosed operational policies: "
                   "P1 dedicated critical islands (critical laterals B and C are their own islands) "
                   "and P2 lateral-head switching only (standard recloser locations, no mid-trunk "
                   "sectionalizing)."),
        "rho_grid": "0 to 12.00% in 0.01 percentage-point steps",
        "plateaus": plateaus,
        "zero_margin_unconstrained_optimum": {
            "upfront_cost_usd": plateaus[0]["unconstrained_optimal_upfront_cost_usd"],
            "num_sections": plateaus[0]["unconstrained_num_sections"],
            "sections": plateaus[0]["unconstrained_sections"],
            "min_headroom_fraction": plateaus[0]["unconstrained_min_headroom_fraction"],
        },
        "submitted_plan_A": {
            "sections": submitted_sections,
            "upfront_cost_usd": 13_420_000.0,
            "policy_optimality_band_rho": submitted_band,
            "analytic_policy_optimality_interval_rho": {
                "lower_open": PRODUCTS[1].island_capacity_mw / (1.505 + 0.360) - 1.0,
                "upper_closed": PRODUCTS[0].island_capacity_mw / 0.930 - 1.0,
                "interval_notation": "(lower_open, upper_closed]",
            },
            "policies": ["P1 dedicated critical islands", "P2 lateral-head switching only"],
        },
        "breakeven_critical_risk_valuations": breakeven_thresholds(),
        "note": ("Stage-0 is classical, exact pre-analysis for challenge stage 1a. The Stage-1 "
                 "Hamiltonian optimizes product selection on the identified candidates. The "
                 "unconstrained frontier is a lower bound that prices the operational policies; "
                 "it is not a recommended build plan."),
    }
    (out_dir / "stage0_summary.json").write_text(json.dumps(summary, indent=2))

    p0 = plateaus[0]
    print(f"zero-margin unconstrained optimum: USD {p0['unconstrained_optimal_upfront_cost_usd']:,.0f} "
          f"({p0['unconstrained_num_sections']} sections, min headroom "
          f"{100*p0['unconstrained_min_headroom_fraction']:.2f}%)")
    for p in plateaus[:8]:
        print(f"  rho {100*p['rho_from']:.2f}%-{100*p['rho_to']:.2f}%: unconstrained "
              f"USD {p['unconstrained_optimal_upfront_cost_usd']:,.0f} "
              f"({p['unconstrained_num_sections']} sec) | policy "
              f"USD {p['policy_optimal_upfront_cost_usd']:,.0f} ({p['policy_optimal_structure']}) "
              f"| price of policy USD {p['price_of_policy_usd']:,.0f}")
    print(f"submitted 4-section Plan A policy-optimality band (rho): {submitted_band}")
    for t in summary["breakeven_critical_risk_valuations"]:
        print(f"  breakeven {t['service_id']}: USD "
              f"{t['breakeven_risk_value_usd_per_mwh']:,.0f}/MWh")
    print(f"wrote {out_dir / 'margin_frontier.csv'}")
    print(f"wrote {out_dir / 'stage0_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
