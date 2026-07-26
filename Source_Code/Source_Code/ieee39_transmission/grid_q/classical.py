"""Stage D solvers, sandbox edition.

Two roles, kept deliberately separate and labelled honestly:

1. `slsqp_dispatch`   — classical *ground truth* for the Stage-2b subproblem:
   full-horizon SLSQP on the physical cubic-cost model with hard balance,
   SOC-bound, and terminal-SOC constraints.  This is the MINLP-family
   reference the write-up benchmarks against.

2. `polyproblem_relaxation` — a classical multi-start smooth minimisation of
   the *exact Dirac-3 polynomial* (penalised, sum-constrained).  It exercises
   the identical objective the device will receive and therefore validates the
   encoding and the penalty ordering, but it is a CPU proxy for landscape
   quality only — it is *not* a device-performance claim.  Live Dirac-3
   sampling replaces it on qBraid.

3. `exhaustive_selection` / `anneal_selection` / `milp_selection` — exact,
   heuristic, and established-MILP-solver (HiGHS via scipy) baselines for the
   Stage-2a binary selection Hamiltonian (n <= ~18 exact).  `milp_selection`
   satisfies the challenge requirement of benchmarking against an established
   classical solver on the identical problem instance.
"""
from __future__ import annotations

import itertools
import numpy as np
from scipy.optimize import minimize

from .hamiltonian import (PolyProblem, dispatch_physical, true_dispatch_cost,
                          islanded_lg_cap)
from .islands import CandidateIsland
from .scenarios import Scenario


# ---------------------------------------------------------------------------
# 1. classical ground truth for dispatch (physical model, hard constraints)
# ---------------------------------------------------------------------------

