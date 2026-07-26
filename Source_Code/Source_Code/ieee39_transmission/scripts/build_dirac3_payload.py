"""Build the Dirac-3 payload ladder; direct component submission is disabled.

Four payload families on the IEEE 39-bus case, all written to
qci/ieee39_flagship/ with a device-spec lint report embedded in each JSON:

  1. selection — islanding selection over the 14 candidate blocks under the
     worst retained scenario: 14 integer variables, rank 2.  It is retained
     for exact-enumeration/HiGHS verification but excluded from live evidence
     because its distinct coefficients do not satisfy analog resolution.
  2. hourly dispatch — one payload per hour of the flagship candidate's
     islanded ride-through window (4 payloads, 4 variables each).  Dirac-3's
     simplex constraint IS the hourly energy balance; calibrated quadratic
     walls encode capacity limits; coefficient ratio ~15-60.  IN-SPEC.
  3. window dispatch — the whole 4 h window on one simplex (13 variables,
     energy-adequacy relaxation, disclosed).  IN-SPEC.
  4. probe — the full 24 h cubic Hamiltonian (121 variables, rank 3).  Its
     penalty-dominated coefficient spread EXCEEDS the device's documented
     ~200:1 guidance; it is submitted only as a coefficient-resolution
     characterisation probe and is labelled as such everywhere.

Every payload embeds a classical optimum of the *identical* device objective
(simplex-constrained CPU minimisation) so that live Dirac-3 samples can be
scored for device quality separately from encoding quality.

The default run writes the exact JSON used by the integrated workflow. Direct
submission from this component script is disabled. Any optional new execution
must use ``run_judge_reproduction.py`` from ``Source_Code/`` and follow the
guarded procedure in the root README.
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np

from grid_q.network import load_case
from grid_q.islands import generate_candidates, overlap_matrix
from grid_q.scenarios import generate_scenarios, score_severity, compress
from grid_q.hamiltonian import (build_dispatch_hamiltonian,
                                build_selection_hamiltonian,
                                build_hourly_dispatch_payload,
                                build_window_dispatch_payload,
                                decode_simplex_dispatch,
                                allocate_bess_budget,
                                true_dispatch_cost, VOLL)
from grid_q.classical import slsqp_dispatch, simplex_polynomial_min
from grid_q.dirac3 import write_dirac3_payload


def attach_classical_reference(poly_path: Path, prob, extra: dict | None = None):
    """Embed the CPU optimum of the identical device objective in the payload."""
    ref = simplex_polynomial_min(prob)
    rec = json.loads(poly_path.read_text())
    rec["classical_reference"] = {
        "method": "SLSQP on the identical simplex-constrained polynomial",
        "energy": ref["energy"],
        "converged": ref["converged"],
        "x_device_units": [round(float(v), 6) for v in ref["x"]],
        **(extra or {}),
    }
    poly_path.write_text(json.dumps(rec))
    return ref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="case39")
    ap.add_argument("--out-dir", default="qci/ieee39_flagship")
    ap.add_argument(
        "--submit",
        action="store_true",
        help="disabled; use Source_Code/run_judge_reproduction.py",
    )
    ap.add_argument("--relaxation-schedule", type=int, default=1)
    ap.add_argument("--num-samples", type=int, default=20)
    ap.add_argument("--skip-probe", action="store_true",
                    help="omit the 121-variable coefficient-resolution probe")
    args = ap.parse_args()
    if args.submit:
        raise SystemExit(
            "direct component submission is disabled; from Source_Code/ use "
            "run_judge_reproduction.py and follow the root README"
        )

    net = load_case(args.case)
    cands = generate_candidates(net, 14)
    scens = generate_scenarios(net, 200, n_candidates=len(cands))
    score_severity(net, scens)
    reps, tail = compress(scens, 6)
    retained = reps + sorted(tail, key=lambda s: (-s.severity, s.sid))
    flagship = max(cands, key=lambda c: c.critical_load)
    stress = max(retained, key=lambda s: s.severity)

    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    # Remove only deterministic build products. Live receipts are stored by
    # the root runner under results/live and are never touched here. This
    # prevents obsolete candidate-specific payloads from surviving a rebuild
    # and being mistaken for registered campaign inputs.
    for pattern in ("*_polynomial.json", "*_summary.json", "payload_manifest.json"):
        for old in out_dir.glob(pattern):
            old.unlink()
    manifest = {"case": args.case, "flagship_cid": flagship.cid,
                "stress_sid": stress.sid, "payloads": []}

    # --- 1. islanding selection (integer, local verification only) ---------
    from scripts.run_end_to_end import (islanded_value, exposure_if_grid_tied,
                                        outage_window)
    v, u = {}, {}
    for c in cands:
        v[c.cid], _, _ = islanded_value(c, stress, method="fast")
        u[c.cid], _, _ = exposure_if_grid_tied(c, stress, len(cands))
    sel = build_selection_hamiltonian(cands, v, u, overlap_matrix(cands))
    p = write_dirac3_payload(sel, out_dir,
                             relaxation_schedule=args.relaxation_schedule,
                             num_samples=args.num_samples)
    print(f"selection payload: {sel.n_vars} vars, degree {sel.degree}, "
          f"{p['n_terms']} terms, ratio {p['lint']['stats']['coeff_ratio']:.3g} "
          f"-> {p['polynomial']}")
    selection_resolution_pass = bool(
        p["lint"]["stats"]["coefficient_resolution_pass"]
    )
    manifest["payloads"].append({
        "family": "selection",
        "in_spec": selection_resolution_pass,
        "role": ("registered_evidence" if selection_resolution_pass
                 else "local_exact_only"),
        "file": p["polynomial"].name,
    })

    # --- 2 + 3. device-native dispatch (hourly + window, in-spec) ----------
    # Classical master allocates the SOC-coupled battery budget across the
    # window hours; each hourly Dirac-3 subproblem is then SOC-feasible by
    # construction (decomposition pattern recommended by the challenge).
    w0 = outage_window(stress)
    alloc = allocate_bess_budget(flagship, stress, w0, hours=4)
    soc = 0.5 * flagship.der.bess_mwh
    for t in range(4):
        hp = build_hourly_dispatch_payload(flagship, stress, w0 + t,
                                           dis_cap_mw=alloc[t])
        if hp is None:
            print(f"hourly payload h{w0+t}: net load <= 0, skipped")
            continue
        p = write_dirac3_payload(hp, out_dir,
                                 relaxation_schedule=args.relaxation_schedule,
                                 num_samples=args.num_samples)
        ref = attach_classical_reference(
            p["polynomial"], hp,
            extra={"bess_master_allocation_mw": alloc[t]})
        dec = decode_simplex_dispatch(hp, np.asarray(ref["x"]), flagship,
                                      soc0_mwh=soc)
        soc = dec["soc_end_mwh"]
        print(f"hourly payload h{w0+t}: {hp.n_vars} vars, ratio "
              f"{p['lint']['stats']['coeff_ratio']:.3g}, CPU-ref energy "
              f"{ref['energy']:.2f}, BESS alloc {alloc[t]:.1f} MW, "
              f"decoded shed {dec['shed_mwh']:.3f} MWh")
        manifest["payloads"].append({
            "family": "hourly_dispatch",
            "in_spec": bool(p["lint"]["stats"]["coefficient_resolution_pass"]),
            "role": "registered_evidence",
            "file": p["polynomial"].name,
        })

    wp = build_window_dispatch_payload(flagship, stress, w0)
    p = write_dirac3_payload(wp, out_dir,
                             relaxation_schedule=args.relaxation_schedule,
                             num_samples=args.num_samples)
    ref = attach_classical_reference(p["polynomial"], wp)
    print(f"window payload: {wp.n_vars} vars, ratio "
          f"{p['lint']['stats']['coeff_ratio']:.3g}, CPU-ref energy "
          f"{ref['energy']:.2f} -> {p['polynomial']}")
    manifest["payloads"].append({
        "family": "window_dispatch",
        "in_spec": bool(p["lint"]["stats"]["coefficient_resolution_pass"]),
        "role": "registered_evidence",
        "file": p["polynomial"].name,
    })

    # --- 4. 24 h cubic coefficient-resolution probe (disclosed) ------------
    if not args.skip_probe:
        disp = build_dispatch_hamiltonian(flagship, stress)
        p = write_dirac3_payload(disp, out_dir,
                                 relaxation_schedule=args.relaxation_schedule,
                                 num_samples=args.num_samples)
        print(f"probe payload: {disp.n_vars} vars, degree {disp.degree}, "
              f"{p['n_terms']} terms — OUTSIDE coefficient-resolution "
              f"guidance, spread {p['lint']['stats']['coeff_ratio']:.3g} "
              "(characterisation probe)")
        manifest["payloads"].append({"family": "probe_24h_cubic",
                                     "in_spec": False,
                                     "role": "characterisation_only",
                                     "file": p["polynomial"].name})

    (out_dir / "payload_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {out_dir / 'payload_manifest.json'}")

if __name__ == "__main__":
    main()
