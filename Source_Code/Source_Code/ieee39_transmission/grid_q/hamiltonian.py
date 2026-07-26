"""Stage C: polynomial cost Hamiltonians for Dirac-3 (Challenge Stages 2a/2b).

Two Hamiltonians, mirroring the Phase 2 architecture:

Stage 2b — DER-dispatch Hamiltonian (per candidate island, per scenario,
24-hour horizon, rank 3).  Variables per hour t:
    p_mt[t]   microturbine output          (cubic thermal cost, native rank 3)
    p_dis[t]  BESS discharge
    p_ch[t]   BESS charge
    l_shed[t] unserved load
plus one global slack.  96 physical variables + slack = 97 <= 135 (Dirac-3
budget).  Constraints enter as ordered quadratic penalties:
    mu_bal * (p_mt + p_dis - p_ch + pv - load + l_shed)^2        per hour
    kappa  * p_ch[t] * p_dis[t]                                   exclusivity
    nu_soc * (sum_t (eta*p_ch[t] - p_dis[t]/eta) - dE_target)^2   terminal SOC
The terminal-SOC penalty expands to a dense all-to-all quadratic over the 48
charge/discharge variables — precisely the coupling structure Dirac-3 embeds
without minor-embedding overhead.  Intra-horizon SOC bound violations are
checked classically and repaired by the outer loop (penalty retune +
projection), as stated in Phase 2.

Stage 2a — islanding-selection Hamiltonian (per contingency scenario,
rank 2/3).  Binary y_j selects candidate island j from Stage A's
pre-validated connected blocks:
    H_sel = sum_j y_j * (V_j + A_j)            islanded value-at-stake + upgrade
          + sum_j (1-y_j) * U_j                unserved-exposure if not islanded
          + lam_overlap * sum_{i<j} O_ij y_i y_j
          + lam_bin * sum_j y_j (y_j - 1)      integer -> binary
where V_j is the *evaluated* Stage-2b dispatch cost of candidate j under the
scenario, U_j its weighted unserved-load exposure if it stays grid-tied
through the contingency, and A_j its annualised upgrade cost share.
Connectivity never appears as a constraint because every candidate is
connected by construction.

Hamiltonians are stored as {tuple(sorted(var_indices)): coeff} with variables
0-indexed; degree <= 3 throughout.  `evaluate` is the single evaluator used by
every solver so encodings cannot silently diverge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from .islands import CandidateIsland
from .scenarios import Scenario

GRID_PRICE = 45.0          # $/MWh energy purchased through the PCC
VOLL = 2_500.0             # $/MWh value of lost load (ordinary)
VOLL_CRITICAL = 10_000.0   # $/MWh value of lost load (critical)
ANNUAL_HOURS = 8760.0

# Dirac-3 documented coefficient dynamic range (max/min |coefficient| ratio ~200)
# and continuous-solution resolution (~sum_constraint/200).  Device-facing
# payloads built here are conditioned to respect this; the full-fidelity
# polynomial is always kept classically for decoding and scoring.
DIRAC3_DYNAMIC_RANGE = 200.0


def islanded_lg_cap(cand: CandidateIsland) -> float:
    """Usable legacy-generation capacity while islanded (disclosed cap).

    Legacy in-island units are derated 50% when islanded (Stage-A siting
    rule), and an island can never usefully dispatch more than its own maximum
    load (1.3 x base) because there is no export path while separated from the
    utility.  The second bound is physically meaningful and it also keeps the
    Dirac-3 payload coefficients within a moderate dynamic range: without it a
    generation-heavy candidate (one containing a large IEEE-39 plant) carries
    a variable scale tens of times its load, and the balance-penalty
    coefficients blow past the device's documented 200:1 range.
    """
    return min(0.5 * cand.internal_gen_mw, 1.3 * cand.base_load)


@dataclass
class PolyProblem:
    """A Dirac-3-shaped polynomial minimisation problem."""
    name: str
    n_vars: int
    terms: dict[tuple[int, ...], float]
    scales: np.ndarray                 # physical value = scales * x
    upper: np.ndarray                  # physical upper bounds
    sum_constraint: float              # device-units budget R (incl. slack)
    var_names: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def add(self, idx: tuple[int, ...], coeff: float) -> None:
        if abs(coeff) < 1e-12:
            return
        key = tuple(sorted(idx))
        self.terms[key] = self.terms.get(key, 0.0) + coeff

    def evaluate(self, x: np.ndarray) -> float:
        e = self.terms.get((), 0.0)
        for idx, c in self.terms.items():
            if idx:
                e += c * np.prod(x[list(idx)])
        return float(e)

    @property
    def degree(self) -> int:
        return max((len(k) for k in self.terms), default=0)


# ---------------------------------------------------------------------------
# Stage 2b: cubic DER dispatch over a 24 h horizon
# ---------------------------------------------------------------------------

def build_dispatch_hamiltonian(cand: CandidateIsland, scen: Scenario,
                               soc0_frac: float = 0.5,
                               mu_bal: float = 25_000.0,
                               nu_soc: float = 2_500.0,
                               kappa: float = 500.0,
                               eta: float = 0.92,
                               hours: int = 24) -> PolyProblem:
    T = hours
    names, scales, upper = [], [], []

    def add_var(name, ub):
        names.append(name)
        upper.append(max(ub, 1e-6))
        scales.append(max(ub, 1e-6))     # device x in [0,1] per variable

    mt_cap = cand.der.microturbine_mw
    b_mw = cand.der.bess_mw
    # legacy in-island generation: derated 50% when islanded and capped at the
    # island's own maximum load (no export path while islanded) — see
    # islanded_lg_cap for the disclosed physical/encoding rationale.
    lg_cap = islanded_lg_cap(cand)
    for t in range(T):
        add_var(f"p_mt[{t}]", mt_cap)
        add_var(f"p_lg[{t}]", lg_cap)
        add_var(f"p_dis[{t}]", b_mw)
        add_var(f"p_ch[{t}]", b_mw)
        add_var(f"l_shed[{t}]", cand.base_load * 1.3)
    add_var("slack", float(T))           # keeps the simplex feasible at idle

    n = len(names)
    prob = PolyProblem(name=f"dispatch_c{cand.cid}_s{scen.sid}", n_vars=n,
                       terms={}, scales=np.array(scales),
                       upper=np.array(upper),
                       sum_constraint=float(n),   # sum of unit-scaled x <= n
                       var_names=names,
                       meta={"cid": cand.cid, "sid": scen.sid, "hours": T,
                             "soc0_frac": soc0_frac, "eta": eta,
                             "mu_bal": mu_bal, "nu_soc": nu_soc,
                             "kappa": kappa})

    s = prob.scales
    # per-MW cubic costs: microturbine and legacy unit reuse the disclosed
    # synthetic cubic structure, each scaled to its own capacity.
    c2, c1 = 0.015, 60.0
    c3 = 0.15 * c2 / max(mt_cap, 1.0)
    g2, g1 = 0.012, 55.0
    g3 = 0.15 * g2 / max(lg_cap, 1.0)

    STRIDE = 5

    def V(t):  # variable index helpers: mt, lg, dis, ch, shed
        b = STRIDE * t
        return b, b + 1, b + 2, b + 3, b + 4

    crit_frac = cand.critical_load / max(cand.base_load, 1e-6)
    voll_eff = crit_frac * VOLL_CRITICAL + (1 - crit_frac) * VOLL

    for t in range(T):
        i_mt, i_lg, i_dis, i_ch, i_sh = V(t)
        load_t = cand.base_load * scen.load_scale[t]
        pv_t = cand.der.pv_mw * scen.pv_scale[t]

        # cubic thermal costs (native rank 3 on Dirac-3, no quadratization)
        prob.add((i_mt,), c1 * s[i_mt])
        prob.add((i_mt, i_mt), c2 * s[i_mt] ** 2)
        prob.add((i_mt, i_mt, i_mt), c3 * s[i_mt] ** 3)
        prob.add((i_lg,), g1 * s[i_lg])
        prob.add((i_lg, i_lg), g2 * s[i_lg] ** 2)
        prob.add((i_lg, i_lg, i_lg), g3 * s[i_lg] ** 3)
        # shed penalty
        prob.add((i_sh,), voll_eff * s[i_sh])
        # charge/discharge exclusivity
        prob.add((i_ch, i_dis), kappa * s[i_ch] * s[i_dis])

        # power balance penalty: (a . x + b)^2 with
        # a = [s_mt, s_lg, s_dis, -s_ch, s_sh], b = pv_t - load_t
        idxs = [i_mt, i_lg, i_dis, i_ch, i_sh]
        coefs = [s[i_mt], s[i_lg], s[i_dis], -s[i_ch], s[i_sh]]
        b = pv_t - load_t
        prob.add((), mu_bal * b * b)
        for a_i, ii in zip(coefs, idxs):
            prob.add((ii,), 2 * mu_bal * a_i * b)
            for a_j, jj in zip(coefs, idxs):
                # ordered double loop: off-diagonal pairs visited twice, which
                # correctly accumulates the 2*a_i*a_j cross term; diagonal once.
                prob.add((ii, jj), mu_bal * a_i * a_j)

    # terminal SOC: require net energy exchange returns SOC to soc0
    # sum_t (eta*p_ch - p_dis/eta) = 0  ->  quadratic in the 48 BESS variables
    idxs, coefs = [], []
    for t in range(T):
        _, _, i_dis, i_ch, _ = V(t)
        idxs += [i_ch, i_dis]
        coefs += [eta * s[i_ch], -s[i_dis] / eta]
    for ii, a_i in zip(idxs, coefs):
        for jj, a_j in zip(idxs, coefs):
            prob.add((ii, jj), nu_soc * a_i * a_j)
    # (ordered double loop: off-diagonal terms visited twice = full 2*a_i*a_j)

    return prob


def dispatch_physical(prob: PolyProblem, x: np.ndarray) -> dict[str, np.ndarray]:
    """Decode a device-unit solution vector into physical MW schedules."""
    T = prob.meta["hours"]
    p = prob.scales * x
    out = {k: np.zeros(T) for k in ("p_mt", "p_lg", "p_dis", "p_ch", "l_shed")}
    for t in range(T):
        out["p_mt"][t] = p[5 * t]
        out["p_lg"][t] = p[5 * t + 1]
        out["p_dis"][t] = p[5 * t + 2]
        out["p_ch"][t] = p[5 * t + 3]
        out["l_shed"][t] = p[5 * t + 4]
    return out


def true_dispatch_cost(cand: CandidateIsland, scen: Scenario,
                       sched: dict[str, np.ndarray],
                       voll_override: float | None = None) -> tuple[float, dict]:
    """Physical objective + feasibility diagnostics (imbalance, SOC bounds)."""
    T = len(sched["p_mt"])
    mt_cap = cand.der.microturbine_mw
    c2, c1 = 0.015, 60.0
    c3 = 0.15 * c2 / max(mt_cap, 1.0)
    crit_frac = cand.critical_load / max(cand.base_load, 1e-6)
    voll_eff = (voll_override if voll_override is not None
                else crit_frac * VOLL_CRITICAL + (1 - crit_frac) * VOLL)
    lg_cap = islanded_lg_cap(cand)
    g2, g1 = 0.012, 55.0
    g3 = 0.15 * g2 / max(lg_cap, 1.0)
    eta = 0.92
    cost, imb_max = 0.0, 0.0
    soc = 0.5 * cand.der.bess_mwh
    soc_viol = 0.0
    unserved_frac = np.zeros(T)
    p_lg = sched.get("p_lg", np.zeros(T))
    for t in range(T):
        pm, pd, pc, sh = (sched[k][t] for k in ("p_mt", "p_dis", "p_ch", "l_shed"))
        pg = p_lg[t]
        load_t = cand.base_load * scen.load_scale[t]
        pv_t = cand.der.pv_mw * scen.pv_scale[t]
        cost += (c3 * pm ** 3 + c2 * pm ** 2 + c1 * pm
                 + g3 * pg ** 3 + g2 * pg ** 2 + g1 * pg + voll_eff * sh)
        imb = pm + pg + pd - pc + pv_t - load_t + sh
        imb_max = max(imb_max, abs(imb))
        soc += eta * pc - pd / eta
        soc_viol = max(soc_viol, max(0.0, -soc), max(0.0, soc - cand.der.bess_mwh))
        unserved_frac[t] = min(1.0, sh / max(load_t, 1e-6))
    return cost, {"imbalance_max_mw": imb_max, "soc_violation_mwh": soc_viol,
                  "unserved_frac": unserved_frac}


# ---------------------------------------------------------------------------
# Stage 2b': device-native outage-window dispatch (Dirac-3 spec-compliant)
#
# The 24 h / 121-variable cubic Hamiltonian above is retained as a documented
# *capability probe*: its penalty-dominated coefficient spread exceeds the
# device's documented ~200:1 dynamic range, so it characterises device
# behaviour beyond spec rather than claiming in-spec optimisation.
#
# The payloads below are the in-spec quantum artifacts used for the headline
# live runs.  Encoding, per hour of the islanded ride-through window:
#
#   variables    x = (p_mt, p_lg, p_dis, s_shed) in device units of u MW
#   simplex      sum(x) = R = net_load_t / u   <- Dirac-3's native constraint
#                IS the hourly energy balance; no balance penalty is needed.
#   objective    sum_src (c1_src - K) * u * x_src            (merit order)
#              + sum_src (a2_src + c2_src) * u^2 * x_src^2   (capacity walls)
#   with K > max marginal fuel cost.  Subtracting the uniform service reward
#   K per MWh makes every real source's linear coefficient negative, so
#   leaving energy on the uncosted shed slack costs exactly (K - c_src) per
#   MWh: K acts as a device-side VoLL surrogate.  Because K exceeds every
#   fuel marginal cost, the merit order (shed last) of the true-VoLL problem
#   is preserved; classical scoring restores the true VoLL and cubic terms.
#   The quadratic wall a2_src = (K - c1_src) / (2 * cap_src) - c2_src is
#   calibrated so each source's unconstrained device optimum lands exactly at
#   its capacity, encoding per-variable caps that the raw simplex lacks.
#
# Result: coefficient dynamic range ~15-60 (well inside 200), solution
# resolution R/200 = 0.5% of hourly net load, and an exact per-hour balance.
# Cross-hour BESS energy (SOC) limits are enforced by a classical repair pass
# that is disclosed and scored.  This mirrors the challenge's recommended
# decomposition: a classical master iterates; Dirac-3 solves the subproblems.
# ---------------------------------------------------------------------------

DEVICE_SERVICE_REWARD_MARGIN = 2.0   # K = margin * max marginal fuel cost


def _window_caps(cand: CandidateIsland) -> dict[str, float]:
    return {
        "p_mt": cand.der.microturbine_mw,
        "p_lg": islanded_lg_cap(cand),
        "p_dis": cand.der.bess_mw,
    }


def allocate_bess_budget(cand: CandidateIsland, scen: Scenario,
                         window_start: int, hours: int = 4,
                         soc0_mwh: float | None = None,
                         eta: float = 0.92) -> list[float]:
    """Classical master step: allocate the battery's usable energy across the
    outage-window hours BEFORE the hourly device subproblems are built.

    The battery couples hours through its state of charge; the hourly Dirac-3
    subproblems are independent, so a naive per-hour optimum drains the
    battery early and sheds load late (observed and fixed in this build).
    Because the battery is the *most expensive* source (75 $/MWh degradation
    vs 55-60 $/MWh fuel), its true role is covering only the firm-capacity
    gap.  The master therefore allocates discharge to each hour's net load
    above firm capacity, scaled down proportionally if the usable stored
    energy cannot cover every gap.  Each hourly payload then receives an
    SOC-feasible discharge cap by construction — a classical-master /
    quantum-subproblem split in the decomposition pattern the challenge
    recommends.
    """
    caps = _window_caps(cand)
    firm = caps["p_mt"] + caps["p_lg"]
    soc = (soc0_mwh if soc0_mwh is not None else 0.5 * cand.der.bess_mwh)
    usable_mwh = max(0.0, soc) * eta          # MWh deliverable at the bus
    gaps = []
    for t in range(hours):
        h = window_start + t
        net = max(0.0, cand.base_load * scen.load_scale[h]
                  - cand.der.pv_mw * scen.pv_scale[h])
        gaps.append(min(max(0.0, net - firm), caps["p_dis"]))
    need = sum(gaps)
    if need <= 1e-9:
        return [0.0] * hours
    scale = min(1.0, usable_mwh / need)
    return [g * scale for g in gaps]


def build_hourly_dispatch_payload(cand: CandidateIsland, scen: Scenario,
                                  hour: int,
                                  device_R: float = 10.0,
                                  dis_cap_mw: float | None = None) -> "PolyProblem | None":
    """One in-spec Dirac-3 payload for one hour of islanded operation.

    Returns None for hours with non-positive net load (PV covers everything:
    nothing to optimise, surplus PV is curtailed).  Device unit u is chosen so
    the simplex budget R is a fixed, spec-safe constant (default 10, inside
    the documented [1, 10000] range) regardless of island size, which also
    fixes the relative solution resolution at R/200 = 0.5% of net load.

    dis_cap_mw, when given, is the classical master's SOC-feasible battery
    allocation for this hour (see allocate_bess_budget); it replaces the raw
    power rating in the capacity-wall calibration.
    """
    load_t = cand.base_load * scen.load_scale[hour]
    pv_t = cand.der.pv_mw * scen.pv_scale[hour]
    net = load_t - pv_t
    if net <= 1e-6:
        return None
    u = net / device_R                       # MW per device unit
    caps = dict(_window_caps(cand))
    if dis_cap_mw is not None:
        caps["p_dis"] = min(caps["p_dis"], max(0.0, dis_cap_mw))
        # a negligible master allocation (< 0.5% of net load) would need a
        # capacity wall steeper than the device's 200:1 coefficient range;
        # drop the variable instead — the disclosed classical repair serves
        # the sliver from true equipment headroom.
        if caps["p_dis"] < 0.005 * net:
            caps["p_dis"] = 0.0

    c1 = {"p_mt": 60.0, "p_lg": 55.0, "p_dis": 75.0}
    c2 = {"p_mt": 0.015, "p_lg": 0.012, "p_dis": 0.0}
    max_marg = max(c1[k] + 2.0 * c2[k] * caps[k] for k in c1 if caps[k] > 1e-6)
    K = DEVICE_SERVICE_REWARD_MARGIN * max_marg

    names = [k for k in ("p_mt", "p_lg", "p_dis") if caps[k] > 1e-6] + ["s_shed"]
    n = len(names)
    scales = np.array([u] * n)
    upper = np.array([caps.get(k, net) for k in names[:-1]] + [net])
    prob = PolyProblem(name=f"hdisp_c{cand.cid}_s{scen.sid}_h{hour}",
                       n_vars=n, terms={}, scales=scales, upper=upper,
                       sum_constraint=float(device_R), var_names=names,
                       meta={"kind": "hourly_dispatch", "encoding": "continuous",
                             "cid": cand.cid, "sid": scen.sid, "hour": int(hour),
                             "u_mw_per_unit": u, "net_load_mw": net,
                             "service_reward_K": K,
                             "caps_mw": {k: caps[k] for k in names[:-1]},
                             "note": ("simplex = exact hourly balance; "
                                      "K = device-side VoLL surrogate; "
                                      "quadratic walls encode caps; cubic fuel "
                                      "curvature restored classically")})
    for i, k in enumerate(names[:-1]):
        lin = (c1[k] - K) * u
        a2 = max(0.0, (K - c1[k]) / (2.0 * max(caps[k], 1e-6)) - c2[k])
        quad = (a2 + c2[k]) * u * u
        prob.add((i,), lin)
        prob.add((i, i), quad)
    # shed slack (last var) carries no cost on-device: choosing it over a
    # source forfeits the service reward, i.e. costs K - c_src per MWh.
    return prob


def build_window_dispatch_payload(cand: CandidateIsland, scen: Scenario,
                                  window_start: int, hours: int = 4,
                                  device_R: float = 40.0) -> PolyProblem:
    """Mid-rung payload: whole outage window on one simplex.

    Only the *total* window energy balance is machine-enforced (an energy-
    adequacy relaxation, disclosed); per-hour fills are shaped by the same
    calibrated quadratic walls.  This gives a 13-variable instance between the
    4-variable hourly payloads and the 121-variable probe, forming a
    documented exactness-vs-scale ladder for the device study.
    """
    caps = _window_caps(cand)
    net = [max(0.0, cand.base_load * scen.load_scale[window_start + t]
               - cand.der.pv_mw * scen.pv_scale[window_start + t])
           for t in range(hours)]
    total = max(sum(net), 1e-6)
    u = total / device_R
    c1 = {"p_mt": 60.0, "p_lg": 55.0, "p_dis": 75.0}
    c2 = {"p_mt": 0.015, "p_lg": 0.012, "p_dis": 0.0}
    max_marg = max(c1[k] + 2.0 * c2[k] * caps[k] for k in c1 if caps[k] > 1e-6)
    K = DEVICE_SERVICE_REWARD_MARGIN * max_marg

    names, scales, upper = [], [], []
    src = [k for k in ("p_mt", "p_lg", "p_dis") if caps[k] > 1e-6]
    for t in range(hours):
        for k in src:
            names.append(f"{k}[{t}]")
            scales.append(u)
            upper.append(caps[k])
    names.append("s_shed")
    scales.append(u)
    upper.append(total)
    prob = PolyProblem(name=f"wdisp_c{cand.cid}_s{scen.sid}_w{window_start}",
                       n_vars=len(names), terms={},
                       scales=np.array(scales), upper=np.array(upper),
                       sum_constraint=float(device_R), var_names=names,
                       meta={"kind": "window_dispatch", "encoding": "continuous",
                             "cid": cand.cid, "sid": scen.sid,
                             "window_start": int(window_start), "hours": hours,
                             "u_mw_per_unit": u, "net_load_mwh": total,
                             "hourly_net_mw": net, "service_reward_K": K,
                             "caps_mw": {k: caps[k] for k in src},
                             "note": "energy-adequacy relaxation (disclosed)"})
    for i, name in enumerate(names[:-1]):
        k = name.split("[")[0]
        lin = (c1[k] - K) * u
        a2 = max(0.0, (K - c1[k]) / (2.0 * max(caps[k], 1e-6)) - c2[k])
        prob.add((i,), lin)
        prob.add((i, i), (a2 + c2[k]) * u * u)
    return prob


def decode_simplex_dispatch(prob: PolyProblem, x: np.ndarray,
                            cand: CandidateIsland,
                            soc0_mwh: float | None = None) -> dict:
    """Decode device-unit simplex solutions into MW, with disclosed repair.

    Repair order: clamp each source at its physical cap, then cap cumulative
    BESS discharge at the usable stored energy; any residual becomes shed.
    Returns the repaired schedule slice plus repair diagnostics so the
    magnitude of the classical correction is always reported, never hidden.
    """
    meta = prob.meta
    u = meta["u_mw_per_unit"]
    eta = 0.92
    soc = (soc0_mwh if soc0_mwh is not None else 0.5 * cand.der.bess_mwh)
    if meta["kind"] == "hourly_dispatch":
        hours = 1
        nets = [meta["net_load_mw"]]
    else:
        hours = meta["hours"]
        nets = meta["hourly_net_mw"]
    caps = meta["caps_mw"]
    src = list(caps)
    vals = {name: float(v) * u for name, v in zip(prob.var_names, x)}
    sched = {k: np.zeros(hours) for k in ("p_mt", "p_lg", "p_dis", "p_ch", "l_shed")}
    clamp_mw = 0.0
    curtail_mwh = 0.0
    redispatch_mwh = 0.0
    merit = ["p_lg", "p_mt", "p_dis"]        # cheapest first (55/60/75 $/MWh)
    hard_caps = dict(_window_caps(cand))
    for t in range(hours):
        served = 0.0
        for k in src:
            key = k if hours == 1 else f"{k}[{t}]"
            raw = vals.get(key, 0.0)
            val = min(raw, caps[k])
            clamp_mw += max(0.0, raw - caps[k])
            if k == "p_dis":
                val = min(val, soc * eta)
                soc -= val / eta
            sched[k][t] = val
            served += val
        residual = nets[t] - served
        if residual > 1e-9:
            # merit-order redispatch into remaining PHYSICAL headroom before
            # declaring shed (the device saw the master's tighter walls; the
            # repair may use the true equipment limits, SOC included).
            for k in merit:
                if k not in hard_caps or residual <= 1e-9:
                    continue
                head = max(0.0, hard_caps[k] - sched[k][t])
                if k == "p_dis":
                    head = min(head, soc * eta)
                add = min(head, residual)
                if add > 0:
                    if k == "p_dis":
                        soc -= add / eta
                    sched[k][t] += add
                    residual -= add
                    redispatch_mwh += add
        sched["l_shed"][t] = max(0.0, residual)
        curtail_mwh += max(0.0, -residual) if residual < 0 else 0.0
    return {"sched": sched, "repair_clamped_mw": clamp_mw,
            "repair_curtailed_mwh": curtail_mwh,
            "repair_redispatch_mwh": redispatch_mwh,
            "soc_end_mwh": soc, "hours": hours,
            "shed_mwh": float(np.sum(sched["l_shed"]))}


# ---------------------------------------------------------------------------
# Stage 2a: islanding selection over pre-validated candidate blocks
# ---------------------------------------------------------------------------

def build_selection_hamiltonian(cands: list[CandidateIsland],
                                islanded_cost: dict[int, float],
                                exposure: dict[int, float],
                                overlap: np.ndarray,
                                lam_overlap: float = 1.0,
                                lam_bin: float = 1.0,
                                horizon_hours: int = 24) -> PolyProblem:
    n = len(cands)
    names = [f"y[{c.cid}]" for c in cands]
    prob = PolyProblem(name="islanding_selection", n_vars=n, terms={},
                       scales=np.ones(n), upper=np.ones(n),
                       sum_constraint=float(n), var_names=names,
                       meta={"kind": "selection", "encoding": "integer",
                             "num_levels": [2] * n})
    scale = max(max(exposure.values(), default=1.0), 1.0)
    for j, c in enumerate(cands):
        upgrade_share = c.der.upgrade_cost * horizon_hours / ANNUAL_HOURS
        # y_j*(V_j + A_j - U_j) + const U_j ; constant dropped (argmin invariant)
        prob.add((j,), (islanded_cost[c.cid] + upgrade_share
                        - exposure[c.cid]) / scale)
        prob.add((j, j), lam_bin)      # y^2 - y  penalty (with next line)
        prob.add((j,), -lam_bin)
    for i in range(n):
        for j in range(i + 1, n):
            if overlap[i, j] > 0:
                prob.add((i, j), lam_overlap * overlap[i, j]
                         * (max(exposure.values()) / scale))
    prob.meta["cost_scale"] = scale
    return prob