def slsqp_dispatch(cand: CandidateIsland, scen: Scenario, hours: int = 24,
                   eta: float = 0.92, seed: int = 17,
                   voll_override: float | None = None,
                   window_start: int = 0, cyclic_soc: bool = True):
    T = hours
    mt_cap = cand.der.microturbine_mw
    b_mw, b_mwh = cand.der.bess_mw, cand.der.bess_mwh
    c2, c1 = 0.015, 60.0
    c3 = 0.15 * c2 / max(mt_cap, 1.0)
    crit_frac = cand.critical_load / max(cand.base_load, 1e-6)
    from .hamiltonian import VOLL, VOLL_CRITICAL
    voll_eff = (voll_override if voll_override is not None
                else crit_frac * VOLL_CRITICAL + (1 - crit_frac) * VOLL)

    lg_cap = islanded_lg_cap(cand)
    g2, g1 = 0.012, 55.0
    g3 = 0.15 * g2 / max(lg_cap, 1.0)

    w = slice(window_start, window_start + T)
    load = cand.base_load * scen.load_scale[w]
    pv = cand.der.pv_mw * scen.pv_scale[w]

    def unpack(z):
        return (z[0:T], z[T:2 * T], z[2 * T:3 * T], z[3 * T:4 * T],
                z[4 * T:5 * T])

    def obj(z):
        pm, pg, pd, pc, sh = unpack(z)
        return float(np.sum(c3 * pm ** 3 + c2 * pm ** 2 + c1 * pm
                            + g3 * pg ** 3 + g2 * pg ** 2 + g1 * pg
                            + voll_eff * sh))

    def grad(z):
        pm, pg, pd, pc, sh = unpack(z)
        return np.concatenate([3 * c3 * pm ** 2 + 2 * c2 * pm + c1,
                               3 * g3 * pg ** 2 + 2 * g2 * pg + g1,
                               np.zeros(T), np.zeros(T),
                               np.full(T, voll_eff)])

    # The physical model is CONVEX: positive-coefficient cubic fuel costs on
    # a box, plus LINEAR balance and cumulative-SOC constraints.  The primary
    # solver is trust-constr with an analytic gradient and explicit
    # LinearConstraint matrices, which terminates with a first-order
    # certificate (the earlier lambda-per-hour SLSQP formulation stalled at
    # maxiter on the 120-variable instance and reported converged=False).
    # Multi-start SLSQP is retained as a fallback.  The historical function
    # name is kept so downstream scripts and result JSONs stay comparable.
    from scipy.optimize import LinearConstraint
    soc0 = 0.5 * b_mwh
    A_bal = np.zeros((T, 5 * T))
    for t in range(T):
        A_bal[t, [t, T + t, 2 * T + t, 4 * T + t]] = 1.0
        A_bal[t, 3 * T + t] = -1.0
    rhs = load - pv
    Ltri = np.tril(np.ones((T, T)))
    A_soc = np.zeros((T, 5 * T))
    A_soc[:, 3 * T:4 * T] = eta * Ltri
    A_soc[:, 2 * T:3 * T] = -Ltri / eta
    lincons = [LinearConstraint(A_bal, rhs, rhs),
               LinearConstraint(A_soc, -soc0, b_mwh - soc0)]
    if cyclic_soc:
        # day-ahead product: SOC returns to its starting point
        lincons.append(LinearConstraint(A_soc[-1:], 0.0, 0.0))
    # outage ride-through (cyclic_soc=False): stored energy may be drawn down

    bounds = ([(0, mt_cap)] * T + [(0, lg_cap)] * T + [(0, b_mw)] * T
              + [(0, b_mw)] * T + [(0, cand.base_load * 1.3)] * T)

    # Deterministic feasible merit-order initializer (hourly balance is exact).
    z_merit = np.zeros(5 * T)
    for t in range(T):
        need = max(0.0, load[t] - pv[t])
        pm0 = min(need, mt_cap)
        pg0 = min(need - pm0, lg_cap)
        z_merit[t] = pm0
        z_merit[T + t] = pg0
        z_merit[4 * T + t] = max(0.0, need - pm0 - pg0)

    res = minimize(obj, z_merit, jac=grad, method="trust-constr",
                   bounds=bounds, constraints=lincons,
                   options={"maxiter": 3000, "gtol": 1e-8, "xtol": 1e-10})
    method = "trust-constr"
    if not res.success:  # fallback: multi-start SLSQP on the same matrices
        cons = [{"type": "eq", "fun": (lambda z, t=t: A_bal[t] @ z - rhs[t])}
                for t in range(T)]
        cons.append({"type": "ineq", "fun": lambda z: A_soc @ z + soc0})
        cons.append({"type": "ineq", "fun": lambda z: (b_mwh - soc0) - A_soc @ z})
        if cyclic_soc:
            cons.append({"type": "eq", "fun": lambda z: A_soc[-1] @ z})
        rng = np.random.default_rng(seed)
        best = None
        for z0 in [z_merit,
                   np.clip(z_merit * (1 + 0.05 * rng.standard_normal(5 * T)),
                           0.0, None)]:
            attempt = minimize(obj, z0, jac=grad, method="SLSQP",
                               bounds=bounds, constraints=cons,
                               options={"maxiter": 2000, "ftol": 1e-9})
            if (best is None or (attempt.success and not best.success)
                    or (attempt.success == best.success
                        and attempt.fun < best.fun)):
                best = attempt
        res, method = best, "SLSQP-fallback"

    pm, pg, pd, pc, sh = unpack(res.x)
    sched = {"p_mt": pm, "p_lg": pg, "p_dis": pd, "p_ch": pc, "l_shed": sh}
    cost = float(np.sum(c3 * pm ** 3 + c2 * pm ** 2 + c1 * pm
                        + g3 * pg ** 3 + g2 * pg ** 2 + g1 * pg
                        + voll_eff * sh))
    imb = pm + pg + pd - pc + pv - load + sh
    soc = soc0 + np.cumsum(eta * pc - pd / eta)
    diag = {"imbalance_max_mw": float(np.max(np.abs(imb))),
            "soc_violation_mwh": float(max(0.0, np.max(soc - b_mwh),
                                           np.max(-soc))),
            "unserved_frac": np.minimum(1.0, sh / np.maximum(load, 1e-6))}
    return {"sched": sched, "cost": cost, "diag": diag,
            "converged": bool(res.success), "method": method}


def fast_dispatch(cand: CandidateIsland, scen: Scenario, hours: int = 24,
                  eta: float = 0.92, window_start: int = 0,
                  voll_override: float | None = None):
    """Deterministic merit-order dispatch used for fast scenario sweeps.

    It is conservative: PV serves load first, then microturbine, derated legacy
    generation, BESS discharge, and finally load shedding.  It does not claim
    to be the optimum; SLSQP remains the classical ground-truth check for the
    flagship dispatch instance.  The function makes the Phase-3 audit
    reproducible in seconds while keeping all power-balance/SOC diagnostics.
    """
    T = hours
    sched = {k: np.zeros(T) for k in ("p_mt", "p_lg", "p_dis", "p_ch", "l_shed")}
    mt_cap = cand.der.microturbine_mw
    lg_cap = islanded_lg_cap(cand)
    b_mw, b_mwh = cand.der.bess_mw, cand.der.bess_mwh
    soc = 0.5 * b_mwh
    for t in range(T):
        k = window_start + t
        load_t = cand.base_load * scen.load_scale[k]
        pv_t = cand.der.pv_mw * scen.pv_scale[k]
        need = max(0.0, load_t - pv_t)
        pm = min(need, mt_cap)
        need -= pm
        pg = min(need, lg_cap)
        need -= pg
        pd = min(need, b_mw, soc * eta)
        soc -= pd / eta
        need -= pd
        sh = max(0.0, need)
        sched["p_mt"][t] = pm
        sched["p_lg"][t] = pg
        sched["p_dis"][t] = pd
        sched["l_shed"][t] = sh
    from .hamiltonian import true_dispatch_cost
    cost, diag = true_dispatch_cost(cand, scen, sched, voll_override=voll_override)
    return {"sched": sched, "cost": cost, "diag": diag, "converged": True,
            "method": "fast_merit_order"}


