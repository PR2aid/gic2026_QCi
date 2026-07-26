"""Stage B: uncertainty compression.

Reproducible public scenario generator (load variation, renewable forecast
error, N-1 transmission contingencies, PCC tie-line loss, local generator
failure) followed by regime clustering with an explicit CVaR-aware
tail-retention rule so resilience-critical scenarios are never averaged away.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from .network import Network

HOURS = 24
# canonical normalised daily load shape (per-unit of peak)
_BASE_SHAPE = np.array([
    .62, .58, .56, .55, .56, .60, .68, .78, .86, .90, .92, .93,
    .93, .92, .91, .92, .95, 1.0, .99, .96, .90, .82, .74, .67])
_PV_SHAPE = np.clip(np.sin(np.pi * (np.arange(24) - 6) / 12.), 0, None)


@dataclass
class Scenario:
    sid: int
    load_scale: np.ndarray          # (24,) multiplicative on base load
    pv_scale: np.ndarray            # (24,) per-unit of PV nameplate
    contingency: str                # 'none' | 'line:<k>' | 'pcc:<cid>' | 'gen:<g>'
    prob: float
    severity: float = 0.0           # filled by score_severity
    tags: list[str] = field(default_factory=list)


def generate_scenarios(net: Network, n: int = 200, seed: int = 17,
                       n1_lines: int | None = None,
                       n_candidates: int | None = None) -> list[Scenario]:
    """Generate seeded scenarios over the complete declared candidate domain.

    ``n_candidates`` is required rather than inferred or hardcoded.  This is a
    fail-closed guard: PCC contingencies must be sampled from every valid
    candidate identifier in the planning instance.
    """
    if n_candidates is None or n_candidates <= 0:
        raise ValueError("n_candidates must be supplied and positive")
    rng = np.random.default_rng(seed)
    scens = []
    n1_pool = list(range(len(net.lines)))
    if n1_lines:
        n1_pool = n1_pool[:n1_lines]
    for i in range(n):
        load_level = rng.normal(1.0, 0.08)
        load = np.clip(_BASE_SHAPE * load_level
                       + rng.normal(0, 0.02, HOURS), 0.4, 1.3)
        cloud = rng.beta(2, 2)  # 0=overcast, 1=clear
        pv = np.clip(_PV_SHAPE * cloud + rng.normal(0, 0.05, HOURS), 0, 1)
        r = rng.random()
        if r < 0.55:
            cont = "none"
        elif r < 0.80:
            cont = f"line:{int(rng.choice(n1_pool))}"
        elif r < 0.92:
            cont = f"pcc:{int(rng.integers(0, n_candidates))}"
        else:
            cont = f"gen:{int(rng.integers(0, len(net.generators)))}"
        scens.append(Scenario(sid=i, load_scale=load, pv_scale=pv,
                              contingency=cont, prob=1.0 / n))
    return scens


def score_severity(net: Network, scens: list[Scenario]) -> None:
    """Cheap physics-informed severity proxy: peak net load stress plus a
    contingency weight.  Used only to decide which tail scenarios must be
    retained explicitly; the optimizer re-evaluates everything downstream."""
    total_load = net.total_load()
    for s in scens:
        stress = float(np.max(s.load_scale)) - 0.4 * float(np.mean(s.pv_scale))
        w = {"none": 0.0, "line": 0.5, "pcc": 0.8, "gen": 0.7}[s.contingency.split(":")[0]]
        s.severity = stress + w


def compress(scens: list[Scenario], n_regimes: int = 6, cvar_alpha: float = 0.95,
             seed: int = 17) -> tuple[list[Scenario], list[Scenario]]:
    """k-means regime clustering on (load shape, pv shape, contingency onehot)
    + explicit retention of the CVaR_alpha severity tail.

    Returns (regime_representatives, retained_tail).  Representatives carry
    the aggregated probability of their cluster; tail scenarios keep their own
    probability and are removed from the clusters they came from.
    """
    rng = np.random.default_rng(seed)
    sev = np.array([s.severity for s in scens])
    thresh = np.quantile(sev, cvar_alpha)
    tail = [s for s in scens if s.severity >= thresh]
    body = [s for s in scens if s.severity < thresh]
    for s in tail:
        s.tags.append("cvar_tail")

    cont_kinds = ["none", "line", "pcc", "gen"]
    feats = np.array([
        np.concatenate([s.load_scale, s.pv_scale,
                        [3.0 * (s.contingency.split(":")[0] == k) for k in cont_kinds]])
        for s in body])
    # plain k-means (numpy, seeded, 25 iterations)
    centroids = feats[rng.choice(len(feats), n_regimes, replace=False)]
    for _ in range(25):
        d = ((feats[:, None, :] - centroids[None]) ** 2).sum(-1)
        lab = d.argmin(1)
        for k in range(n_regimes):
            if (lab == k).any():
                centroids[k] = feats[lab == k].mean(0)
    reps = []
    for k in range(n_regimes):
        members = [b for b, l in zip(body, lab) if l == k]
        if not members:
            continue
        d_k = ((feats[lab == k] - centroids[k]) ** 2).sum(-1)
        rep = members[int(np.argmin(d_k))]
        rep = Scenario(sid=rep.sid, load_scale=rep.load_scale,
                       pv_scale=rep.pv_scale, contingency=rep.contingency,
                       prob=sum(m.prob for m in members),
                       severity=rep.severity, tags=[f"regime:{k}"])
        reps.append(rep)
    retained_mass = sum(s.prob for s in reps) + sum(s.prob for s in tail)
    original_mass = sum(s.prob for s in scens)
    if not np.isclose(retained_mass, original_mass, rtol=0.0, atol=1e-12):
        raise RuntimeError(
            "scenario compression lost probability mass: "
            f"retained={retained_mass:.17g}, original={original_mass:.17g}"
        )
    return reps, tail
