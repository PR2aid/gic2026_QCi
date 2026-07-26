"""Stage A: classical candidate-island generation (Challenge Stage 1).

Produces a set of *pre-validated connected* candidate microgrids so the
quantum stage selects among blocks (binary y_j per candidate) instead of
assigning individual buses.  This is the mechanism promised in Phase 2 that
guarantees island connectivity by construction and keeps the Dirac-3 variable
count inside the documented 135-variable rank-3 budget.

Method: greedy electrically-coherent agglomeration on the susceptance-weighted
graph (multi-seed BFS balanced by load), followed by a minimum-cost DER/BESS
siting rule so every candidate can serve its critical load when islanded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import numpy as np

from .network import Network


@dataclass
class DERPortfolio:
    microturbine_mw: float = 0.0     # dispatchable, cubic-cost analogue
    pv_mw: float = 0.0               # scenario-scaled, zero marginal cost
    bess_mw: float = 0.0             # power rating
    bess_mwh: float = 0.0            # energy rating
    upgrade_cost: float = 0.0        # $ (annualised)


@dataclass
class CandidateIsland:
    cid: int
    buses: list[int]
    pcc_lines: list[int]             # indices into net.lines crossing boundary
    base_load: float                 # MW
    critical_load: float             # MW
    der: DERPortfolio = field(default_factory=DERPortfolio)
    internal_gen_mw: float = 0.0     # legacy generation capacity inside

    def is_connected(self, net: Network) -> bool:
        bset = set(self.buses)
        adj = {b: set() for b in self.buses}
        for f, t, _, _ in net.lines:
            if f in bset and t in bset:
                adj[f].add(t)
                adj[t].add(f)
        seen, stack = {self.buses[0]}, [self.buses[0]]
        while stack:
            for nb in adj[stack.pop()]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return len(seen) == len(self.buses)


# Disclosed DER unit-cost assumptions (annualised $/MW or $/MWh); values are
# order-of-magnitude NREL-consistent synthetic parameters released with the code.
COST_MT_PER_MW = 90_000.0
COST_PV_PER_MW = 55_000.0
COST_BESS_PER_MW = 40_000.0
COST_BESS_PER_MWH = 25_000.0


def generate_candidates(net: Network, n_candidates: int = 14,
                        seed: int = 17,
                        target_sizes: tuple[int, int] = (2, 6),
                        ensure_load_coverage: bool = True) -> list[CandidateIsland]:
    """Greedy electrically coherent candidate generation with coverage repair.

    Seeds are load buses (largest first, then random). Each candidate grows by
    absorbing the neighbour with the strongest electrical coupling (1/x) until
    it reaches a target bus count. Candidates may overlap in the candidate
    pool (they are alternatives, not a partition); the Hamiltonian overlap
    penalty prevents simultaneous selection of overlapping blocks.

    Phase 3 explicitly asks that the *design* cover all customers.  Therefore
    the default final pass repairs coverage by adding the shortest grid path
    from any uncovered load bus to its nearest candidate.  This keeps every
    block connected by construction while making the Stage-A design auditable:
    every positive-load bus is in at least one candidate microgrid.
    """
    rng = np.random.default_rng(seed)
    adj_w: dict[int, dict[int, float]] = {b: {} for b in net.buses}
    for f, t, x, _ in net.lines:
        w = 1.0 / max(x, 1e-6)
        adj_w[f][t] = max(adj_w[f].get(t, 0.0), w)
        adj_w[t][f] = max(adj_w[t].get(f, 0.0), w)

    load_buses = sorted(net.loads, key=net.loads.get, reverse=True)
    seeds = load_buses[:n_candidates]
    while len(seeds) < n_candidates:
        seeds.append(int(rng.choice(net.buses)))

    gen_by_bus: dict[int, float] = {}
    for g in net.generators:
        gen_by_bus[g.bus] = gen_by_bus.get(g.bus, 0.0) + g.pmax

    line_index = {(f, t): k for k, (f, t, _, _) in enumerate(net.lines)}

    def make_candidate(cid: int, block: list[int]) -> CandidateIsland:
        # preserve discovery order but remove duplicates
        block = list(dict.fromkeys(block))
        bset = set(block)
        pcc = [k for (f, t), k in line_index.items()
               if (f in bset) != (t in bset)]
        base_load = sum(net.loads.get(b, 0.0) for b in block)
        crit_load = sum(net.loads.get(b, 0.0) for b in block
                        if b in net.critical_buses)
        cand = CandidateIsland(cid=cid, buses=block, pcc_lines=pcc,
                               base_load=base_load, critical_load=crit_load,
                               internal_gen_mw=sum(gen_by_bus.get(b, 0.0)
                                                   for b in block))
        assert cand.is_connected(net), "candidate must be connected by construction"
        cand.der = _site_resources(cand)
        return cand

    candidates = []
    for cid, seed_bus in enumerate(seeds):
        size = int(rng.integers(target_sizes[0], target_sizes[1] + 1))
        block = [seed_bus]
        bset = {seed_bus}
        while len(block) < size:
            frontier = {}
            for b in block:
                for nb, w in adj_w[b].items():
                    if nb not in bset:
                        frontier[nb] = frontier.get(nb, 0.0) + w
            if not frontier:
                break
            nxt = max(frontier, key=frontier.get)
            block.append(nxt)
            bset.add(nxt)
        candidates.append(make_candidate(cid, block))

    if ensure_load_coverage:
        candidates = _repair_load_coverage(net, candidates, make_candidate)
    return candidates


def generate_partition_candidates(net: Network,
                                  n_candidates: int = 14) -> list[CandidateIsland]:
    """Build a deterministic connected partition for the planning master.

    The hardware-dispatch benchmark intentionally retains the original
    overlapping candidate pool produced by :func:`generate_candidates`.
    System-level planning requires a different object: each bus, and therefore
    each positive-load customer bus, must have exactly one owner.  This
    routine grows an electrically weighted multi-source Voronoi partition from
    the largest load buses.  Ownership propagates along the graph, so every
    block is connected; the blocks are disjoint and cover the complete network.

    Candidate IDs are stable seed ranks (largest load first).  The resulting
    candidates can therefore be selected once in a two-stage stochastic master
    without overlap, load, upgrade-cost, or outage-cost double counting.
    """
    load_buses = sorted(net.loads, key=lambda b: (-net.loads[b], b))
    if not 1 <= n_candidates <= len(load_buses):
        raise ValueError(
            "n_candidates must be between 1 and the number of load buses "
            f"({len(load_buses)}), got {n_candidates}"
        )
    seeds = load_buses[:n_candidates]

    # Electrical distance is the sum of positive branch reactances.  The
    # (distance, seed-rank) tuple makes every tie deterministic.
    adjacency: dict[int, list[tuple[int, float]]] = {b: [] for b in net.buses}
    for f, t, x, _ in net.lines:
        length = max(abs(float(x)), 1e-6)
        adjacency[f].append((t, length))
        adjacency[t].append((f, length))
    for b in adjacency:
        adjacency[b].sort()

    best: dict[int, tuple[float, int]] = {
        b: (float("inf"), n_candidates) for b in net.buses
    }
    queue: list[tuple[float, int, int]] = []
    for cid, bus in enumerate(seeds):
        best[bus] = (0.0, cid)
        heapq.heappush(queue, (0.0, cid, bus))

    while queue:
        dist, cid, bus = heapq.heappop(queue)
        if (dist, cid) != best[bus]:
            continue
        for neighbour, length in adjacency[bus]:
            proposal = (dist + length, cid)
            if proposal < best[neighbour]:
                best[neighbour] = proposal
                heapq.heappush(queue, (proposal[0], cid, neighbour))

    unassigned = [b for b, (dist, _) in best.items() if not np.isfinite(dist)]
    if unassigned:
        raise RuntimeError(f"network partition failed; unassigned buses {unassigned}")

    gen_by_bus: dict[int, float] = {}
    for g in net.generators:
        gen_by_bus[g.bus] = gen_by_bus.get(g.bus, 0.0) + g.pmax

    candidates: list[CandidateIsland] = []
    for cid in range(n_candidates):
        buses = [b for b in net.buses if best[b][1] == cid]
        bset = set(buses)
        pcc = [
            k for k, (f, t, _, _) in enumerate(net.lines)
            if (f in bset) != (t in bset)
        ]
        cand = CandidateIsland(
            cid=cid,
            buses=buses,
            pcc_lines=pcc,
            base_load=sum(net.loads.get(b, 0.0) for b in buses),
            critical_load=sum(
                net.loads.get(b, 0.0)
                for b in buses if b in net.critical_buses
            ),
            internal_gen_mw=sum(gen_by_bus.get(b, 0.0) for b in buses),
        )
        if not cand.is_connected(net):
            raise RuntimeError(f"partition candidate {cid} is not connected")
        cand.der = _site_resources(cand)
        candidates.append(cand)

    ownership = {
        b: sum(b in c.buses for c in candidates) for b in net.buses
    }
    bad = {b: count for b, count in ownership.items() if count != 1}
    if bad:
        raise RuntimeError(f"partition ownership is not exactly one: {bad}")
    return candidates


def _repair_load_coverage(net: Network, candidates: list[CandidateIsland],
                          make_candidate) -> list[CandidateIsland]:
    """Add shortest paths to candidates until every positive-load bus is covered."""
    adj = net.adjacency()

    def shortest_path_to_set(start: int, targets: set[int]) -> list[int]:
        from collections import deque
        q = deque([start])
        parent = {start: None}
        hit = None
        while q:
            b = q.popleft()
            if b in targets:
                hit = b
                break
            for nb in sorted(adj[b]):
                if nb not in parent:
                    parent[nb] = b
                    q.append(nb)
        if hit is None:
            return [start]
        path = []
        cur = hit
        while cur is not None:
            path.append(cur)
            cur = parent[cur]
        return list(reversed(path))  # target -> ... -> start

    load_buses = set(net.loads)
    # Assign larger loads first so major customers get the nearest repair.
    for bus in sorted(load_buses, key=lambda b: net.loads[b], reverse=True):
        covered = set().union(*(c.buses for c in candidates))
        if bus in covered:
            continue
        best = None
        for j, c in enumerate(candidates):
            path = shortest_path_to_set(bus, set(c.buses))
            score = (len(path), c.base_load, j)
            if best is None or score < best[0]:
                best = (score, j, path)
        _, j, path = best
        new_block = candidates[j].buses + path
        candidates[j] = make_candidate(candidates[j].cid, new_block)

    missing = load_buses - set().union(*(c.buses for c in candidates))
    if missing:
        raise RuntimeError(f"coverage repair failed; missing load buses {sorted(missing)}")
    return candidates


def _site_resources(c: CandidateIsland) -> DERPortfolio:
    """Minimum-cost firm-capacity rule for the Phase-3 resilience definition.

    The challenge defines islanding feasibility as every customer belonging to
    at least one microgrid and every microgrid being able to serve its *base
    load* while separated from the utility.  Therefore the firm component is
    sized to base load, not only critical load.  PV and a 4-hour BESS are added
    as operating-cost reducers; because they may be unavailable at a zero-PV
    peak, they are not counted toward the firm guarantee.  Given the disclosed
    cost model, this is the closed-form minimum firm microturbine capacity.
    """
    firm = max(0.0, 1.05 * c.base_load - 0.5 * c.internal_gen_mw)
    mt = firm
    pv = 0.4 * c.base_load        # nameplate; scenario-scaled at dispatch
    bess_mw = 0.3 * c.base_load
    bess_mwh = 4.0 * bess_mw
    cost = (mt * COST_MT_PER_MW + pv * COST_PV_PER_MW
            + bess_mw * COST_BESS_PER_MW + bess_mwh * COST_BESS_PER_MWH)
    return DERPortfolio(microturbine_mw=mt, pv_mw=pv, bess_mw=bess_mw,
                        bess_mwh=bess_mwh, upgrade_cost=cost)


def overlap_matrix(cands: list[CandidateIsland]) -> np.ndarray:
    n = len(cands)
    O = np.zeros((n, n))
    sets = [set(c.buses) for c in cands]
    for i in range(n):
        for j in range(i + 1, n):
            inter = len(sets[i] & sets[j])
            if inter:
                O[i, j] = O[j, i] = inter / min(len(sets[i]), len(sets[j]))
    return O