# ---------------------------------------------------------------------------
# 2. classical relaxation of the exact Dirac-3 polynomial (encoding check)
# ---------------------------------------------------------------------------

def polyproblem_relaxation(prob: PolyProblem, restarts: int = 6,
                           seed: int = 17,
                           x0_feasible: "np.ndarray | None" = None):
    """Multi-start L-BFGS-B on the penalised polynomial with box [0,1]^n.

    Returns best x (device units), energy, and per-restart energies.  The sum
    constraint is enforced implicitly: idle capacity flows to the slack, and
    the physical model never rewards exceeding per-variable scales because
    every variable is box-bounded at its own scale.
    """
    rng = np.random.default_rng(seed)
    n = prob.n_vars

    # group terms by degree for a vectorised gradient
    lin = np.zeros(n)
    quad: list[tuple[int, int, float]] = []
    cub: list[tuple[int, int, int, float]] = []
    const = prob.terms.get((), 0.0)
    for idx, c in prob.terms.items():
        if len(idx) == 1:
            lin[idx[0]] += c
        elif len(idx) == 2:
            quad.append((*idx, c))
        elif len(idx) == 3:
            cub.append((*idx, c))

    def f_and_g(x):
        e = const + lin @ x
        g = lin.copy()
        for i, j, c in quad:
            if i == j:
                e += c * x[i] * x[i]
                g[i] += 2 * c * x[i]
            else:
                e += c * x[i] * x[j]
                g[i] += c * x[j]
                g[j] += c * x[i]
        for i, j, k, c in cub:
            if i == j == k:
                e += c * x[i] ** 3
                g[i] += 3 * c * x[i] ** 2
            else:  # our builder only emits pure-cube rank-3 terms
                e += c * x[i] * x[j] * x[k]
                g[i] += c * x[j] * x[k]
                g[j] += c * x[i] * x[k]
                g[k] += c * x[i] * x[j]
        return e, g

    best_x, best_e, energies = None, np.inf, []
    starts = ([x0_feasible] if x0_feasible is not None else [])
    starts += [rng.random(n) * 0.3 for _ in range(restarts)]
    for x0 in starts:
        res = minimize(lambda x: f_and_g(x), np.asarray(x0, float), jac=True,
                       method="L-BFGS-B", bounds=[(0.0, 1.0)] * n,
                       options={"maxiter": 800})
        energies.append(float(res.fun))
        if res.fun < best_e:
            best_e, best_x = float(res.fun), res.x
    return {"x": best_x, "energy": best_e, "restart_energies": energies}


def simplex_polynomial_min(prob: PolyProblem, restarts: int = 4,
                           seed: int = 17) -> dict:
    """CPU minimisation of a device polynomial ON the device's feasible set
    (x >= 0, sum x = R).  This solves the *identical* mathematical problem
    Dirac-3 receives, so decoded live samples can be compared against a
    classical optimum of the same objective — separating encoding error from
    device sampling error.  SLSQP with an explicit equality constraint."""
    rng = np.random.default_rng(seed)
    n, R = prob.n_vars, float(prob.sum_constraint)

    def f(x):
        return prob.evaluate(x)

    cons = [{"type": "eq", "fun": lambda x: np.sum(x) - R}]
    best = None
    starts = [np.full(n, R / n)]
    starts += [rng.dirichlet(np.ones(n)) * R for _ in range(restarts)]
    for x0 in starts:
        res = minimize(f, x0, method="SLSQP", bounds=[(0.0, R)] * n,
                       constraints=cons, options={"maxiter": 500, "ftol": 1e-10})
        if best is None or (res.success and res.fun < best.fun):
            best = res
    return {"x": best.x, "energy": float(best.fun),
            "converged": bool(best.success)}


# ---------------------------------------------------------------------------
# 3. selection solvers
# ---------------------------------------------------------------------------

