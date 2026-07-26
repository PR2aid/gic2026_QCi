from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qci_phase3.microgrid import (
    CONTINGENCIES, DESIGN_RISK_VALUE_USD_PER_MWH,
    build_stage1_design_hamiltonian, build_stage2_islanding_hamiltonian,
    build_stage3b_exact_balance_hamiltonian,
    build_stage3s_sumconstraint_hamiltonian, decode_design, decode_islanding,
    decode_stage3b, exact_solve_stage3s, two_unit_pockets,
)
from qci_phase3.polynomial import Polynomial


class PolynomialTests(unittest.TestCase):
    def test_repeated_indices_and_resolution(self):
        poly = Polynomial(1, max_degree=3, binary_simplify=False)
        poly.add([0], -126.480744); poly.add([0, 0], 63.518256)
        poly.add([0, 0, 0], -0.834148)
        payload = poly.to_qci_polynomial_file("cubic")
        self.assertEqual([term["idx"] for term in payload["file_config"]["polynomial"]["data"]],
                         [[0, 0, 1], [0, 1, 1], [1, 1, 1]])
        self.assertTrue(payload["coefficient_resolution_audit_local_only"]["pass"])


class ModelTests(unittest.TestCase):
    @staticmethod
    def design(mode):
        h, items, _ = build_stage1_design_hamiltonian(mode)
        return h, decode_design(h.exact_solve_binary()["x"], items)

    def test_exact_three_point_pareto(self):
        expected = {
            "cost_efficient": (13_420_000, 1_073_600, 0),
            "balanced_critical": (16_440_000, 1_315_200, 1),
            "robust_critical": (19_460_000, 1_556_800, 2),
        }
        for mode, values in expected.items():
            h, design = self.design(mode)
            self.assertAlmostEqual(design["upfront_cost_usd"], values[0])
            self.assertAlmostEqual(design["annualized_cost_usd_yr"], values[1])
            self.assertEqual(design["selected_blackstart_overlap_count"], values[2])
            self.assertTrue(h.coefficient_resolution_audit()["pass"])

    def test_headline_resilience_metrics(self):
        expected = {
            "cost_efficient": (21.0, 16.0, 0.34375),
            "balanced_critical": (4.2, 8.0, 0.25),
            "robust_critical": (0.0, 0.0, 0.0),
        }
        critical_services = {"MG_lateral_22_24", "MG_lateral_25_32"}
        for mode, target in expected.items():
            _, design = self.design(mode); annual = hours = 0.0; worst = 0.0
            for contingency in CONTINGENCIES:
                h2, selected, _ = build_stage2_islanding_hamiltonian(design, contingency, mode)
                self.assertTrue(h2.coefficient_resolution_audit()["pass"])
                decoded = decode_islanding(h2.exact_solve_binary()["x"], selected, contingency)
                annual += contingency.event_rate_per_year * decoded["critical_unserved_mwh"]
                hours += sum(contingency.duration_h for service in critical_services
                             if service not in decoded["active_service_ids"])
                worst = max(worst, decoded["max_customer_fraction_unserved"])
            self.assertAlmostEqual(annual, target[0]); self.assertAlmostEqual(hours, target[1])
            self.assertAlmostEqual(worst, target[2])

    def test_stage3b_exact_balance_and_matched_continuous(self):
        for mode in ("balanced_critical", "robust_critical"):
            _, design = self.design(mode)
            for contingency in CONTINGENCIES:
                for pocket in two_unit_pockets(design, contingency):
                    h, variables, _, levels = build_stage3b_exact_balance_hamiltonian(
                        pocket, contingency, mode)
                    oracle = h.exact_solve_integer(levels)
                    decoded = decode_stage3b(oracle["solution"], variables)
                    self.assertEqual(oracle["solution"], [1])
                    self.assertTrue(h.coefficient_resolution_audit()["pass"])
                    self.assertAlmostEqual(decoded["balance_residual_mw"], 0.0, places=12)
                    self.assertTrue(decoded["all_units_within_capacity"])
                    hs, _, _ = build_stage3s_sumconstraint_hamiltonian(pocket, contingency, mode)
                    continuous = exact_solve_stage3s(hs)["solution"][0]
                    self.assertLess(abs(continuous - 2/3), 0.01)


