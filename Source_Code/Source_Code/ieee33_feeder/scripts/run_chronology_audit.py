#!/usr/bin/env python3
"""Run a deterministic 24-hour SOC and black-start stress audit.

This is a credential-free *classical* engineering screen for the three
disclosed design modes.  It uses an explicitly synthetic normalized hourly
load/PV profile, deterministic PV-first dispatch, battery efficiency and SOC
recursion, a protected energy reserve, and a four-hour islanding event.  Six
four-hour-aligned event starts cover the full synthetic day.

The audit is deliberately modest.  It is not historical-data evidence,
forecasting, stochastic unit commitment, AC optimal power flow, transient or
protection analysis, and it is not QCi/Dirac-3 hardware evidence.  Emergency
load shedding is an auditable outcome rather than a physical-constraint
violation.  The process fails closed only if its own power, energy, reserve,
balance, or SOC-recursion constraints are violated.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "phase3-24h-chronology-audit-v1"
TOL = 1e-9

# One value per hour.  These are purpose-built, dimensionless stress profiles;
# they are not observations, forecasts, or a fit to a utility data set.
LOAD_MULTIPLIER = [
    0.72, 0.69, 0.67, 0.66, 0.67, 0.71,
    0.78, 0.86, 0.92, 0.96, 0.99, 1.00,
    0.99, 0.97, 0.98, 1.02, 1.08, 1.12,
    1.15, 1.12, 1.03, 0.94, 0.86, 0.78,
]
PV_CAPACITY_FACTOR = [
    0.00, 0.00, 0.00, 0.00, 0.00, 0.00,
    0.02, 0.10, 0.28, 0.48, 0.68, 0.82,
    0.90, 0.86, 0.72, 0.52, 0.30, 0.12,
    0.02, 0.00, 0.00, 0.00, 0.00, 0.00,
]
OUTAGE_START_HOURS = [0, 4, 8, 12, 16, 20]
OUTAGE_DURATION_H = 4
TIME_STEP_H = 1.0

# Transparent synthetic operating assumptions.  PV nameplate is set equal to
# the package BESS power rating.  A single 1.06 delivery derating is applied to
# both power paths.  Battery discharge is AC power delivered to the load, so
# internal stored energy falls by P_discharge / eta_discharge.
LOSS_DERATING = 1.06
CHARGE_EFFICIENCY = 0.95
DISCHARGE_EFFICIENCY = 0.92
INITIAL_SOC_FRACTION = 0.85
PROTECTED_RESERVE_FRACTION = 0.25
BLACK_START_AUX_ENERGY_FRACTION = 0.05
PV_NAMEPLATE_TO_BESS_POWER = 1.0

TOTAL_CUSTOMER_BLOCKS = 32

SERVICES: dict[str, dict[str, Any]] = {
    "MG_trunk_1_17": {
        "base_load_mw": 1.505,
        "critical_load_mw": 0.000,
        "customer_blocks": 17,
    },
    "MG_lateral_18_21": {
        "base_load_mw": 0.360,
        "critical_load_mw": 0.000,
        "customer_blocks": 4,
    },
    "MG_lateral_22_24": {
        "base_load_mw": 0.930,
        "critical_load_mw": 0.840,
        "customer_blocks": 3,
    },
    "MG_lateral_25_32": {
        "base_load_mw": 0.920,
        "critical_load_mw": 0.210,
        "customer_blocks": 8,
    },
}

# The first three inventories reproduce the exact planning points. Every
# primary section is covered in all modes.  The intermediate point protects
# the high-critical-load 22--24 pocket; the robust point protects both critical
# pockets. A fourth classical sensitivity upsizes only the two black-start
# overlays after the conservative SOC audit reveals residual shortfall; it is
# explicitly not represented as a Dirac-3 result.
PRIMARY_RESOURCES = [
    {"resource_id": "primary_trunk", "service_id": "MG_trunk_1_17",
     "role": "primary", "power_mw": 2.0, "energy_mwh": 8.0},
    {"resource_id": "primary_lateral_A", "service_id": "MG_lateral_18_21",
     "role": "primary", "power_mw": 1.0, "energy_mwh": 4.0},
    {"resource_id": "primary_lateral_B", "service_id": "MG_lateral_22_24",
     "role": "primary", "power_mw": 1.0, "energy_mwh": 4.0},
    {"resource_id": "primary_lateral_C", "service_id": "MG_lateral_25_32",
     "role": "primary", "power_mw": 1.0, "energy_mwh": 4.0},
]
BACKUP_B = {
    "resource_id": "blackstart_lateral_B", "service_id": "MG_lateral_22_24",
    "role": "blackstart_overlap", "power_mw": 1.0, "energy_mwh": 4.0,
}
BACKUP_C = {
    "resource_id": "blackstart_lateral_C", "service_id": "MG_lateral_25_32",
    "role": "blackstart_overlap", "power_mw": 1.0, "energy_mwh": 4.0,
}
BACKUP_B_HARDENED = {
    **BACKUP_B, "resource_id": "blackstart_lateral_B_2MW8MWh",
    "power_mw": 2.0, "energy_mwh": 8.0,
}
BACKUP_C_HARDENED = {
    **BACKUP_C, "resource_id": "blackstart_lateral_C_2MW8MWh",
    "power_mw": 2.0, "energy_mwh": 8.0,
}
DESIGN_RESOURCES = {
    "cost_efficient": PRIMARY_RESOURCES,
    "balanced_critical": PRIMARY_RESOURCES + [BACKUP_B],
    "robust_critical": PRIMARY_RESOURCES + [BACKUP_B, BACKUP_C],
    "chronology_hardened_sensitivity": (
        PRIMARY_RESOURCES + [BACKUP_B_HARDENED, BACKUP_C_HARDENED]
    ),
}
DESIGN_UPFRONT_COST_USD = {
    "cost_efficient": 13_420_000.0,
    "balanced_critical": 16_440_000.0,
    "robust_critical": 19_460_000.0,
    "chronology_hardened_sensitivity": 24_060_000.0,
}

SCENARIOS: dict[str, dict[str, Any]] = {
    "upstream_PCC_outage": {
        "affected_services": list(SERVICES),
        "faulted_primary_services": [],
    },
    "lateral_22_24_fault": {
        "affected_services": ["MG_lateral_22_24"],
        "faulted_primary_services": ["MG_lateral_22_24"],
    },
    "lateral_25_32_fault": {
        "affected_services": ["MG_lateral_25_32"],
        "faulted_primary_services": ["MG_lateral_25_32"],
    },
    "compound_two_lateral_fault": {
        "affected_services": ["MG_lateral_22_24", "MG_lateral_25_32"],
        "faulted_primary_services": ["MG_lateral_22_24", "MG_lateral_25_32"],
    },
}


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _initial_states(design_mode: str) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for resource in deepcopy(DESIGN_RESOURCES[design_mode]):
        resource["pv_nameplate_mw"] = (
            resource["power_mw"] * PV_NAMEPLATE_TO_BESS_POWER
        )
        resource["deliverable_power_limit_mw"] = resource["power_mw"] / LOSS_DERATING
        resource["reserve_mwh"] = (
            PROTECTED_RESERVE_FRACTION * resource["energy_mwh"]
        )
        resource["soc_mwh"] = INITIAL_SOC_FRACTION * resource["energy_mwh"]
        resource["started_in_event"] = False
        states[resource["resource_id"]] = resource
    return states


def _resources_for_service(states: dict[str, dict[str, Any]], service_id: str,
                           primary_faulted: bool) -> list[dict[str, Any]]:
    resources = [state for state in states.values() if state["service_id"] == service_id]
    if primary_faulted:
        resources = [state for state in resources if state["role"] != "primary"]
    # The disclosed dispatch rule is primary first, then overlapping backup.
    return sorted(resources, key=lambda r: (r["role"] != "primary", r["resource_id"]))


def _charge_from_surplus(resources: list[dict[str, Any]], surplus_mw: float,
                         flows: dict[str, dict[str, float]]) -> float:
    """Charge the least-full resources first; return unused PV power."""
    ordered = sorted(
        resources,
        key=lambda r: (r["soc_mwh"] / r["energy_mwh"], r["resource_id"]),
    )
    remaining = max(0.0, surplus_mw)
    for resource in ordered:
        if remaining <= TOL:
            break
        room_limited_mw = max(
            0.0,
            (resource["energy_mwh"] - resource["soc_mwh"])
            / (CHARGE_EFFICIENCY * TIME_STEP_H),
        )
        charge_mw = min(
            remaining,
            resource["deliverable_power_limit_mw"],
            room_limited_mw,
        )
        resource["soc_mwh"] += CHARGE_EFFICIENCY * charge_mw * TIME_STEP_H
        flows[resource["resource_id"]]["charge_mw"] += charge_mw
        remaining -= charge_mw
    return remaining


def _grid_connected_charge(states: dict[str, dict[str, Any]], hour: int) -> None:
    """Use only local PV surplus to restore readiness while the grid is present."""
    for service_id, service in SERVICES.items():
        resources = _resources_for_service(states, service_id, primary_faulted=False)
        if not resources:
            continue
        local_load_mw = service["base_load_mw"] * LOAD_MULTIPLIER[hour]
        pv_mw = sum(
            min(
                r["pv_nameplate_mw"] * PV_CAPACITY_FACTOR[hour] / LOSS_DERATING,
                r["deliverable_power_limit_mw"],
            )
            for r in resources
        )
        surplus_mw = max(0.0, pv_mw - local_load_mw)
        dummy = {
            r["resource_id"]: {"charge_mw": 0.0}
            for r in resources
        }
        _charge_from_surplus(resources, surplus_mw, dummy)


def _dispatch_islanded_service(
    resources: list[dict[str, Any]],
    service: dict[str, Any],
    hour: int,
    event_start: bool,
) -> dict[str, Any]:
    demand_mw = service["base_load_mw"] * LOAD_MULTIPLIER[hour]
    critical_demand_mw = service["critical_load_mw"] * LOAD_MULTIPLIER[hour]
    flows = {
        r["resource_id"]: {
            "soc_start_mwh": r["soc_mwh"],
            "black_start_aux_mwh": 0.0,
            "pv_available_mw": 0.0,
            "pv_to_load_mw": 0.0,
            "charge_mw": 0.0,
            "discharge_to_load_mw": 0.0,
            "soc_end_mwh": r["soc_mwh"],
            "startup_failed": False,
        }
        for r in resources
    }

    active: list[dict[str, Any]] = []
    for resource in resources:
        flow = flows[resource["resource_id"]]
        if event_start:
            auxiliary_mwh = BLACK_START_AUX_ENERGY_FRACTION * resource["energy_mwh"]
            if resource["soc_mwh"] - auxiliary_mwh < resource["reserve_mwh"] - TOL:
                flow["startup_failed"] = True
                continue
            resource["soc_mwh"] -= auxiliary_mwh
            resource["started_in_event"] = True
            flow["black_start_aux_mwh"] = auxiliary_mwh
        if resource["started_in_event"]:
            active.append(resource)

    remaining_load_mw = demand_mw
    pv_surplus_mw = 0.0
    for resource in active:
        flow = flows[resource["resource_id"]]
        pv_available_mw = min(
            resource["pv_nameplate_mw"] * PV_CAPACITY_FACTOR[hour] / LOSS_DERATING,
            resource["deliverable_power_limit_mw"],
        )
        pv_to_load_mw = min(remaining_load_mw, pv_available_mw)
        flow["pv_available_mw"] = pv_available_mw
        flow["pv_to_load_mw"] = pv_to_load_mw
        remaining_load_mw -= pv_to_load_mw
        pv_surplus_mw += pv_available_mw - pv_to_load_mw

    # If aggregate PV exceeds load, it may recharge batteries.  No battery is
    # simultaneously charged and discharged in the same hourly step.
    if remaining_load_mw <= TOL and active:
        _charge_from_surplus(active, pv_surplus_mw, flows)
    else:
        for resource in active:
            if remaining_load_mw <= TOL:
                break
            flow = flows[resource["resource_id"]]
            output_headroom_mw = max(
                0.0,
                resource["deliverable_power_limit_mw"] - flow["pv_to_load_mw"],
            )
            energy_limited_mw = max(
                0.0,
                (resource["soc_mwh"] - resource["reserve_mwh"])
                * DISCHARGE_EFFICIENCY / TIME_STEP_H,
            )
            discharge_mw = min(remaining_load_mw, output_headroom_mw, energy_limited_mw)
            resource["soc_mwh"] -= (
                discharge_mw * TIME_STEP_H / DISCHARGE_EFFICIENCY
            )
            flow["discharge_to_load_mw"] = discharge_mw
            remaining_load_mw -= discharge_mw

    violations: list[str] = []
    for resource in resources:
        flow = flows[resource["resource_id"]]
        flow["soc_end_mwh"] = resource["soc_mwh"]
        expected_soc = (
            flow["soc_start_mwh"]
            - flow["black_start_aux_mwh"]
            + CHARGE_EFFICIENCY * flow["charge_mw"] * TIME_STEP_H
            - flow["discharge_to_load_mw"] * TIME_STEP_H / DISCHARGE_EFFICIENCY
        )
        flow["soc_recursion_residual_mwh"] = resource["soc_mwh"] - expected_soc
        if abs(flow["soc_recursion_residual_mwh"]) > 1e-8:
            violations.append(f"{resource['resource_id']}: SOC recursion mismatch")
        if resource["soc_mwh"] < resource["reserve_mwh"] - TOL:
            violations.append(f"{resource['resource_id']}: protected reserve breached")
        if resource["soc_mwh"] > resource["energy_mwh"] + TOL:
            violations.append(f"{resource['resource_id']}: energy capacity exceeded")
        if flow["charge_mw"] > resource["deliverable_power_limit_mw"] + TOL:
            violations.append(f"{resource['resource_id']}: charge power exceeded")
        if (flow["pv_to_load_mw"] + flow["discharge_to_load_mw"]
                > resource["deliverable_power_limit_mw"] + TOL):
            violations.append(f"{resource['resource_id']}: output power exceeded")
        if flow["charge_mw"] > TOL and flow["discharge_to_load_mw"] > TOL:
            violations.append(f"{resource['resource_id']}: simultaneous charge/discharge")

    unserved_mw = max(0.0, remaining_load_mw)
    served_mw = demand_mw - unserved_mw
    critical_served_mw = min(critical_demand_mw, served_mw)
    critical_unserved_mw = max(0.0, critical_demand_mw - critical_served_mw)
    balance_residual_mw = demand_mw - served_mw - unserved_mw
    if abs(balance_residual_mw) > 1e-8:
        violations.append("service power balance mismatch")

    return {
        "demand_mw": demand_mw,
        "critical_demand_mw": critical_demand_mw,
        "served_mw": served_mw,
        "unserved_mw": unserved_mw,
        "critical_served_mw": critical_served_mw,
        "critical_unserved_mw": critical_unserved_mw,
        "customer_blocks_equivalent_unserved": (
            service["customer_blocks"] * unserved_mw / demand_mw
            if demand_mw > TOL else 0.0
        ),
        "active_resource_count": len(active),
        "black_start_achieved": bool(active),
        "balance_residual_mw": balance_residual_mw,
        "resource_flows": flows,
        "constraint_violations": violations,
    }


def simulate_case(design_mode: str, scenario_name: str, outage_start_hour: int) -> dict[str, Any]:
    scenario = SCENARIOS[scenario_name]
    outage_hours = set(range(outage_start_hour, outage_start_hour + OUTAGE_DURATION_H))
    states = _initial_states(design_mode)

    event_hourly: list[dict[str, Any]] = []
    violations: list[str] = []
    affected_demand_mwh = 0.0
    served_mwh = 0.0
    unserved_mwh = 0.0
    critical_demand_mwh = 0.0
    critical_unserved_mwh = 0.0
    customer_block_hours_unserved = 0.0
    worst_customer_fraction_unserved = 0.0

    for hour in range(24):
        if hour not in outage_hours:
            _grid_connected_charge(states, hour)
            for state in states.values():
                if state["soc_mwh"] < state["reserve_mwh"] - TOL:
                    violations.append(
                        f"hour {hour}, {state['resource_id']}: reserve breached while grid connected"
                    )
                if state["soc_mwh"] > state["energy_mwh"] + TOL:
                    violations.append(
                        f"hour {hour}, {state['resource_id']}: energy capacity exceeded while charging"
                    )
            continue

        service_results: dict[str, Any] = {}
        hour_equivalent_blocks_unserved = 0.0
        for service_id in scenario["affected_services"]:
            resources = _resources_for_service(
                states,
                service_id,
                primary_faulted=service_id in scenario["faulted_primary_services"],
            )
            result = _dispatch_islanded_service(
                resources,
                SERVICES[service_id],
                hour,
                event_start=(hour == outage_start_hour),
            )
            service_results[service_id] = result
            for violation in result["constraint_violations"]:
                violations.append(f"hour {hour}, {service_id}: {violation}")
            affected_demand_mwh += result["demand_mw"] * TIME_STEP_H
            served_mwh += result["served_mw"] * TIME_STEP_H
            unserved_mwh += result["unserved_mw"] * TIME_STEP_H
            critical_demand_mwh += result["critical_demand_mw"] * TIME_STEP_H
            critical_unserved_mwh += result["critical_unserved_mw"] * TIME_STEP_H
            hour_equivalent_blocks_unserved += result["customer_blocks_equivalent_unserved"]

        customer_block_hours_unserved += hour_equivalent_blocks_unserved * TIME_STEP_H
        customer_fraction_unserved = hour_equivalent_blocks_unserved / TOTAL_CUSTOMER_BLOCKS
        worst_customer_fraction_unserved = max(
            worst_customer_fraction_unserved,
            customer_fraction_unserved,
        )
        event_hourly.append({
            "hour": hour,
            "load_multiplier": LOAD_MULTIPLIER[hour],
            "pv_capacity_factor": PV_CAPACITY_FACTOR[hour],
            "affected_service_results": service_results,
            "customer_fraction_unserved": customer_fraction_unserved,
        })

    min_soc_fraction = min(
        state["soc_mwh"] / state["energy_mwh"] for state in states.values()
    )
    black_start_services_started = 0
    for service_id in scenario["affected_services"]:
        event_rows = [
            row["affected_service_results"][service_id]
            for row in event_hourly
        ]
        if event_rows and event_rows[0]["black_start_achieved"]:
            black_start_services_started += 1

    return {
        "design_mode": design_mode,
        "scenario": scenario_name,
        "outage_start_hour": outage_start_hour,
        "outage_end_hour_exclusive": outage_start_hour + OUTAGE_DURATION_H,
        "affected_services": scenario["affected_services"],
        "installed_resource_count": len(states),
        "installed_blackstart_overlap_count": sum(
            state["role"] == "blackstart_overlap" for state in states.values()
        ),
        "affected_demand_mwh": affected_demand_mwh,
        "served_mwh": served_mwh,
        "unserved_mwh": unserved_mwh,
        "critical_demand_mwh": critical_demand_mwh,
        "critical_unserved_mwh": critical_unserved_mwh,
        "customer_block_hours_equivalent_unserved": customer_block_hours_unserved,
        "worst_hour_customer_fraction_unserved": worst_customer_fraction_unserved,
        "black_start_services_started": black_start_services_started,
        "black_start_services_required": len(scenario["affected_services"]),
        "all_affected_services_black_started": (
            black_start_services_started == len(scenario["affected_services"])
        ),
        "all_critical_energy_served": critical_unserved_mwh <= TOL,
        "all_affected_load_energy_served": unserved_mwh <= TOL,
        "minimum_end_of_day_soc_fraction": min_soc_fraction,
        "physical_constraints_pass": not violations,
        "constraint_violations": violations,
        "reliability_shortfall_is_an_outcome_not_a_constraint_error": unserved_mwh > TOL,
        "end_state_by_resource": {
            rid: {
                "service_id": state["service_id"],
                "role": state["role"],
                "soc_mwh": state["soc_mwh"],
                "soc_fraction": state["soc_mwh"] / state["energy_mwh"],
                "reserve_mwh": state["reserve_mwh"],
            }
            for rid, state in sorted(states.items())
        },
        "grid_connected_readiness_hours_simulated": 24 - OUTAGE_DURATION_H,
        "event_hourly_trace": event_hourly,
    }


def _case_csv_row(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "design_mode": case["design_mode"],
        "scenario": case["scenario"],
        "outage_start_hour": case["outage_start_hour"],
        "outage_end_hour_exclusive": case["outage_end_hour_exclusive"],
        "affected_services": ";".join(case["affected_services"]),
        "installed_resource_count": case["installed_resource_count"],
        "installed_blackstart_overlap_count": case["installed_blackstart_overlap_count"],
        "affected_demand_mwh": round(case["affected_demand_mwh"], 9),
        "served_mwh": round(case["served_mwh"], 9),
        "unserved_mwh": round(case["unserved_mwh"], 9),
        "critical_demand_mwh": round(case["critical_demand_mwh"], 9),
        "critical_unserved_mwh": round(case["critical_unserved_mwh"], 9),
        "customer_block_hours_equivalent_unserved": round(
            case["customer_block_hours_equivalent_unserved"], 9
        ),
        "worst_hour_customer_fraction_unserved": round(
            case["worst_hour_customer_fraction_unserved"], 9
        ),
        "black_start_services_started": case["black_start_services_started"],
        "black_start_services_required": case["black_start_services_required"],
        "all_affected_services_black_started": case["all_affected_services_black_started"],
        "all_critical_energy_served": case["all_critical_energy_served"],
        "all_affected_load_energy_served": case["all_affected_load_energy_served"],
        "minimum_end_of_day_soc_fraction": round(
            case["minimum_end_of_day_soc_fraction"], 9
        ),
        "physical_constraints_pass": case["physical_constraints_pass"],
        "reliability_shortfall": case[
            "reliability_shortfall_is_an_outcome_not_a_constraint_error"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO / "results" / "chronology_audit",
        help="Output directory (default: results/chronology_audit)",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if len(LOAD_MULTIPLIER) != 24 or len(PV_CAPACITY_FACTOR) != 24:
        raise SystemExit("load and PV profiles must each contain exactly 24 hourly values")
    if any(value < 0.0 for value in LOAD_MULTIPLIER + PV_CAPACITY_FACTOR):
        raise SystemExit("load multipliers and PV capacity factors must be nonnegative")
    if any(start < 0 or start + OUTAGE_DURATION_H > 24 for start in OUTAGE_START_HOURS):
        raise SystemExit("every outage window must lie inside the 24-hour horizon")
    if not (0.0 < CHARGE_EFFICIENCY <= 1.0 and 0.0 < DISCHARGE_EFFICIENCY <= 1.0):
        raise SystemExit("charge and discharge efficiencies must lie in (0, 1]")
    if not (0.0 <= PROTECTED_RESERVE_FRACTION < INITIAL_SOC_FRACTION <= 1.0):
        raise SystemExit("SOC fractions must satisfy 0 <= reserve < initial <= 1")

    assumptions = {
        "time_step_h": TIME_STEP_H,
        "outage_duration_h": OUTAGE_DURATION_H,
        "outage_start_hours": OUTAGE_START_HOURS,
        "loss_derating": LOSS_DERATING,
        "charge_efficiency": CHARGE_EFFICIENCY,
        "discharge_efficiency": DISCHARGE_EFFICIENCY,
        "initial_soc_fraction": INITIAL_SOC_FRACTION,
        "protected_reserve_fraction": PROTECTED_RESERVE_FRACTION,
        "black_start_aux_energy_fraction_of_nameplate": BLACK_START_AUX_ENERGY_FRACTION,
        "pv_nameplate_to_bess_power": PV_NAMEPLATE_TO_BESS_POWER,
        "dispatch_rule": (
            "PV first; charge only from local PV surplus; primary battery before overlap "
            "battery; protect reserve; shed residual load rather than violate constraints; "
            "critical load receives served energy first for metric accounting"
        ),
    }
    reproducibility_inputs = {
        "schema_version": SCHEMA_VERSION,
        "load_multiplier": LOAD_MULTIPLIER,
        "pv_capacity_factor": PV_CAPACITY_FACTOR,
        "assumptions": assumptions,
        "services": SERVICES,
        "design_resources": DESIGN_RESOURCES,
        "scenarios": SCENARIOS,
    }

    cases = [
        simulate_case(mode, scenario_name, start_hour)
        for mode in DESIGN_RESOURCES
        for scenario_name in SCENARIOS
        for start_hour in OUTAGE_START_HOURS
    ]
    physical_violations = [
        {
            "design_mode": case["design_mode"],
            "scenario": case["scenario"],
            "outage_start_hour": case["outage_start_hour"],
            "violations": case["constraint_violations"],
        }
        for case in cases if not case["physical_constraints_pass"]
    ]

    worst_by_design: dict[str, dict[str, Any]] = {}
    for mode in DESIGN_RESOURCES:
        mode_cases = [case for case in cases if case["design_mode"] == mode]
        worst = max(
            mode_cases,
            key=lambda case: (
                case["critical_unserved_mwh"],
                case["unserved_mwh"],
                case["worst_hour_customer_fraction_unserved"],
            ),
        )
        worst_by_design[mode] = {
            "scenario": worst["scenario"],
            "outage_start_hour": worst["outage_start_hour"],
            "affected_demand_mwh": worst["affected_demand_mwh"],
            "unserved_mwh": worst["unserved_mwh"],
            "critical_unserved_mwh": worst["critical_unserved_mwh"],
            "customer_block_hours_equivalent_unserved": worst[
                "customer_block_hours_equivalent_unserved"
            ],
            "worst_hour_customer_fraction_unserved": worst[
                "worst_hour_customer_fraction_unserved"
            ],
            "all_affected_services_black_started": worst[
                "all_affected_services_black_started"
            ],
        }

    summary = {
        "schema_version": SCHEMA_VERSION,
        "audit_pass": not physical_violations,
        "audit_type": "classical deterministic 24-hour SOC/black-start stress screen",
        "source_and_assumption_metadata": {
            "section_load_source": (
                "Transparent section aggregates derived from the public Baran-Wu/MATPOWER "
                "case33bw pattern and repeated here to keep this audit standalone."
            ),
            "profile_source": (
                "Purpose-built synthetic normalized 24-hour load and PV shapes. They are not "
                "historical measurements, forecasts, or fitted utility data."
            ),
            "resource_source": (
                "Disclosed synthetic 1 MW/4 MWh and 2 MW/8 MWh hybrid BESS/PV packages; "
                "PV nameplate is assumed equal to BESS power for this screen."
            ),
            "customer_metric_note": (
                "Because public meter counts are unavailable, one feeder load bus is one "
                "customer block. Partial service inside an aggregate section is reported as "
                "load-proportional equivalent customer-block-hours, not literal customers."
            ),
            "non_claims": [
                "not historical or utility operational data",
                "not a probabilistic forecast or stochastic unit-commitment study",
                "not AC-OPF, voltage/frequency/transient, protection, or restoration certification",
                "not QCi/Dirac-3 execution, simulation, or quantum-advantage evidence",
                "not investment, regulatory, or operational advice",
            ],
        },
        "assumptions": assumptions,
        "load_multiplier": LOAD_MULTIPLIER,
        "pv_capacity_factor": PV_CAPACITY_FACTOR,
        "design_modes": list(DESIGN_RESOURCES),
        "design_upfront_cost_usd": DESIGN_UPFRONT_COST_USD,
        "scenario_names": list(SCENARIOS),
        "case_count": len(cases),
        "input_sha256": _canonical_sha256(reproducibility_inputs),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "physical_constraint_violation_count": len(physical_violations),
        "physical_constraint_violations": physical_violations,
        "reliability_interpretation": (
            "Unserved load is retained as a stress outcome. The dispatcher never depletes "
            "protected SOC or exceeds a power/energy constraint to hide a shortfall."
        ),
        "hardened_sensitivity_status": (
            "Classical post-audit engineering sensitivity only: two black-start overlays "
            "are upsized from 1 MW/4 MWh to 2 MW/8 MWh. It is not one of the three "
            "Dirac-3 planning modes and is not hardware evidence."
        ),
        "worst_case_by_design": worst_by_design,
        "cases": cases,
    }

    json_path = args.output_dir / "chronology_audit_summary.json"
    csv_path = args.output_dir / "chronology_audit_cases.csv"
    json_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows = [_case_csv_row(case) for case in cases]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"24-hour chronology audit: {'PASS' if summary['audit_pass'] else 'FAIL'}")
    print(f"cases={len(cases)} physical_constraint_violations={len(physical_violations)}")
    for mode, worst in worst_by_design.items():
        print(
            f"{mode}: worst={worst['scenario']}@{worst['outage_start_hour']:02d}:00 "
            f"unserved={worst['unserved_mwh']:.6f} MWh "
            f"critical_unserved={worst['critical_unserved_mwh']:.6f} MWh"
        )
    print(json_path.relative_to(REPO))
    print(csv_path.relative_to(REPO))
    return 0 if summary["audit_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
