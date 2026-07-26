"""End-to-end Phase 3 pipeline on the IEEE 39-bus case.

Stage A: a deterministic connected partition assigns every bus to exactly one
         candidate microgrid; no customer, load, or upgrade cost can overlap.
Stage B: 200 public scenarios -> regime representatives plus the complete
         CVaR tail, preserving probability mass exactly.
Stage C: one here-and-now stochastic master selects a fixed upgrade portfolio
         from expected scenario costs.  Exact enumeration, HiGHS MILP, and
         seeded simulated annealing solve the identical binary polynomial.
Stage D: the frozen portfolio is evaluated without redesign in every retained
         scenario.  Separately, the original overlapping candidate pool is
         retained only for the immutable Dirac-3 dispatch benchmark ladder.

Usage:  python scripts/run_end_to_end.py [--case case39] [--candidates 14]
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np

from grid_q.network import load_case
from grid_q.islands import (generate_candidates, generate_partition_candidates,
                            overlap_matrix)
from grid_q.scenarios import generate_scenarios, score_severity, compress
from grid_q.hamiltonian import (GRID_PRICE, VOLL, VOLL_CRITICAL,
                                build_dispatch_hamiltonian,
                                build_selection_hamiltonian,
                                dispatch_physical, true_dispatch_cost)
from grid_q.classical import (slsqp_dispatch, fast_dispatch, polyproblem_relaxation,
                              exhaustive_selection, anneal_selection,
                              milp_selection)
from grid_q.metrics import aggregate

OUTAGE_HOURS = 4  # duration a severed candidate stays unsupplied if not islanded


def outage_window(scen, hours=OUTAGE_HOURS):
    """Peak-net-load-aligned outage window (worst case for the candidate)."""
    net_load = scen.load_scale - 0.3 * scen.pv_scale
    rolling = np.convolve(net_load, np.ones(hours), mode="valid")
    return int(np.argmax(rolling))


def islanded_value(cand, scen, method="fast"):
    """Cost of riding through the outage window islanded.

    `method=fast` uses deterministic merit-order dispatch for reproducible
    scenario sweeps; `method=slsqp` uses the slower continuous optimizer.
    """
    w0 = outage_window(scen)
    if method == "slsqp":
        d = slsqp_dispatch(cand, scen, hours=OUTAGE_HOURS, window_start=w0,
                           voll_override=VOLL, cyclic_soc=False)
    else:
        d = fast_dispatch(cand, scen, hours=OUTAGE_HOURS, window_start=w0,
                          voll_override=VOLL)
    other_hours = np.concatenate([scen.load_scale[:w0],
                                  scen.load_scale[w0 + OUTAGE_HOURS:]])
    grid_rest = GRID_PRICE * cand.base_load * float(np.sum(other_hours))
    return d["cost"] + grid_rest, d, w0


def exposure_if_grid_tied(cand, scen, n_cands: int = 14):
    """Weighted unserved-load exposure of candidate `cand` under `scen` if it
    does NOT island, plus its 24 h grid-purchase cost if supply survives.

    Returns (total_cost, hit, voll_component).  Scenario generation draws PCC
    identifiers from the explicitly supplied candidate domain.  This decoder
    rejects an out-of-domain identifier rather than remapping it.
    """
    crit_frac = cand.critical_load / max(cand.base_load, 1e-6)
    voll_eff = crit_frac * VOLL_CRITICAL + (1 - crit_frac) * VOLL
    kind, _, arg = scen.contingency.partition(":")
    energy_24h = cand.base_load * float(np.sum(scen.load_scale))
    grid_cost = GRID_PRICE * energy_24h
    if kind == "pcc" and not 0 <= int(arg) < n_cands:
        raise ValueError(
            f"PCC identifier {arg} is outside candidate domain 0..{n_cands - 1}"
        )
    if kind == "pcc" and int(arg) == cand.cid:
        voll_part = voll_eff * cand.base_load * OUTAGE_HOURS
        return voll_part + grid_cost, True, voll_part
    if kind == "line" and int(arg) in cand.pcc_lines:
        voll_part = 0.5 * voll_eff * cand.base_load * OUTAGE_HOURS
        return voll_part + grid_cost, True, voll_part
    if kind == "gen":
        return grid_cost, False, 0.0
    return grid_cost, False, 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="case39")
    ap.add_argument("--candidates", type=int, default=14)
    ap.add_argument("--scenarios", type=int, default=200)
    ap.add_argument("--regimes", type=int, default=6)
    ap.add_argument("--relax-restarts", type=int, default=0,
                    help="extra random starts for the exact-polynomial CPU relaxation; 0 keeps the audit fast")
    ap.add_argument("--landscape-check", action="store_true",
                    help="run the optional 121-variable CPU landscape check (slow on some CPUs)")
    ap.add_argument("--dispatch-eval", choices=["fast", "slsqp"], default="fast",
                    help="dispatch evaluator for scenario bookkeeping")
    args = ap.parse_args()

    t0 = time.time()
    net = load_case(args.case)
    cands = generate_partition_candidates(net, args.candidates)
    O = overlap_matrix(cands)
    covered_load_buses = set().union(*(c.buses for c in cands)) & set(net.loads)
    coverage_ratio = len(covered_load_buses) / max(1, len(net.loads))
    ownership = {
        b: sum(b in c.buses for c in cands) for b in net.loads
    }
    pairwise_overlaps = [
        len(set(cands[i].buses) & set(cands[j].buses))
        for i in range(len(cands)) for j in range(i + 1, len(cands))
    ]
    total_load_mw = net.total_load()
    candidate_load_sum_mw = sum(c.base_load for c in cands)
    partition_ok = (
        all(count == 1 for count in ownership.values())
        and all(c.is_connected(net) for c in cands)
        and max(pairwise_overlaps, default=0) == 0
        and np.isclose(candidate_load_sum_mw, total_load_mw,
                       rtol=0.0, atol=1e-9)
    )
    if not partition_ok:
        raise RuntimeError("connected-partition invariants failed")

    scens = generate_scenarios(
        net, args.scenarios, n_candidates=len(cands)
    )
    score_severity(net, scens)
    reps, tail = compress(scens, args.regimes)
    tail = sorted(tail, key=lambda s: (-s.severity, s.sid))
    retained = reps + tail
    probability_sum = sum(s.prob for s in retained)
    if not np.isclose(probability_sum, 1.0, rtol=0.0, atol=1e-12):
        raise RuntimeError(
            f"retained scenario probability is {probability_sum}, not one"
        )
    pcc_ids = [
        int(s.contingency.split(":", 1)[1])
        for s in scens if s.contingency.startswith("pcc:")
    ]
    if any(not 0 <= cid < len(cands) for cid in pcc_ids):
        raise RuntimeError("scenario generator emitted an invalid PCC identifier")

    print(f"[{args.case}] {len(cands)} disjoint connected candidates | "
          f"load-bus ownership "
          f"{len(covered_load_buses)}/{len(net.loads)} ({coverage_ratio:.1%}) | "
          f"{len(reps)} regimes + all {len(tail)} CVaR-tail scenarios retained "
          f"(probability {probability_sum:.6f})")

    n_cands = len(cands)
    scenario_data = []
    for scen in retained:
        v, u, affected = {}, {}, set()
        dispatch_cache = {}
        for c in cands:
            val, d, w0 = islanded_value(c, scen, method=args.dispatch_eval)
            v[c.cid] = val
            dispatch_cache[c.cid] = (d, w0)
            u[c.cid], hit, _ = exposure_if_grid_tied(c, scen, n_cands)
            if hit:
                affected.add(c.cid)
        # An installed microgrid islands only when its own boundary is hit.
        # Otherwise it remains grid connected and has the same operating cost
        # as the unselected state.
        selected_cost = {
            c.cid: (v[c.cid] if c.cid in affected else u[c.cid])
            for c in cands
        }
        scenario_data.append({
            "scen": scen, "islanded_cost": v, "grid_tied_cost": u,
            "selected_cost": selected_cost, "affected": affected,
            "dispatch_cache": dispatch_cache,
        })

    # One two-stage stochastic master: y is chosen once using expected costs,
    # then frozen for every scenario.  The zero-overlap matrix is retained in
    # the polynomial call as an independently checked structural invariant.
    master_selected = {
        c.cid: sum(
            row["scen"].prob * row["selected_cost"][c.cid]
            for row in scenario_data
        )
        for c in cands
    }
    master_unselected = {
        c.cid: sum(
            row["scen"].prob * row["grid_tied_cost"][c.cid]
            for row in scenario_data
        )
        for c in cands
    }
    sel = build_selection_hamiltonian(
        cands, master_selected, master_unselected, O
    )
    sel.meta.update({
        "decision_timing": "here_and_now_before_scenario",
        "scenario_probability_sum": probability_sum,
        "partition_disjoint": True,
    })
    exact = exhaustive_selection(sel)
    sa = anneal_selection(sel, seed=1701)
    milp = milp_selection(sel)
    sa_gap = (
        (sa["energy"] - exact["energy"])
        / max(abs(exact["energy"]), 1e-9)
    )
    milp_gap = (
        (milp["energy"] - exact["energy"])
        / max(abs(exact["energy"]), 1e-9)
        if milp.get("energy") is not None else None
    )
    chosen_ids = {
        c.cid for c, value in zip(cands, exact["y"]) if value > 0.5
    }
    portfolio = sorted(chosen_ids)
    portfolio_upgrade = sum(
        c.der.upgrade_cost for c in cands if c.cid in chosen_ids
    )
    master_operating_cost = sum(
        master_selected[c.cid] if c.cid in chosen_ids
        else master_unselected[c.cid]
        for c in cands
    )
    master_daily_equivalent = master_operating_cost + portfolio_upgrade / 365.0
    milp_txt = f"{milp_gap:+.2%}" if milp_gap is not None else "n/a"
    print(
        f"fixed stochastic master selected {portfolio}; "
        f"SA gap {sa_gap:+.2%}; HiGHS gap {milp_txt}"
    )

    per_scenario, gap_records = [], []
    for row in scenario_data:
        scen = row["scen"]
        v = row["islanded_cost"]
        u = row["grid_tied_cost"]
        affected = row["affected"]
        dispatch_cache = row["dispatch_cache"]
        op_cost, upgrade, unserved = 0.0, 0.0, np.zeros(24)
        unserved_load_mw = np.zeros(24)
        voll_cost = 0.0
        crit_hours = 0.0
        for c in cands:
            if c.cid in chosen_ids and c.cid in affected:
                op_cost += v[c.cid]
                d, w0 = dispatch_cache[c.cid]
                voll_cost += VOLL * float(np.sum(d["sched"]["l_shed"]))
                frac = np.zeros(24)
                frac[w0:w0 + OUTAGE_HOURS] = d["diag"]["unserved_frac"]
                # non-critical shed only: critical load covered by siting rule
                noncrit = 1.0 - c.critical_load / max(c.base_load, 1e-6)
                capped = np.minimum(frac, noncrit)
                unserved = np.maximum(unserved, capped)
                unserved_load_mw += capped * c.base_load
                crit_hours += float(np.sum(frac > noncrit + 1e-6)) * (
                    c.critical_load > 0)
            else:
                op_cost += u[c.cid]
                hit = c.cid in affected
                if hit and c.cid not in chosen_ids:
                    _, _, voll_part = exposure_if_grid_tied(
                        c, scen, n_cands
                    )
                    voll_cost += voll_part
                    w0 = outage_window(scen)
                    frac = np.zeros(24)
                    frac[w0:w0 + OUTAGE_HOURS] = 1.0
                    unserved = np.maximum(unserved, frac)
                    unserved_load_mw += frac * c.base_load
                    if c.critical_load > 0:
                        crit_hours += OUTAGE_HOURS
        per_scenario.append({
            "sid": scen.sid, "tags": scen.tags, "prob": scen.prob,
            "contingency": scen.contingency,
            "n_selected": len(chosen_ids), "selected": portfolio,
            "affected": sorted(affected),
            "operating_cost": op_cost, "upgrade_cost": portfolio_upgrade,
            "voll_cost": voll_cost,
            "unserved_frac": unserved,
            "unserved_load_frac": np.minimum(unserved_load_mw / total_load_mw, 1.0),
            "critical_outage_hours": crit_hours,
            "selection_sa_gap": sa_gap,
            "selection_milp_gap": milp_gap,
            "selection_milp_wall_s": milp.get("wall_s"),
        })
        print(f"  scen {scen.sid:>3} [{scen.contingency:>9}] "
              f"fixed {portfolio} affected {sorted(affected)}")

    # no-islanding counterfactual
    no_island = []
    for row in scenario_data:
        scen = row["scen"]
        cost, unserved, crit = 0.0, np.zeros(24), 0.0
        unserved_load_mw = np.zeros(24)
        voll_cost = 0.0
        for c in cands:
            gc = row["grid_tied_cost"][c.cid]
            hit = c.cid in row["affected"]
            _, _, voll_part = exposure_if_grid_tied(
                c, scen, n_cands
            )
            cost += gc
            voll_cost += voll_part
            if hit:
                w0 = outage_window(scen)
                frac = np.zeros(24)
                frac[w0:w0 + OUTAGE_HOURS] = 1.0
                unserved = np.maximum(unserved, frac)
                unserved_load_mw += frac * c.base_load
                if c.critical_load > 0:
                    crit += OUTAGE_HOURS
        no_island.append({"prob": scen.prob, "operating_cost": cost,
                          "upgrade_cost": 0.0, "unserved_frac": unserved,
                          "unserved_load_frac": np.minimum(
                              unserved_load_mw / total_load_mw, 1.0),
                          "voll_cost": voll_cost,
                          "critical_outage_hours": crit})

    # Dirac-3 dispatch-polynomial landscape check on the flagship instance.
    # This is a CPU encoding check only; the live Dirac-3 sampler replaces it
    # on qBraid. The default uses only the deterministic feasible initializer
    # so the audit remains quick.
    benchmark_cands = generate_candidates(net, args.candidates)
    flagship = max(benchmark_cands, key=lambda c: c.critical_load)
    stress = max(retained, key=lambda s: s.severity)
    if args.landscape_check:
        from grid_q.hamiltonian import islanded_lg_cap
        prob = build_dispatch_hamiltonian(flagship, stress)
        x_feasible = np.zeros(prob.n_vars)
        for t in range(24):
            load_t = flagship.base_load * stress.load_scale[t]
            pv_t = flagship.der.pv_mw * stress.pv_scale[t]
            need = load_t - pv_t
            pm = min(max(need, 0), flagship.der.microturbine_mw)
            pg = min(max(need - pm, 0), islanded_lg_cap(flagship))
            sh = max(need - pm - pg, 0)
            x_feasible[5 * t] = pm / prob.scales[5 * t]
            x_feasible[5 * t + 1] = pg / prob.scales[5 * t + 1]
            x_feasible[5 * t + 4] = sh / prob.scales[5 * t + 4]
        relax = polyproblem_relaxation(prob, restarts=args.relax_restarts,
                                       x0_feasible=x_feasible)
        sched = dispatch_physical(prob, relax["x"])
        relax_cost, relax_diag = true_dispatch_cost(flagship, stress, sched)
        truth = slsqp_dispatch(flagship, stress)
        gap = (relax_cost - truth["cost"]) / max(truth["cost"], 1e-9)
        gap_records.append({
            "flagship_cid": flagship.cid, "scenario_sid": stress.sid,
            "n_vars": prob.n_vars, "degree": prob.degree,
            "n_terms": len(prob.terms),
            "slsqp_cost": truth["cost"], "slsqp_converged": truth["converged"],
            "classical_truth_method": truth.get("method"),
            "poly_relaxation_cost": relax_cost,
            "relative_gap": gap,
            "poly_imbalance_max_mw": relax_diag["imbalance_max_mw"],
            "poly_soc_violation_mwh": relax_diag["soc_violation_mwh"],
            "relax_restarts": args.relax_restarts,
        })
        print(f"\nflagship dispatch (cand {flagship.cid}, scen {stress.sid}): "
              f"SLSQP ${truth['cost']:.0f} vs poly-relaxation ${relax_cost:.0f} "
              f"(gap {gap:+.2%}, imbalance {relax_diag['imbalance_max_mw']:.3f} MW, "
              f"SLSQP converged={truth['converged']})")
    else:
        gap_records.append({"flagship_cid": flagship.cid,
                            "scenario_sid": stress.sid,
                            "landscape_check": "skipped"})

    metrics_islanding = aggregate(per_scenario)
    metrics_islanding["portfolio_candidates"] = portfolio
    metrics_no = aggregate(no_island)
    selected_load_buses = set().union(
        *(set(c.buses) & set(net.loads)
          for c in cands if c.cid in chosen_ids)
    ) if chosen_ids else set()
    selected_load_mw = sum(
        c.base_load for c in cands if c.cid in chosen_ids
    )
    metrics_islanding["portfolio_load_bus_coverage_ratio"] = (
        len(selected_load_buses) / len(net.loads)
    )
    metrics_islanding["portfolio_load_mw_coverage_ratio"] = (
        selected_load_mw / total_load_mw
    )
    annual_no = metrics_no["expected_operating_cost_$"] * 365.0
    annual_with = (
        metrics_islanding["expected_operating_cost_$"] * 365.0
        + portfolio_upgrade
    )
    metrics_no["expected_total_annual_cost_$"] = round(annual_no, 2)
    metrics_islanding["expected_total_annual_cost_$"] = round(annual_with, 2)
    metrics_islanding["expected_total_annual_saving_$"] = round(
        annual_no - annual_with, 2
    )
    metrics_islanding["expected_total_annual_saving_fraction"] = (
        (annual_no - annual_with) / annual_no
    )
    if any(r["selected"] != portfolio for r in per_scenario):
        raise RuntimeError("portfolio changed during scenario evaluation")
    out = {
        "case": args.case,
        "n_candidates": len(cands),
        "planning_model": "fixed two-stage stochastic master on connected partition",
        "load_bus_coverage_ratio": coverage_ratio,
        "n_load_buses": len(net.loads),
        "n_covered_load_buses": len(covered_load_buses),
        "load_assignment_counts": {str(k): v for k, v in ownership.items()},
        "candidate_load_sum_mw": candidate_load_sum_mw,
        "system_load_mw": total_load_mw,
        "max_pairwise_overlap_buses": max(pairwise_overlaps, default=0),
        "partition_invariants_pass": bool(partition_ok),
        "pcc_candidate_domain": [0, len(cands) - 1],
        "pcc_identifiers_observed": sorted(set(pcc_ids)),
        "n_scenarios_generated": args.scenarios,
        "n_scenarios_retained": len(retained),
        "n_regime_representatives": len(reps),
        "n_cvar_tail_retained": len(tail),
        "retained_probability_sum": probability_sum,
        "metrics_with_islanding": metrics_islanding,
        "metrics_no_islanding": metrics_no,
        "selection_sa_gap": sa_gap,
        "selection_milp_gap": milp_gap,
        "selection_milp_wall_s": milp.get("wall_s"),
        "master_fixed_portfolio": portfolio,
        "master_operating_cost_24h_expected_$": master_operating_cost,
        "master_objective_24h_equivalent_$": master_daily_equivalent,
        "master_frozen_across_scenarios": True,
        "cost_semantics": ("operating_cost = expected 24h energy cost "
                           "(grid purchases / islanded fuel+BESS) PLUS the "
                           "reliability cost of unserved energy at VoLL; "
                           "voll_cost isolates the VoLL component; total annual "
                           "cost annualises the 24h expectation and adds the "
                           "annualised fixed-portfolio upgrade"),
        "scenario_dispatch_eval_method": args.dispatch_eval,
        "dispatch_polynomial_check": gap_records[0],
        "partition_candidates": [
            {
                "cid": c.cid,
                "buses": c.buses,
                "load_buses": [b for b in c.buses if b in net.loads],
                "base_load_mw": c.base_load,
                "annualised_upgrade_cost_$": c.der.upgrade_cost,
            }
            for c in cands
        ],
        "wall_seconds": round(time.time() - t0, 1),
        "per_scenario": [
            {k: (vv.tolist() if isinstance(vv, np.ndarray) else vv)
             for k, vv in r.items()} for r in per_scenario],
    }
    res_path = REPO / "results" / f"end_to_end_{args.case}.json"
    res_path.write_text(json.dumps(out, indent=2))
    print(f"\nWITH islanding: {metrics_islanding}")
    print(f"NO  islanding: {metrics_no}")
    print(f"wrote {res_path} in {out['wall_seconds']}s")


if __name__ == "__main__":
    main()
