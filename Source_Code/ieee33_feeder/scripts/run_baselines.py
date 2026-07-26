#!/usr/bin/env python3
"""Benchmark the submitted Hamiltonians with deterministic CPU baselines.

The exact enumerator is the local correctness oracle for these audit-sized
instances.  Simulated annealing is a non-quantum stochastic baseline on the
same objective and variable domains.  No Dirac-3 result is inferred here.
"""
from __future__ import annotations

import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qci_phase3.polynomial import Polynomial


def load_payload(path: Path) -> tuple[Polynomial, list[int] | None, dict]:
    rec = json.loads(path.read_text())
    cfg = rec["file_config"]["polynomial"]
    hint = rec.get("job_params_hint", {})
    if hint.get("job_type") == "sample-hamiltonian":
        levels = None  # continuous sum-constraint payload
        binary = False
    else:
        levels = [int(v) for v in hint.get("num_levels", [2] * int(cfg["num_variables"]))]
        binary = all(v == 2 for v in levels)
    poly = Polynomial(
        nvars=int(cfg["num_variables"]),
        max_degree=int(cfg["max_degree"]),
        binary_simplify=binary,
        constant=float(rec.get("constant_offset_local_only", 0.0)),
    )
    for term in cfg["data"]:
        key = [int(i) - 1 for i in term["idx"] if int(i) > 0]
        poly.add(key, float(term["val"]))
    return poly, levels, rec


def exact_continuous_two_var(poly: Polynomial, sum_constraint: float) -> dict:
    """Golden-section exact minimum for the convex two-variable simplex case."""
    if poly.nvars != 2:
        raise ValueError("continuous baseline supports exactly two variables")

    def f(u: float) -> float:
        return poly.energy([u, sum_constraint - u])

    phi = (5 ** 0.5 - 1) / 2
    a, b = 0.0, sum_constraint
    c = b - phi * (b - a)
    d = a + phi * (b - a)
    while b - a > 1e-12:
        if f(c) < f(d):
            b, d = d, c
            c = b - phi * (b - a)
        else:
            a, c = c, d
            d = a + phi * (b - a)
    u = 0.5 * (a + b)
    return {"energy": f(u), "solution": [u, sum_constraint - u]}


def random_search_continuous(poly: Polynomial, sum_constraint: float, evaluations: int, seed: int) -> float:
    """Matched-budget stochastic baseline: uniform simplex sampling."""
    rng = np.random.default_rng(seed)
    best = float("inf")
    for _ in range(evaluations):
        u = float(rng.random()) * sum_constraint
        e = poly.energy([u, sum_constraint - u])
        if e < best:
            best = e
    return best


def anneal_integer(poly: Polynomial, levels: list[int], evaluations: int, seed: int) -> tuple[float, list[int]]:
    """Run exactly ``evaluations`` objective evaluations, including the seed state."""
    rng = np.random.default_rng(seed)
    x = np.array([int(rng.integers(0, level)) for level in levels], dtype=int)
    e = poly.energy(x)
    best_e, best_x = e, x.copy()
    scale = max(abs(e), 1.0)
    for t in range(max(1, evaluations - 1)):
        j = int(rng.integers(0, len(levels)))
        old = int(x[j])
        # Draw uniformly from the other legal levels so every loop iteration
        # performs one new objective evaluation.  This makes the stated
        # evaluation count exact rather than an upper bound.
        proposal = int(rng.integers(0, levels[j] - 1))
        if proposal >= old:
            proposal += 1
        x[j] = proposal
        e2 = poly.energy(x)
        temperature = scale * (0.001 ** (t / max(1, evaluations - 2)))
        if e2 <= e or rng.random() < math.exp(-(e2 - e) / max(temperature, 1e-12)):
            e = e2
            if e < best_e:
                best_e, best_x = e, x.copy()
        else:
            x[j] = old
    return float(best_e), best_x.tolist()


def benchmark(path: Path, seeds: int, evaluations: int) -> dict:
    poly, levels, rec = load_payload(path)
    continuous = levels is None
    sum_constraint = float(rec.get("job_params_hint", {}).get("sum_constraint", 1.0)) if continuous else None
    exact_times = []
    exact = None
    for _ in range(10):
        exact_started = time.perf_counter()
        if continuous:
            exact = exact_continuous_two_var(poly, sum_constraint)
        elif all(v == 2 for v in levels):
            exact = poly.exact_solve_binary()
        else:
            exact = poly.exact_solve_integer(levels)
        exact_times.append(1_000.0 * (time.perf_counter() - exact_started))
    assert exact is not None
    exact_time_ms = float(np.median(exact_times))
    exact_e = float(exact["energy"])

    gaps = []
    runtimes = []
    for seed in range(seeds):
        started = time.perf_counter()
        if continuous:
            found_e = random_search_continuous(poly, sum_constraint, evaluations=evaluations, seed=seed)
        else:
            found_e, _ = anneal_integer(poly, levels, evaluations=evaluations, seed=seed)
        runtimes.append(1_000.0 * (time.perf_counter() - started))
        gaps.append(max(0.0, found_e - exact_e))

    # Success criterion: exact hit for discrete domains; relative optimality
    # gap <= 0.1% for the continuous sum-constraint domain (disclosed).
    if continuous:
        denom = max(abs(exact_e), 1.0)
        successes = sum(g / denom <= 1e-3 for g in gaps)
        domain = f"continuous simplex sum={sum_constraint}"
        state_space = "continuous"
    else:
        successes = sum(g <= 1e-7 for g in gaps)
        domain = "binary" if all(v == 2 for v in levels) else f"integer levels={levels}"
        state_space = int(np.prod(levels, dtype=np.int64))

    cfg = rec["file_config"]["polynomial"]
    return {
        "payload": path.name,
        "stage": rec.get("metadata", {}).get("stage", "unknown"),
        "variables": poly.nvars,
        "rank": int(cfg["max_degree"]),
        "terms": len(cfg["data"]),
        "domain": domain,
        "state_space": state_space,
        "exact_energy": exact_e,
        "exact_median_runtime_ms_10_repeats": exact_time_ms,
        "sa_seeds": seeds,
        "sa_evaluations_per_seed": evaluations,
        "sa_success_rate": successes / seeds,
        "sa_mean_gap": float(np.mean(gaps)),
        "sa_max_gap": float(np.max(gaps)),
        "sa_median_runtime_ms": float(np.median(runtimes)),
    }


def main() -> int:
    payload_dir = REPO / "qci_payloads"
    paths = sorted(payload_dir.glob("*.json"))
    samples_per_live_job = 25
    rows = [benchmark(path, seeds=30, evaluations=samples_per_live_job) for path in paths]
    out_csv = REPO / "results" / "classical_baseline_summary.csv"
    out_json = REPO / "results" / "classical_baseline_summary.json"
    with out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    out_json.write_text(json.dumps({
        "method": "CPU exact enumeration plus same-objective stochastic baseline",
        "budget_note": (
            f"{samples_per_live_job} objective evaluations per seed, matched only by count "
            f"to the {samples_per_live_job} samples requested in each frozen Dirac-3 job; "
            "this is not a claim of equal computational work or equivalent sampling dynamics"
        ),
        "rows": rows,
    }, indent=2))
    print(f"benchmarked {len(rows)} recommended payloads")
    print(f"wrote {out_csv}")
    print(f"wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
