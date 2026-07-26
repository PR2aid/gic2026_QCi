"""Auditable three-stage resilient-microgrid model for QCi Dirac-3.

The public instance aggregates MATPOWER/Baran-Wu case33bw into four connected
sections. Synthetic resource prices, event rates and critical labels are fully
disclosed; they are demonstration assumptions, not utility data or advice.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .polynomial import Polynomial

CRF = 0.08
OUTAGE_HOURS = 4.0
LOSS_DERATING = 1.06
SWITCH_UPGRADE_COST_USD = 180_000.0
BLACKSTART_UPGRADE_COST_USD = 420_000.0
VOLL_CRITICAL_USD_PER_MWH = 10_000.0
VOLL_FIRM_USD_PER_MWH = 2_500.0
DESIGN_PENALTY_MULTIPLE = 3.0
ISLANDING_REGULARIZER_USD = 100.0
STAGE3B_NUM_LEVELS = 3

# Synthetic operating-wear/conversion curves. Coefficient dimensions are
# USD/MWh, USD/(MW^2 h), and USD/(MW^3 h), respectively.
PRIMARY_C1, PRIMARY_C2, PRIMARY_C3 = 65.0, 42.0, 18.0
BLACKSTART_C1, BLACKSTART_C2, BLACKSTART_C3 = 95.0, 60.0, 25.0

DESIGN_RISK_VALUE_USD_PER_MWH = {
    "cost_efficient": 0.0,
    "balanced_critical": 30_000.0,
    "robust_critical": 75_000.0,
}
DESIGN_LABELS = {
    "cost_efficient": "Plan A - Cost-efficient",
    "balanced_critical": "Plan B - Balanced-critical",
    "robust_critical": "Plan C - Robust-critical",
}


@dataclass(frozen=True)
class Product:
    name: str
    p_mw: float
    e_mwh: float
    capex_usd: float

    @property
    def island_capacity_mw(self) -> float:
        return min(self.p_mw, self.e_mwh / OUTAGE_HOURS) / LOSS_DERATING


@dataclass(frozen=True)
class Candidate:
    name: str
    buses: tuple[int, ...]
    pcc: int
    load_mw: float
    critical_load_mw: float
    customer_blocks: int
    service_id: str
    role: str = "primary"
    unavailable_in_contingencies: tuple[str, ...] = ()

    @property
    def upgrade_cost_usd(self) -> float:
        return (BLACKSTART_UPGRADE_COST_USD if self.role == "blackstart_overlap"
                else SWITCH_UPGRADE_COST_USD)


@dataclass(frozen=True)
class Contingency:
    name: str
    duration_h: float
    event_rate_per_year: float


PRODUCTS = (
    Product("1MW_4MWh_BESS_PV_HYBRID_DER", 1.0, 4.0, 2_600_000.0),
    Product("2MW_8MWh_BESS_PV_HYBRID_DER", 2.0, 8.0, 4_900_000.0),
)

PRIMARY_CANDIDATES = (
    Candidate("MG_trunk_1_17", tuple(range(1, 18)), 1, 1.505, 0.0, 17,
              "MG_trunk_1_17"),
    Candidate("MG_lateral_18_21", (18, 19, 20, 21), 18, 0.360, 0.0, 4,
              "MG_lateral_18_21"),
    Candidate("MG_lateral_22_24", (22, 23, 24), 22, 0.930, 0.840, 3,
              "MG_lateral_22_24", unavailable_in_contingencies=(
                  "lateral_22_24_fault", "compound_two_lateral_fault")),
    Candidate("MG_lateral_25_32", tuple(range(25, 33)), 25, 0.920, 0.210, 8,
              "MG_lateral_25_32", unavailable_in_contingencies=(
                  "lateral_25_32_fault", "compound_two_lateral_fault")),
)

OVERLAP_CANDIDATES = (
    Candidate("MG_blackstart_22_24", (22, 23, 24), 23, 0.930, 0.840, 3,
              "MG_lateral_22_24", role="blackstart_overlap"),
    Candidate("MG_blackstart_25_32", tuple(range(25, 33)), 29, 0.920, 0.210, 8,
              "MG_lateral_25_32", role="blackstart_overlap"),
)

CONTINGENCIES = (
    Contingency("upstream_PCC_outage", 4.0, 12.0),
    Contingency("lateral_22_24_fault", 4.0, 4.0),
    Contingency("lateral_25_32_fault", 4.0, 4.0),
    Contingency("compound_two_lateral_fault", 4.0, 1.0),
)

TOTAL_LOAD_MW = sum(c.load_mw for c in PRIMARY_CANDIDATES)
TOTAL_CUSTOMER_BLOCKS = sum(c.customer_blocks for c in PRIMARY_CANDIDATES)
TOTAL_CRITICAL_LOAD_MW = sum(c.critical_load_mw for c in PRIMARY_CANDIDATES)
CRITICAL_SERVICE_IDS = tuple(c.service_id for c in PRIMARY_CANDIDATES
                             if c.critical_load_mw > 0)


def candidates_for_mode(mode: str) -> list[Candidate]:
    if mode == "cost_efficient":
        return list(PRIMARY_CANDIDATES)
    if mode in {"balanced_critical", "robust_critical"}:
        return list(PRIMARY_CANDIDATES + OVERLAP_CANDIDATES)
    raise ValueError(f"unknown design mode {mode!r}")


def feasible_items(candidates: list[Candidate]) -> tuple[list[dict], dict[tuple[int, int], int]]:
    items: list[dict] = []
    index: dict[tuple[int, int], int] = {}
    for candidate_index, candidate in enumerate(candidates):
        for product_index, product in enumerate(PRODUCTS):
            if product.island_capacity_mw + 1e-9 < candidate.load_mw:
                continue
            index[(candidate_index, product_index)] = len(items)
            upfront = product.capex_usd + candidate.upgrade_cost_usd
            items.append({
                "candidate_index": candidate_index,
                "candidate": candidate.name,
                "service_id": candidate.service_id,
                "role": candidate.role,
                "buses": list(candidate.buses),
                "pcc": candidate.pcc,
                "product_index": product_index,
                "product": product.name,
                "product_power_mw": product.p_mw,
                "product_energy_mwh": product.e_mwh,
                "upfront_cost_usd": upfront,
                "annualized_cost_usd_yr": CRF * upfront,
                "capacity_mw_loss_derated": product.island_capacity_mw,
                "load_mw": candidate.load_mw,
                "critical_load_mw": candidate.critical_load_mw,
                "customer_blocks": candidate.customer_blocks,
                "unavailable_in_contingencies":
                    list(candidate.unavailable_in_contingencies),
            })
    return items, index


def _add_exactly_one(poly: Polynomial, variables: list[int], weight: float) -> None:
    poly.add_square_of_affine({v: 1.0 for v in variables}, -1.0, weight)


def _add_at_most_one(poly: Polynomial, variables: list[int], weight: float) -> None:
    for position, left in enumerate(variables):
        for right in variables[position + 1:]:
            poly.add([left, right], weight)


def _primary_for_service(service_id: str) -> Candidate:
    return next(c for c in PRIMARY_CANDIDATES if c.service_id == service_id)


def _primary_unavailable(service_id: str, contingency: Contingency) -> bool:
    return contingency.name in _primary_for_service(service_id).unavailable_in_contingencies


def build_stage1_design_hamiltonian(mode: str) -> tuple[Polynomial, list[dict], dict]:
    """Annualized cost-risk planning Hamiltonian, in USD/year."""
    candidates = candidates_for_mode(mode)
    items, index = feasible_items(candidates)
    poly = Polynomial(len(items), max_degree=2)
    for variable, item in enumerate(items):
        poly.add([variable], item["annualized_cost_usd_yr"])
    max_annual = max(item["annualized_cost_usd_yr"] for item in items)
    design_penalty = DESIGN_PENALTY_MULTIPLE * max_annual
    for candidate_index, candidate in enumerate(candidates):
        variables = [index[(candidate_index, p)] for p in range(len(PRODUCTS))
                     if (candidate_index, p) in index]
        if candidate.role == "primary":
            _add_exactly_one(poly, variables, design_penalty)
        else:
            _add_at_most_one(poly, variables, design_penalty)

    beta = DESIGN_RISK_VALUE_USD_PER_MWH[mode]
    risk_terms = []
    if beta > 0:
        for candidate_index, candidate in enumerate(candidates):
            if candidate.role != "blackstart_overlap":
                continue
            variables = [index[(candidate_index, p)] for p in range(len(PRODUCTS))
                         if (candidate_index, p) in index]
            for contingency in CONTINGENCIES:
                if not _primary_unavailable(candidate.service_id, contingency):
                    continue
                mwh_per_event = candidate.critical_load_mw * contingency.duration_h
                expected_mwh_yr = mwh_per_event * contingency.event_rate_per_year
                weight = beta * expected_mwh_yr
                poly.add_square_of_affine({v: 1.0 for v in variables}, -1.0, weight)
                risk_terms.append({
                    "service_id": candidate.service_id,
                    "scenario": contingency.name,
                    "critical_mwh_per_event": mwh_per_event,
                    "event_rate_per_year": contingency.event_rate_per_year,
                    "expected_critical_mwh_per_year": expected_mwh_yr,
                    "risk_weight_usd_per_year": weight,
                })
    metadata = {
        "stage": "stage1_microgrid_design",
        "design_mode": mode,
        "design_label": DESIGN_LABELS[mode],
        "objective": ("annualized upgrade cost [USD/year] + beta [USD/MWh] * "
                      "frequency-weighted critical energy not served [MWh/year]"),
        "objective_units": "USD/year",
        "critical_risk_value_beta_usd_per_mwh": beta,
        "design_penalty_usd_per_year": design_penalty,
        "risk_terms": risk_terms,
        "products": [asdict(p) for p in PRODUCTS],
        "candidates": [asdict(c) for c in candidates],
    }
    return poly, items, metadata


def decode_design(solution: Iterable[int], items: list[dict]) -> dict:
    selected = [item for value, item in zip(solution, items)
                if int(round(float(value))) == 1]
    served: dict[str, dict] = {}
    for item in selected:
        served.setdefault(item["service_id"], item)
    load = sum(item["load_mw"] for item in served.values())
    critical = sum(item["critical_load_mw"] for item in served.values())
    blocks = sum(item["customer_blocks"] for item in served.values())
    return {
        "selected": selected,
        "selected_primary_count": sum(i["role"] == "primary" for i in selected),
        "selected_blackstart_overlap_count":
            sum(i["role"] == "blackstart_overlap" for i in selected),
        "selected_service_ids": sorted(served),
        "upfront_cost_usd": sum(i["upfront_cost_usd"] for i in selected),
        "annualized_cost_usd_yr":
            sum(i["annualized_cost_usd_yr"] for i in selected),
        "load_served_mw": load,
        "critical_load_served_mw": critical,
        "customer_blocks_served": blocks,
        "load_fraction_served": load / TOTAL_LOAD_MW,
        "critical_fraction_served": critical / TOTAL_CRITICAL_LOAD_MW,
        "customer_fraction_served": blocks / TOTAL_CUSTOMER_BLOCKS,
    }


def critical_only_baseline() -> dict:
    selected = []
    for candidate in PRIMARY_CANDIDATES:
        if candidate.critical_load_mw <= 0:
            continue
        product = min((p for p in PRODUCTS if p.island_capacity_mw >= candidate.load_mw),
                      key=lambda p: p.capex_usd)
        selected.append((candidate, product))
    upfront = sum(p.capex_usd + SWITCH_UPGRADE_COST_USD for c, p in selected)
    return {
        "name": "critical_only_greedy_baseline",
        "upfront_cost_usd": upfront,
        "annualized_cost_usd_yr": CRF * upfront,
        "customer_fraction_served":
            sum(c.customer_blocks for c, _ in selected) / TOTAL_CUSTOMER_BLOCKS,
        "feasible_all_customer": False,
    }


def _item_unavailable(item: dict, contingency: Contingency) -> bool:
    return contingency.name in item.get("unavailable_in_contingencies", [])


def build_stage2_islanding_hamiltonian(design: dict, contingency: Contingency,
                                       mode: str) -> tuple[Polynomial, list[dict], dict]:
    """Per-event island activation Hamiltonian with explicit overlap terms.

    Faulted/unavailable candidates are removed from the variable domain before
    the polynomial is built.  They are physical impossibilities in the stated
    contingency, not soft preferences, so spending analog precision on a large
    exclusion penalty would be both weaker and less auditable.
    """
    selected_all = design["selected"]
    selected = [item for item in selected_all
                if not _item_unavailable(item, contingency)]
    excluded = [item for item in selected_all
                if _item_unavailable(item, contingency)]
    poly = Polynomial(len(selected), max_degree=2)
    values = [contingency.duration_h * (
        VOLL_FIRM_USD_PER_MWH * item["load_mw"]
        + (VOLL_CRITICAL_USD_PER_MWH - VOLL_FIRM_USD_PER_MWH)
        * item["critical_load_mw"]
    ) for item in selected]
    for variable, item in enumerate(selected):
        poly.add([variable], -values[variable] + ISLANDING_REGULARIZER_USD)
    overlap_terms = []
    for service_id in sorted({item["service_id"] for item in selected}):
        group = [i for i, item in enumerate(selected)
                 if item["service_id"] == service_id]
        if len(group) > 1:
            # Activating a duplicate can improve the linear objective by at
            # most the largest service value; +1,000 makes at-most-one strict.
            weight = max(values[i] for i in group) + 1_000.0
            _add_at_most_one(poly, group, weight)
            overlap_terms.append({"service_id": service_id,
                                  "variables": group, "weight_usd": weight})
    metadata = {
        "stage": "stage2_contingency_islanding",
        "design_mode": mode,
        "contingency": asdict(contingency),
        "hamiltonian": ("-sum_i(value_i-regularizer)*a_i "
                        "+ sum_over_overlaps(M_ij*a_i*a_j); faulted candidates "
                        "are structurally excluded from the variable domain"),
        "islanding_value_usd_per_event":
            {selected[i]["candidate"]: values[i] for i in range(len(selected))},
        "overlap_terms": overlap_terms,
        "structurally_excluded_unavailable_candidates": [
            {"candidate": item["candidate"], "service_id": item["service_id"],
             "role": item["role"]} for item in excluded
        ],
        "variables": [{"var": i, "candidate": item["candidate"],
                       "service_id": item["service_id"], "role": item["role"],
                       "unavailable": False}
                      for i, item in enumerate(selected)],
    }
    return poly, selected, metadata


def decode_islanding(solution: Iterable[int], selected: list[dict],
                      contingency: Contingency) -> dict:
    active = []
    served: dict[str, dict] = {}
    for value, item in zip(solution, selected):
        if int(round(float(value))) == 1 and not _item_unavailable(item, contingency):
            if item["service_id"] not in served:
                served[item["service_id"]] = item
                active.append(item)
    load = sum(item["load_mw"] for item in served.values())
    critical = sum(item["critical_load_mw"] for item in served.values())
    blocks = sum(item["customer_blocks"] for item in served.values())
    return {
        "active_islands": active,
        "active_service_ids": sorted(served),
        "load_served_mw": load,
        "critical_load_served_mw": critical,
        "customer_blocks_served": blocks,
        "load_fraction_served": load / TOTAL_LOAD_MW,
        "critical_fraction_served": critical / TOTAL_CRITICAL_LOAD_MW,
        "customer_fraction_served": blocks / TOTAL_CUSTOMER_BLOCKS,
        "max_customer_fraction_unserved": 1.0 - blocks / TOTAL_CUSTOMER_BLOCKS,
        "load_fraction_unserved": 1.0 - load / TOTAL_LOAD_MW,
        "critical_unserved_mwh": (TOTAL_CRITICAL_LOAD_MW - critical)
                                   * contingency.duration_h,
    }


def two_unit_pockets(design: dict, contingency: Contingency) -> list[dict]:
    by_service: dict[str, list[dict]] = {}
    for item in design["selected"]:
        if not _item_unavailable(item, contingency):
            by_service.setdefault(item["service_id"], []).append(item)
    return [{"service_id": service_id, "load_mw": units[0]["load_mw"],
             "units": units}
            for service_id, units in sorted(by_service.items()) if len(units) == 2]


def _curve(role: str) -> tuple[float, float, float]:
    return ((BLACKSTART_C1, BLACKSTART_C2, BLACKSTART_C3)
            if role == "blackstart_overlap"
            else (PRIMARY_C1, PRIMARY_C2, PRIMARY_C3))


def _add_cubic_of_affine(poly: Polynomial, variable: int, offset: float,
                         slope: float, duration: float,
                         c1: float, c2: float, c3: float) -> None:
    poly.constant += duration * (c3 * offset**3 + c2 * offset**2 + c1 * offset)
    poly.add([variable], duration * (
        3*c3*offset*offset*slope + 2*c2*offset*slope + c1*slope))
    poly.add([variable, variable], duration * (
        3*c3*offset*slope*slope + c2*slope*slope))
    poly.add([variable, variable, variable], duration * c3 * slope**3)


def build_stage3b_exact_balance_hamiltonian(pocket: dict,
                                            contingency: Contingency,
                                            mode: str) -> tuple[Polynomial, list[dict], dict, list[int]]:
    """One native qudit; power balance is eliminated algebraically."""
    primary = next(item for item in pocket["units"] if item["role"] == "primary")
    backup = next(item for item in pocket["units"]
                  if item["role"] == "blackstart_overlap")
    load = float(pocket["load_mw"])
    step = load / 3.0
    poly = Polynomial(1, max_degree=3, binary_simplify=False)
    _add_cubic_of_affine(poly, 0, step, step, contingency.duration_h,
                         *_curve(primary["role"]))
    _add_cubic_of_affine(poly, 0, 2*step, -step, contingency.duration_h,
                         *_curve(backup["role"]))
    levels = {str(z): {
        "primary_share": (z + 1) / 3,
        "backup_share": (2 - z) / 3,
        "primary_dispatch_mw": load * (z + 1) / 3,
        "backup_dispatch_mw": load * (2 - z) / 3,
        "total_dispatch_mw": load,
    } for z in range(3)}
    variables = [{
        "var": 0, "service_id": pocket["service_id"], "pocket_load_mw": load,
        "primary_candidate": primary["candidate"],
        "backup_candidate": backup["candidate"],
        "primary_capacity_mw": primary["capacity_mw_loss_derated"],
        "backup_capacity_mw": backup["capacity_mw_loss_derated"],
        "primary_unit_kind": "primary_bess_pv_hybrid_der",
        "backup_unit_kind": "blackstart_bess_pv_overlay",
        "levels": levels,
    }]
    audit = poly.coefficient_resolution_audit()
    metadata = {
        "stage": "stage3b_exact_balance_qudit_cubic",
        "design_mode": mode,
        "contingency": asdict(contingency),
        "service_id": pocket["service_id"],
        "variables": variables,
        "num_levels": [3],
        "balance_encoding": ("z in {0,1,2}; primary=(z+1)L/3; "
                             "backup=(2-z)L/3"),
        "exact_balance_by_substitution": True,
        "minimum_primary_share": 1/3,
        "balance_penalty": None,
        "polynomial_rank": 3,
        "coefficient_resolution_audit": audit,
        "recommended_for_hardware": audit["pass"],
    }
    return poly, variables, metadata, [STAGE3B_NUM_LEVELS]


def decode_stage3b(solution: Iterable[int], variables: list[dict]) -> dict:
    values = list(solution)
    if len(values) != 1 or len(variables) != 1:
        raise ValueError("Stage3B expects one qudit")
    raw = float(values[0]); z = int(round(raw))
    if abs(raw - z) > 1e-8 or z not in range(3):
        raise ValueError(f"invalid Stage3B level {raw}")
    meta = variables[0]; level = meta["levels"][str(z)]
    dispatch = {
        meta["primary_candidate"]: {
            "unit_kind": meta["primary_unit_kind"],
            "share_of_load": level["primary_share"],
            "dispatch_mw": level["primary_dispatch_mw"],
            "within_capacity": level["primary_dispatch_mw"] <= meta["primary_capacity_mw"] + 1e-9,
        },
        meta["backup_candidate"]: {
            "unit_kind": meta["backup_unit_kind"],
            "share_of_load": level["backup_share"],
            "dispatch_mw": level["backup_dispatch_mw"],
            "within_capacity": level["backup_dispatch_mw"] <= meta["backup_capacity_mw"] + 1e-9,
        },
    }
    total = sum(item["dispatch_mw"] for item in dispatch.values())
    return {
        "dispatch_level": z,
        "dispatch_by_unit": dispatch,
        "pocket_load_mw": meta["pocket_load_mw"],
        "total_dispatch_mw": total,
        "balance_residual_mw": total - meta["pocket_load_mw"],
        "exact_balance_by_construction": abs(total - meta["pocket_load_mw"]) < 1e-12,
        "all_units_within_capacity": all(item["within_capacity"]
                                          for item in dispatch.values()),
    }


def build_stage3s_sumconstraint_hamiltonian(pocket: dict,
                                            contingency: Contingency,
                                            mode: str) -> tuple[Polynomial, list[dict], dict]:
    """Matched continuous encoding with the machine-native sum(u)=1 gate."""
    ordered = sorted(pocket["units"], key=lambda item: item["role"] != "primary")
    load = float(pocket["load_mw"])
    poly = Polynomial(2, max_degree=3, binary_simplify=False)
    variables = []
    for variable, item in enumerate(ordered):
        c1, c2, c3 = _curve(item["role"])
        poly.add([variable], contingency.duration_h * c1 * load)
        poly.add([variable, variable], contingency.duration_h * c2 * load**2)
        poly.add([variable, variable, variable], contingency.duration_h * c3 * load**3)
        variables.append({
            "var": variable, "candidate": item["candidate"],
            "service_id": item["service_id"], "pocket_load_mw": load,
            "unit_kind": ("primary_bess_pv_hybrid_der" if item["role"] == "primary"
                          else "blackstart_bess_pv_overlay"),
            "capacity_mw": item["capacity_mw_loss_derated"],
        })
    audit = poly.coefficient_resolution_audit()
    metadata = {
        "stage": "stage3s_sumconstraint_cubic",
        "design_mode": mode,
        "contingency": asdict(contingency),
        "service_id": pocket["service_id"],
        "variables": variables,
        "sum_constraint": 1.0,
        "coefficient_resolution_audit": audit,
        "recommended_for_hardware": audit["pass"],
    }
    return poly, variables, metadata


def exact_solve_stage3s(poly: Polynomial, tolerance: float = 1e-13) -> dict:
    """Deterministic golden-section oracle on u0+u1=1."""
    phi = (5**0.5 - 1) / 2
    left, right = 0.0, 1.0
    c = right - phi * (right - left)
    d = left + phi * (right - left)
    objective = lambda u: poly.energy([u, 1.0 - u])
    while right - left > tolerance:
        if objective(c) < objective(d):
            right, d = d, c
            c = right - phi * (right - left)
        else:
            left, c = c, d
            d = left + phi * (right - left)
    u = (left + right) / 2
    return {"energy": objective(u), "solution": [u, 1.0-u],
            "solver": "golden_section_1d"}


def decode_stage3s(solution: Iterable[float], variables: list[dict]) -> dict:
    values = [float(v) for v in solution]
    dispatch = {}
    for share, meta in zip(values, variables):
        power = share * meta["pocket_load_mw"]
        dispatch[meta["candidate"]] = {
            "unit_kind": meta["unit_kind"], "share_of_load": share,
            "dispatch_mw": power,
            "within_capacity": power <= meta["capacity_mw"] + 1e-9,
        }
    load = variables[0]["pocket_load_mw"] if variables else 0.0
    total = sum(item["dispatch_mw"] for item in dispatch.values())
    return {
        "dispatch_by_unit": dispatch,
        "total_dispatch_mw": total,
        "pocket_load_mw": load,
        "balance_residual_mw": total - load,
        "all_units_within_capacity": all(item["within_capacity"]
                                          for item in dispatch.values()),
    }
