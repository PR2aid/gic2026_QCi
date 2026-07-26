"""Dirac-3 payload construction and (guarded) submission via qci-client.

Polynomial terms are serialised as {"idx": [i, j, ...], "val": coeff} with
1-indexed variables, alongside the device job parameters
(sum_constraint, relaxation_schedule, num_samples, solution_precision).

Dry-run always writes the exact JSON that would be uploaded.  Submission
requires QCI_API_URL / QCI_TOKEN in the environment (never stored in the
repository) and is intended to be executed on qBraid.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .hamiltonian import PolyProblem

# Documented Dirac-3 device envelope (QCi Dirac-3 User Guide + beginner guide):
# max variables by polynomial degree, coefficient-resolution guidance, simplex
# budget range, and samples per job.  The linter below distinguishes hard API
# limits from analog-resolution guidance: QCi may accept a poorly conditioned
# objective, but that does not make it admissible registered evidence.
DEVICE_MAX_VARS_BY_DEGREE = {1: 949, 2: 949, 3: 135, 4: 39, 5: 19}
DEVICE_DYNAMIC_RANGE = 200.0
DEVICE_SUM_CONSTRAINT_RANGE = (1.0, 10_000.0)
DEVICE_MAX_SAMPLES = 100
DEVICE_MAX_TOTAL_LEVELS = 949


def lint_payload(payload: dict) -> dict:
    """Validate a payload dict against the documented Dirac-3 envelope.

    Returns {"ok": bool, "errors": [...], "warnings": [...], "stats": {...}}.
    Errors are hard API/spec violations. Warnings flag coefficient sets that
    do not meet both published analog-resolution checks: spread <= 200 and
    every pair of distinct signed coefficients separated by max(|c|)/200.
    """
    errors, warnings = [], []
    n = int(payload["num_variables"])
    terms = payload["polynomial"]
    degree = int(payload.get("degree") or max(
        sum(1 for i in t["idx"] if i > 0) for t in terms))
    encoding = payload.get("encoding", "continuous")
    params = payload.get("job_params", {})

    max_vars = DEVICE_MAX_VARS_BY_DEGREE.get(degree)
    if max_vars is None:
        errors.append(f"degree {degree} unsupported (device max degree 5)")
    elif n > max_vars:
        errors.append(f"{n} variables exceeds device budget {max_vars} for degree {degree}")

    signed_vals = sorted({float(t["val"]) for t in terms
                          if float(t["val"]) != 0.0})
    magnitudes = [abs(v) for v in signed_vals]
    coeff_spread = None
    min_distinct_separation = None
    required_min_separation = None
    spread_resolved = False
    pairwise_resolved = False
    if not signed_vals:
        errors.append("polynomial has no nonzero terms")
    else:
        max_abs = max(magnitudes)
        min_abs = min(magnitudes)
        coeff_spread = max_abs / min_abs
        required_min_separation = max_abs / DEVICE_DYNAMIC_RANGE
        min_distinct_separation = min(
            (b - a for a, b in zip(signed_vals, signed_vals[1:])),
            default=None,
        )
        spread_resolved = coeff_spread <= DEVICE_DYNAMIC_RANGE + 1e-12
        pairwise_resolved = (
            min_distinct_separation is None
            or min_distinct_separation + 1e-12 >= required_min_separation
        )
        if not spread_resolved:
            warnings.append(
                f"coefficient spread {coeff_spread:.3g} exceeds the published "
                f"~{DEVICE_DYNAMIC_RANGE:.0f}:1 analog-resolution guidance")
        if not pairwise_resolved:
            warnings.append(
                f"minimum distinct-coefficient separation "
                f"{min_distinct_separation:.6g} is below the published "
                f"max(|c|)/{DEVICE_DYNAMIC_RANGE:.0f} threshold "
                f"{required_min_separation:.6g}")
        if max_abs > 3.4e38:
            errors.append("coefficient exceeds 32-bit float range")

    for t in terms:
        idx = t["idx"]
        if all(i == 0 for i in idx):
            errors.append("constant (all-zero idx) term is not supported by Dirac-3")
            break
        if any(idx[k] > idx[k + 1] for k in range(len(idx) - 1)):
            errors.append(f"idx not non-decreasing: {idx}")
            break
        if max(idx) > n:
            errors.append(f"idx {idx} references variable > num_variables={n}")
            break

    ns = params.get("num_samples")
    if ns is not None and not (1 <= int(ns) <= DEVICE_MAX_SAMPLES):
        errors.append(f"num_samples {ns} outside [1, {DEVICE_MAX_SAMPLES}]")
    rs = params.get("relaxation_schedule")
    if rs is not None and int(rs) not in (1, 2, 3, 4):
        errors.append(f"relaxation_schedule {rs} not in 1..4")

    if encoding == "continuous":
        R = params.get("sum_constraint")
        if R is None:
            errors.append("continuous job missing sum_constraint")
        elif not (DEVICE_SUM_CONSTRAINT_RANGE[0] <= float(R) <= DEVICE_SUM_CONSTRAINT_RANGE[1]):
            errors.append(f"sum_constraint {R} outside {DEVICE_SUM_CONSTRAINT_RANGE}")
    else:
        levels = params.get("num_levels")
        if not levels:
            errors.append("integer job missing num_levels")
        elif sum(int(v) for v in levels) > DEVICE_MAX_TOTAL_LEVELS:
            errors.append(f"total num_levels {sum(levels)} exceeds {DEVICE_MAX_TOTAL_LEVELS}")
        if params.get("sum_constraint") is not None:
            warnings.append("sum_constraint is ignored for sample-hamiltonian-integer")

    stats = {
        "num_variables": n,
        "degree": degree,
        "n_terms": len(terms),
        "encoding": encoding,
        "coeff_ratio": coeff_spread,
        "coefficient_spread": coeff_spread,
        "min_distinct_separation": min_distinct_separation,
        "required_min_separation": required_min_separation,
        "spread_within_resolution": spread_resolved,
        "pairwise_distinct_separation_resolved": pairwise_resolved,
        "coefficient_resolution_pass": bool(
            signed_vals and spread_resolved and pairwise_resolved
        ),
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def write_dirac3_payload(prob: PolyProblem, out_dir: Path,
                         relaxation_schedule: int = 1,
                         num_samples: int = 20,
                         solution_precision: float | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    poly_terms = []
    for idx, c in sorted(prob.terms.items()):
        if not idx:
            continue  # device objective is shift-invariant; constant recorded in summary

        # QCi polynomial indices are 1-based and must all have length equal to
        # the submitted maximum degree.  Lower-degree terms are left-padded
        # with zeros, e.g. x_4 -> [0, 0, 4] for a rank-3 Hamiltonian.
        # Keeping this exact convention prevents qci-client/API validation
        # failures and makes the JSON auditable against the QCi examples.
        padded_idx = [0] * (prob.degree - len(idx)) + [i + 1 for i in idx]
        if any(padded_idx[k] > padded_idx[k + 1]
               for k in range(len(padded_idx) - 1)):
            raise ValueError(f"QCI polynomial index is not non-decreasing: {padded_idx}")
        poly_terms.append({"idx": padded_idx, "val": float(c)})

    encoding = prob.meta.get("encoding", "continuous")
    if encoding == "integer":
        job_params = {
            "relaxation_schedule": relaxation_schedule,
            "num_samples": num_samples,
            "num_levels": prob.meta.get("num_levels", [2] * prob.n_vars),
        }
    else:
        job_params = {
            "sum_constraint": float(prob.sum_constraint),
            "relaxation_schedule": relaxation_schedule,
            "num_samples": num_samples,
            **({"solution_precision": solution_precision}
               if solution_precision is not None else {}),
        }

    payload = {
        "problem_name": prob.name,
        "num_variables": prob.n_vars,
        "polynomial": poly_terms,
        "degree": prob.degree,
        "device": "dirac-3",
        "encoding": encoding,
        "job_params": job_params,
    }
    lint = lint_payload(payload)
    payload["device_spec_lint"] = lint
    poly_path = out_dir / f"{prob.name}_polynomial.json"
    poly_path.write_text(json.dumps(payload))
    for w in lint["warnings"]:
        print(f"  [lint:warn] {prob.name}: {w}")
    if not lint["ok"]:
        raise ValueError(f"payload {prob.name} violates Dirac-3 spec: {lint['errors']}")

    summary = {
        "problem_name": prob.name,
        "num_variables": prob.n_vars,
        "num_polynomial_terms": len(poly_terms),
        "degree": prob.degree,
        "constant_offset": prob.terms.get((), 0.0),
        "sum_constraint": prob.sum_constraint,
        "encoding": encoding,
        "job_params": job_params,
        "within_135_variable_rank3_budget":
            bool(prob.n_vars <= 135 and prob.degree <= 3),
        "device_spec_lint": lint,
        "var_names": prob.var_names,
        "scales_mw": prob.scales.tolist(),
        "meta": prob.meta,
    }
    summary_path = out_dir / f"{prob.name}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    return {"polynomial": poly_path, "summary": summary_path,
            "n_terms": len(poly_terms), "lint": lint}


def submit_dirac3(polynomial_path: Path, job_name: str,
                  wait: bool = True) -> dict:
    """Submit via qci-client; requires QCI_API_URL and QCI_TOKEN env vars.

    Kept import-guarded so the package remains usable in sandboxes without
    QCI network access; run this on qBraid for the final live result.
    """
    api_url = os.environ.get("QCI_API_URL")
    token = os.environ.get("QCI_TOKEN")
    if not api_url or not token:
        raise RuntimeError("Set QCI_API_URL and QCI_TOKEN before --submit "
                           "(see README section 6; never commit these).")
    import qci_client as qc  # noqa: import guarded intentionally

    payload = json.loads(Path(polynomial_path).read_text())
    client = qc.QciClient(api_token=token, url=api_url)
    poly_file = {
        "file_name": f"{job_name}_poly",
        "file_config": {"polynomial": {
            "num_variables": payload["num_variables"],
            "min_degree": 1,
            "max_degree": payload["degree"],
            "data": payload["polynomial"],
        }},
    }
    file_resp = client.upload_file(file=poly_file)
    encoding = payload.get("encoding", "continuous")
    job_type = ("sample-hamiltonian-integer" if encoding == "integer"
                else "sample-hamiltonian")
    job_body = client.build_job_body(
        job_type=job_type,
        job_name=job_name,
        job_tags=["gic2026", "power-grids", encoding],
        job_params={"device_type": "dirac-3",
                    **payload["job_params"]},
        polynomial_file_id=file_resp["file_id"],
    )
    return client.process_job(job_body=job_body, wait=wait)
