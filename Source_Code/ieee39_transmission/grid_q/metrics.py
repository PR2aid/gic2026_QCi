"""Rubric metrics (Challenge Stage 3 aggregation).

Aggregates per-scenario results into the sponsor's three key performance
metrics: cost (expected operating + reliability cost, and upgrade cost),
maximum fraction of customers unserved per hour, and critical-infrastructure
outage hours.

The unserved-customer metric is reported under BOTH defensible definitions,
clearly labelled, so no reading of the sponsor metric is overstated:

  * max_local_fraction_unserved_per_hour — worst-case fraction of customers
    unserved within any single affected candidate-microgrid footprint
    (a locality/equity view: "somewhere, this share of customers is dark").
  * max_system_fraction_unserved_per_hour — load-weighted fraction of ALL
    system customers unserved in the worst hour (a system view).

Earlier drafts reported only the local definition, whose 1.0 value under a
full PCC-loss scenario could be misread as a system-wide blackout.
"""
from __future__ import annotations

import numpy as np


def aggregate(results: list[dict]) -> dict:
    """Each result: {prob, operating_cost, upgrade_cost, unserved_frac (24,),
    [unserved_load_frac (24,)], critical_outage_hours}."""
    p = np.array([r["prob"] for r in results])
    probability_sum = float(p.sum())
    if not np.isclose(probability_sum, 1.0, rtol=0.0, atol=1e-12):
        raise ValueError(
            "retained scenario probabilities must sum to one; "
            f"got {probability_sum:.17g}"
        )
    exp_cost = float(np.sum(p * np.array([r["operating_cost"] for r in results])))
    upgrade = float(np.mean([r["upgrade_cost"] for r in results]))
    max_unserved = float(max(np.max(r["unserved_frac"]) for r in results))
    crit_hours = float(np.sum(p * np.array([r["critical_outage_hours"]
                                            for r in results])))
    out = {
        "expected_operating_cost_$": round(exp_cost, 2),
        "upgrade_cost_annualised_$": round(upgrade, 2),
        "max_local_fraction_unserved_per_hour": round(max_unserved, 4),
        # kept under the legacy key for backward compatibility of the tables
        "max_fraction_unserved_per_hour": round(max_unserved, 4),
        "expected_critical_outage_hours": round(crit_hours, 3),
        "n_scenarios": len(results),
        "scenario_probability_sum": probability_sum,
    }
    if all("unserved_load_frac" in r for r in results):
        max_sys = float(max(np.max(r["unserved_load_frac"]) for r in results))
        out["max_system_fraction_unserved_per_hour"] = round(max_sys, 4)
    if all("voll_cost" in r for r in results):
        exp_voll = float(np.sum(p * np.array([r["voll_cost"] for r in results])))
        out["expected_reliability_voll_component_$"] = round(exp_voll, 2)
        out["expected_energy_component_$"] = round(exp_cost - exp_voll, 2)
    return out
