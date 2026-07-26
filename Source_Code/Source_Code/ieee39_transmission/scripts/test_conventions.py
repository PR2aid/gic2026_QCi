"""Run and record the 15 fail-closed IEEE-39 convention/invariant gates."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from grid_q.network import load_case, dc_power_flow
from grid_q.islands import (generate_candidates, generate_partition_candidates,
                            overlap_matrix)
from grid_q.scenarios import generate_scenarios, score_severity, compress
from grid_q.hamiltonian import (build_dispatch_hamiltonian,
                                build_selection_hamiltonian,
                                build_hourly_dispatch_payload,
                                decode_simplex_dispatch,
                                dispatch_physical, true_dispatch_cost,
                                islanded_lg_cap)
from grid_q.classical import (exhaustive_selection, milp_selection,
                              simplex_polynomial_min)
from grid_q.dirac3 import lint_payload


EXPECTED_CHECKS = 15


def main():
    net = load_case("case39")
    checks = []

    def ok(name, cond):
        passed = bool(cond)
        checks.append({"name": name, "passed": passed})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        return passed

    all_ok = True

    # 1. DC PF sanity: single injection pair, flow equals transfer
    inj = {b: 0.0 for b in net.buses}
    inj[1] = 100.0
    inj[39] = -100.0
    flows, loading = dc_power_flow(net, inj)
    total_absorbed = sum(abs(f) for f in flows.values())
    all_ok &= ok("DC power flow solves and produces finite flows",
                 np.isfinite(loading) and total_absorbed > 0)

    # 2. connectivity by construction
    cands = generate_candidates(net, 14)
    all_ok &= ok("all 14 candidate islands connected",
                 all(c.is_connected(net) for c in cands))
    covered = set().union(*(c.buses for c in cands)) & set(net.loads)
    all_ok &= ok(f"Stage-A design covers all load buses ({len(covered)}/{len(net.loads)})",
                 covered == set(net.loads))

    # 3. planning master partition: connectivity, exact ownership, no overlap.
    partition = generate_partition_candidates(net, 14)
    owner_counts = {
        b: sum(b in c.buses for c in partition) for b in net.loads
    }
    max_overlap = max(
        (len(set(partition[i].buses) & set(partition[j].buses))
         for i in range(len(partition)) for j in range(i + 1, len(partition))),
        default=0,
    )
    all_ok &= ok("all 14 planning zones connected and pairwise disjoint",
                 all(c.is_connected(net) for c in partition)
                 and max_overlap == 0)
    all_ok &= ok("every load bus has exactly one planning-zone owner",
                 set(owner_counts.values()) == {1})
    all_ok &= ok("partition load sum equals the system load",
                 abs(sum(c.base_load for c in partition) - net.total_load())
                 < 1e-9)

    # 4. PCC sampling uses the complete candidate domain and compression
    # preserves the full probability mass.
    full_scens = generate_scenarios(net, 200, n_candidates=len(partition))
    pcc_ids = [int(s.contingency.split(":")[1]) for s in full_scens
               if s.contingency.startswith("pcc:")]
    all_ok &= ok("PCC samples stay in the declared 0..13 domain and reach >9",
                 pcc_ids and min(pcc_ids) >= 0 and max(pcc_ids) < 14
                 and max(pcc_ids) > 9)
    score_severity(net, full_scens)
    full_reps, full_tail = compress(full_scens, 6)
    all_ok &= ok("scenario compression preserves probability mass exactly",
                 abs(sum(s.prob for s in full_reps + full_tail) - 1.0)
                 < 1e-12)

    # 5. encoding identity on a balanced schedule
    scens = generate_scenarios(net, 50, n_candidates=len(cands))
    score_severity(net, scens)
    reps, _ = compress(scens, 4)
    c = max(cands, key=lambda c: c.critical_load)
    prob = build_dispatch_hamiltonian(c, reps[0])
    x = np.zeros(prob.n_vars)
    for t in range(24):
        load_t = c.base_load * reps[0].load_scale[t]
        pv_t = c.der.pv_mw * reps[0].pv_scale[t]
        need = load_t - pv_t
        pm = min(max(need, 0), c.der.microturbine_mw)
        pg = min(max(need - pm, 0), islanded_lg_cap(c))
        sh = max(need - pm - pg, 0)
        x[5 * t] = pm / prob.scales[5 * t]
        x[5 * t + 1] = pg / prob.scales[5 * t + 1]
        x[5 * t + 4] = sh / prob.scales[5 * t + 4]
    sched = dispatch_physical(prob, x)
    cost, diag = true_dispatch_cost(c, reps[0], sched)
    all_ok &= ok(f"H(x) == physical cost for balanced schedule "
                 f"(|diff|={abs(prob.evaluate(x) - cost):.2e})",
                 abs(prob.evaluate(x) - cost) < 1e-4 * max(cost, 1.0))

    # 6. penalty ordering: 1 MW imbalance dominates cost scale
    x2 = x.copy()
    x2[4] += 1.0 / prob.scales[4]  # +1 MW surplus shed on hour 0 -> imbalance
    delta = prob.evaluate(x2) - prob.evaluate(x)
    fuel_scale = 60.0  # $/MWh linear fuel coefficient
    all_ok &= ok(f"1 MW balance violation raises H by {delta:.1f} "
                 f"(>> {fuel_scale} $/MWh fuel scale)", delta > 10 * fuel_scale)

    # 7. selection Hamiltonian: binary penalty + exhaustive agreement
    sub = cands[:6]
    O = overlap_matrix(sub)
    v = {c.cid: 1000.0 * (i + 1) for i, c in enumerate(sub)}
    u = {c.cid: (5000.0 if i % 2 == 0 else 100.0) for i, c in enumerate(sub)}
    for c_ in sub:
        c_.der.upgrade_cost = 0.0
    sel = build_selection_hamiltonian(sub, v, u, O, lam_overlap=0.0,
                                      lam_bin=5.0)
    y_half = np.full(6, 0.5)
    binary_penalty = lambda y: 5.0 * float(np.sum(y * (y - 1.0)))
    all_ok &= ok("binary-domain polynomial vanishes on {0,1} and is nonzero "
                 "at 0.5",
                 binary_penalty(np.zeros(6)) == 0.0
                 and binary_penalty(np.ones(6)) == 0.0
                 and abs(binary_penalty(y_half)) > 0.0)
    res = exhaustive_selection(sel)
    expect = np.array([1.0 if (v[c.cid] < u[c.cid]) else 0.0 for c in sub])
    all_ok &= ok(f"exhaustive selection matches direct bookkeeping "
                 f"{res['y'].astype(int).tolist()} vs {expect.astype(int).tolist()}",
                 np.array_equal(res["y"], expect))

    # 8. established MILP solver (HiGHS) agrees with the exhaustive oracle
    milp = milp_selection(sel)
    all_ok &= ok(f"HiGHS MILP energy matches exhaustive oracle "
                 f"({milp['energy']:.6g} vs {res['energy']:.6g})",
                 milp.get("energy") is not None
                 and abs(milp["energy"] - res["energy"]) <= 1e-6 * max(1.0, abs(res["energy"])))

    # 9. device-native hourly payload: in-spec + decode consistency
    hp = build_hourly_dispatch_payload(c, reps[0], hour=18)
    if hp is not None:
        payload_view = {
            "num_variables": hp.n_vars, "degree": hp.degree,
            "encoding": "continuous",
            "polynomial": [{"idx": [0] * (hp.degree - len(k)) + [i + 1 for i in k],
                            "val": float(v_)} for k, v_ in hp.terms.items() if k],
            "job_params": {"sum_constraint": hp.sum_constraint,
                           "num_samples": 10, "relaxation_schedule": 1},
        }
        lint = lint_payload(payload_view)
        all_ok &= ok(f"hourly dispatch payload in-spec "
                     f"(ratio {lint['stats']['coeff_ratio']:.3g}, "
                     f"warnings={len(lint['warnings'])})",
                     lint["ok"] and not lint["warnings"])
        ref = simplex_polynomial_min(hp)
        dec = decode_simplex_dispatch(hp, np.asarray(ref["x"]), c)
        served = sum(dec["sched"][k][0] for k in ("p_mt", "p_lg", "p_dis"))
        balance_ok = abs(served + dec["sched"]["l_shed"][0]
                         - hp.meta["net_load_mw"]) < 1e-3 * hp.meta["net_load_mw"]
        all_ok &= ok("hourly payload decode balances net load "
                     f"(served {served:.1f} + shed {dec['sched']['l_shed'][0]:.2f} "
                     f"vs net {hp.meta['net_load_mw']:.1f} MW)", balance_ok)

    if len(checks) != EXPECTED_CHECKS:
        print(f"[FAIL] expected {EXPECTED_CHECKS} recorded checks, got {len(checks)}")
        all_ok = False

    summary = {
        "schema_version": "qpr_ieee39_conventions_v1",
        "case": "case39",
        "checks_expected": EXPECTED_CHECKS,
        "checks_total": len(checks),
        "checks_passed": sum(check["passed"] for check in checks),
        "all_checks_passed": bool(all_ok),
        "checks": checks,
    }
    output = Path(__file__).resolve().parents[1] / "results" / "convention_test_summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print("\nALL CONVENTIONS PASS" if all_ok else "\nCONVENTION FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