def exhaustive_selection(prob: PolyProblem) -> dict:
    """Exact binary selection by vectorized enumeration.

    The Stage-2a selector is intentionally small (candidate blocks rather than
    free bus assignment).  Vectorising the enumeration keeps the audit run
    deterministic and fast while still being a true exhaustive optimum.
    """
    n = prob.n_vars
    assert n <= 22, "exhaustive selection limited to 22 candidates"
    m = 1 << n
    ints = np.arange(m, dtype=np.uint64)[:, None]
    shifts = np.arange(n, dtype=np.uint64)[None, :]
    Y = ((ints >> shifts) & 1).astype(float)

    E = np.full(m, prob.terms.get((), 0.0), dtype=float)
    for idx, c in prob.terms.items():
        if not idx:
            continue
        # repeated indices naturally implement y_j^2/y_j^3; for binary y this
        # equals y_j, but the generic product keeps the polynomial semantics.
        E += float(c) * np.prod(Y[:, list(idx)], axis=1)
    k = int(np.argmin(E))
    return {"y": Y[k].copy(), "energy": float(E[k])}


def anneal_selection(prob: PolyProblem, sweeps: int = 300, seed: int = 17,
                     n_reads: int = 20) -> dict:
    """Simple fixed-seed single-spin-flip simulated-annealing baseline."""
    rng = np.random.default_rng(seed)
    n = prob.n_vars
    best_y, best_e = None, np.inf
    for _ in range(n_reads):
        y = (rng.random(n) < 0.5).astype(float)
        e = prob.evaluate(y)
        for sweep in range(sweeps):
            T = max(0.02, 2.0 * (1 - sweep / sweeps))
            j = int(rng.integers(n))
            y2 = y.copy()
            y2[j] = 1 - y2[j]
            e2 = prob.evaluate(y2)
            if e2 < e or rng.random() < np.exp(-(e2 - e) / T):
                y, e = y2, e2
        if e < best_e:
            best_y, best_e = y.copy(), e
    return {"y": best_y, "energy": float(best_e)}


def milp_selection(prob: PolyProblem) -> dict:
    """Established-solver baseline: HiGHS branch-and-bound via scipy.milp.

    The Stage-2a selection Hamiltonian is quadratic in binaries:
        E(y) = sum_j l_j y_j + sum_{i<j} q_ij y_i y_j (+ const).
    Products y_i y_j are linearised exactly with standard AND variables
    z_ij (z <= y_i, z <= y_j, z >= y_i + y_j - 1), so HiGHS solves the
    *identical* instance to proven optimality.  This provides the challenge's
    required comparison against an established classical solver, alongside
    the exhaustive oracle and the annealing heuristic.
    """
    import time
    from scipy.optimize import milp, LinearConstraint, Bounds

    n = prob.n_vars
    lin = np.zeros(n)
    quad: dict[tuple[int, int], float] = {}
    const = prob.terms.get((), 0.0)
    for idx, c in prob.terms.items():
        if not idx:
            continue
        u = tuple(sorted(set(idx)))
        if len(u) == 1:
            lin[u[0]] += c          # y^k = y for binary y
        elif len(u) == 2:
            quad[u] = quad.get(u, 0.0) + c
        else:
            raise ValueError("milp_selection expects degree <= 2 in distinct vars")

    pairs = sorted(quad)
    m = len(pairs)
    cost = np.concatenate([lin, np.array([quad[p] for p in pairs])])
    A_rows, lb, ub = [], [], []
    for k, (i, j) in enumerate(pairs):
        row1 = np.zeros(n + m); row1[i] = -1.0; row1[n + k] = 1.0   # z <= y_i
        row2 = np.zeros(n + m); row2[j] = -1.0; row2[n + k] = 1.0   # z <= y_j
        row3 = np.zeros(n + m); row3[i] = 1.0; row3[j] = 1.0; row3[n + k] = -1.0
        A_rows += [row1, row2, row3]                                # y_i+y_j-z <= 1
        lb += [-np.inf, -np.inf, -np.inf]
        ub += [0.0, 0.0, 1.0]
    t0 = time.time()
    constraints = ([LinearConstraint(np.vstack(A_rows), lb, ub)]
                   if A_rows else [])
    res = milp(c=cost, integrality=np.ones(n + m),
               bounds=Bounds(0, 1), constraints=constraints)
    if not res.success:
        return {"y": None, "energy": None, "status": res.message,
                "wall_s": time.time() - t0}
    y = np.round(res.x[:n])
    return {"y": y, "energy": float(prob.evaluate(y)),
            "milp_objective_plus_const": float(res.fun + const),
            "status": "optimal", "wall_s": time.time() - t0,
            "solver": "HiGHS (scipy.optimize.milp)"}