class PipelineTests(unittest.TestCase):
    def test_pipeline_payloads(self):
        subprocess.run([sys.executable, "scripts/run_pipeline.py"], cwd=ROOT, check=True,
                       capture_output=True, text=True)
        payloads = sorted((ROOT / "qci_payloads").glob("*.json"))
        self.assertEqual(len(payloads), 27)
        self.assertEqual(len(list((ROOT / "qci_payloads").glob("stage3b_*.json"))), 6)
        self.assertEqual(len(list((ROOT / "qci_payloads").glob("stage3s_*.json"))), 6)
        for path in payloads:
            record = json.loads(path.read_text())
            self.assertTrue(record["coefficient_resolution_audit_local_only"]["pass"], path.name)

    def test_stage0_break_even_and_analytic_band(self):
        subprocess.run([sys.executable, "scripts/run_stage0.py"], cwd=ROOT, check=True,
                       capture_output=True, text=True)
        data = json.loads((ROOT / "results/stage0_sectionalization/stage0_summary.json").read_text())
        thresholds = {row["service_id"]: row["breakeven_risk_value_usd_per_mwh"]
                      for row in data["breakeven_critical_risk_valuations"]}
        self.assertAlmostEqual(thresholds["MG_lateral_22_24"], 14_380.95, places=1)
        self.assertAlmostEqual(thresholds["MG_lateral_25_32"], 57_523.81, places=1)
        interval = data["submitted_plan_A"]["analytic_policy_optimality_interval_rho"]
        self.assertAlmostEqual(interval["lower_open"], 0.011685, places=5)
        self.assertAlmostEqual(interval["upper_closed"], 0.014405, places=5)

    def test_ac_and_chronology_audits(self):
        subprocess.run([sys.executable, "scripts/run_ac_audit.py"], cwd=ROOT, check=True,
                       capture_output=True, text=True)
        ac = json.loads((ROOT / "results/ac_powerflow/ac_powerflow_summary.json").read_text())
        self.assertGreater(ac["grid_connected_base_case"]["min_voltage_pu"], 0.90)
        self.assertTrue(all(row["all_served_island_ac_checks_pass"]
                            for row in ac["design_scenario_results"]))
        subprocess.run([sys.executable, "scripts/run_chronology_audit.py"], cwd=ROOT,
                       check=True, capture_output=True, text=True)
        chronology = json.loads((ROOT / "results/chronology_audit/chronology_audit_summary.json").read_text())
        self.assertTrue(chronology["audit_pass"]); self.assertEqual(chronology["case_count"], 96)
        self.assertEqual(chronology["physical_constraint_violation_count"], 0)
        hardened = chronology["worst_case_by_design"]["chronology_hardened_sensitivity"]
        self.assertAlmostEqual(hardened["critical_unserved_mwh"], 0.0, places=9)


class IntegratedScientificAuditTests(unittest.TestCase):
    def test_faulted_stage2_candidates_are_structurally_removed(self):
        for mode in ("cost_efficient", "balanced_critical", "robust_critical"):
            h1, items, _ = build_stage1_design_hamiltonian(mode)
            design = decode_design(h1.exact_solve_binary()["x"], items)
            for contingency in CONTINGENCIES:
                h2, selected, metadata = build_stage2_islanding_hamiltonian(
                    design, contingency, mode)
                self.assertEqual(h2.nvars, len(selected))
                self.assertTrue(all(contingency.name not in item["unavailable_in_contingencies"]
                                    for item in selected))
                expected_excluded = [item for item in design["selected"]
                                     if contingency.name in item["unavailable_in_contingencies"]]
                self.assertEqual(len(metadata["structurally_excluded_unavailable_candidates"]),
                                 len(expected_excluded))

    def test_static_robustness_is_not_conflated_with_chronology(self):
        with (ROOT / "results/design_pareto.csv").open() as handle:
            pareto = {row["design_mode"]: row for row in csv.DictReader(handle)}
        self.assertAlmostEqual(float(pareto["robust_critical"]["expected_annual_critical_unserved_mwh"]), 0.0)
        chronology = json.loads((ROOT / "results/chronology_audit/chronology_audit_summary.json").read_text())
        self.assertGreater(chronology["worst_case_by_design"]["robust_critical"]["critical_unserved_mwh"], 0.0)
        self.assertAlmostEqual(
            chronology["worst_case_by_design"]["chronology_hardened_sensitivity"]["critical_unserved_mwh"],
            0.0,
        )

    def test_all_classical_baseline_rows_match_payload_oracles(self):
        subprocess.run([sys.executable, "scripts/run_baselines.py"], cwd=ROOT,
                       check=True, capture_output=True, text=True)
        data = json.loads((ROOT / "results/classical_baseline_summary.json").read_text())
        self.assertEqual(len(data["rows"]), 27)
        for row in data["rows"]:
            payload = json.loads((ROOT / "qci_payloads" / row["payload"]).read_text())
            self.assertAlmostEqual(float(row["exact_energy"]),
                                   float(payload["local_exact_ground_state"]["energy"]), places=7)


if __name__ == "__main__":
    unittest.main()
