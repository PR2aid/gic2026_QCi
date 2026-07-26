from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ieee33_feeder"))

import run_live_dirac3 as live
import run_all_local as reproduction
from scripts import certify_live_results as certify
from scripts import integrate_live_results as integrate
from qci_phase3.microgrid import (  # noqa: E402
    CONTINGENCIES, build_stage1_design_hamiltonian,
    build_stage2_islanding_hamiltonian, decode_design,
)


class CampaignTests(unittest.TestCase):
    def test_campaign_is_exactly_budgeted_and_classified(self):
        body = live.protocol_body()
        self.assertEqual(body["planned_jobs"], 10)
        self.assertEqual(body["planned_returned_samples"], 250)
        self.assertEqual(body["registered_evidence_jobs"], 9)
        self.assertEqual(body["characterization_jobs"], 1)
        evidence = [j for j in body["jobs"] if j["evidence_class"] == "REGISTERED_EVIDENCE"]
        probes = [j for j in body["jobs"] if j["evidence_class"] != "REGISTERED_EVIDENCE"]
        self.assertTrue(all(j["coefficient_resolution_audit"]["pass"] for j in evidence))
        self.assertEqual(len(probes), 1)
        self.assertFalse(probes[0]["coefficient_resolution_audit"]["pass"])
        baseline = json.loads((
            ROOT / "ieee33_feeder/results/classical_baseline_summary.json"
        ).read_text())
        self.assertIn("25 objective evaluations per seed", baseline["budget_note"])
        self.assertTrue(all(
            row["sa_evaluations_per_seed"] == live.SAMPLES
            for row in baseline["rows"]
        ))
        strict = json.loads((
            ROOT / "results/live/strict_evidence_audit.json"
        ).read_text())
        self.assertTrue(strict["strict_audit_pass"])
        self.assertEqual(strict["campaign_raw_feasible_counted_samples"], 250)
        physics = json.loads((
            ROOT / "results/live/physical_decode_audit.json"
        ).read_text())
        self.assertTrue(physics["analysis_complete"])
        self.assertEqual(physics["hourly_raw_cap_feasible_counted_samples"], 72)
        self.assertEqual(physics["hourly_counted_samples"], 100)

    def test_freeze_and_reproduction_integrity_are_fail_closed(self):
        first = live.prepare()
        second = live.prepare()
        self.assertEqual(first, second)
        self.assertEqual(first["protocol_sha256"],
                         live.hashlib.sha256(live.canonical(live.protocol_body())).hexdigest())

        def compare(left, right, *, allow_window=False):
            deviations = {"strict": 0.0, "diagnostic": 0.0}
            accepted = reproduction.collect_deviations(
                left,
                right,
                out=deviations,
                allow_window_diagnostic=allow_window,
            )
            return accepted, deviations

        # Non-finite values and structural/type changes must never pass.
        for bad in (float("nan"), float("inf"), float("-inf")):
            self.assertFalse(compare({"x": 1.0}, {"x": bad})[0])
            self.assertFalse(compare({"x": bad}, {"x": 1.0})[0])
            self.assertIsNone(certify._max_relative_deviation({"x": 1.0}, {"x": bad}))
            self.assertIsNone(certify._max_relative_deviation({"x": bad}, {"x": 1.0}))
        with mock.patch.object(
            certify,
            "build_analysis",
            return_value={"certification_pass": True, "non_finite": float("nan")},
        ):
            with self.assertRaises(ValueError):
                certify.main()
        self.assertFalse(compare({"x": [1.0]}, {"x": [1.0, 2.0]})[0])
        self.assertFalse(compare({"x": 1}, {"x": 1.0})[0])
        self.assertFalse(compare({"x": 1.0}, {"y": 1.0})[0])

        # The 1e-4 allowance is limited to the identified non-converged
        # window diagnostic fields.  The same drift elsewhere remains strict.
        reference = {
            "classical_reference": {
                "method": "SLSQP",
                "energy": -10.0,
                "converged": False,
                "x_device_units": [1.0, 2.0],
            },
            "polynomial": [{"idx": [0], "val": 1.0}],
        }
        small_drift = json.loads(json.dumps(reference))
        small_drift["classical_reference"]["x_device_units"][0] += 5e-5
        accepted, deviations = compare(reference, small_drift, allow_window=True)
        self.assertTrue(accepted)
        self.assertGreater(deviations["diagnostic"], reproduction.PAYLOAD_EQUIV_REL_TOL)
        self.assertLess(deviations["diagnostic"], reproduction.SOLVER_DIAGNOSTIC_REL_TOL)
        accepted, deviations = compare(reference, small_drift, allow_window=False)
        self.assertTrue(accepted)
        self.assertGreater(deviations["strict"], reproduction.PAYLOAD_EQUIV_REL_TOL)

        excessive = json.loads(json.dumps(reference))
        excessive["classical_reference"]["energy"] *= 1.001
        accepted, deviations = compare(reference, excessive, allow_window=True)
        self.assertTrue(accepted)
        self.assertGreater(deviations["diagnostic"], reproduction.SOLVER_DIAGNOSTIC_REL_TOL)

        machine_drift = json.loads(json.dumps(reference))
        machine_drift["polynomial"][0]["val"] += 5e-5
        accepted, deviations = compare(reference, machine_drift, allow_window=True)
        self.assertTrue(accepted)
        self.assertGreater(deviations["strict"], reproduction.PAYLOAD_EQUIV_REL_TOL)

        # The real-package guard restores exact bytes after missing/additional
        # files and after ordinary or BaseException exits.
        self.assertEqual(reproduction.verify_pristine_payloads(), 42)
        target = ROOT / reproduction.WINDOW_DIAGNOSTIC_PAYLOAD
        hourly = (
            ROOT / "ieee39_transmission/qci/ieee39_flagship/"
            "hdisp_c0_s171_h16_polynomial.json"
        )
        original = target.read_bytes()
        unexpected = target.parent / "_unexpected_regression_payload.json"

        with reproduction.preserved_payload_bytes():
            changed = json.loads(target.read_text())
            changed["classical_reference"]["x_device_units"][0] += 2e-6
            target.write_text(json.dumps(changed))
            result = reproduction.verify_regenerated_payloads()
            self.assertGreater(result["worst"]["diagnostic"], 0.0)
            self.assertEqual(result["worst"]["strict"], 0.0)
        self.assertEqual(target.read_bytes(), original)

        with reproduction.preserved_payload_bytes():
            changed = json.loads(target.read_text())
            changed["polynomial"][0]["val"] += 1e-3
            target.write_text(json.dumps(changed))
            with self.assertRaises(SystemExit):
                reproduction.verify_regenerated_payloads()
        self.assertEqual(target.read_bytes(), original)

        with reproduction.preserved_payload_bytes():
            changed = json.loads(hourly.read_text())
            changed["classical_reference"]["energy"] *= 1.00005
            hourly.write_text(json.dumps(changed))
            with self.assertRaises(SystemExit):
                reproduction.verify_regenerated_payloads()

        with reproduction.preserved_payload_bytes():
            target.unlink()
            with self.assertRaises(SystemExit):
                reproduction.verify_regenerated_payloads()
        self.assertEqual(target.read_bytes(), original)

        with self.assertRaises(RuntimeError):
            with reproduction.preserved_payload_bytes():
                target.write_text('{"changed": true}\n')
                unexpected.write_text("{}\n")
                raise RuntimeError("controlled restoration regression")
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(unexpected.exists())

        with self.assertRaises(KeyboardInterrupt):
            with reproduction.preserved_payload_bytes():
                target.unlink()
                raise KeyboardInterrupt
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(reproduction.PAYLOAD_SNAPSHOT.exists())

        reproduction.PAYLOAD_SNAPSHOT.mkdir(parents=True)
        marker = reproduction.PAYLOAD_SNAPSHOT / "do_not_overwrite"
        marker.write_text("sentinel\n")
        try:
            with self.assertRaises(SystemExit):
                reproduction.snapshot_shipped_payloads()
            self.assertEqual(marker.read_text(), "sentinel\n")
        finally:
            marker.unlink()
            reproduction.PAYLOAD_SNAPSHOT.rmdir()

        # The notebook-level acceptance certificate must be bound to the
        # current uninterrupted reproduction, never to shipped PASS files.
        notebook = json.loads((ROOT / "RUN_ON_QBRAID.ipynb").read_text())
        setup_cell = "".join(notebook["cells"][1]["source"])
        runner_cell = "".join(notebook["cells"][2]["source"])
        acceptance_cell = "".join(notebook["cells"][3]["source"])
        self.assertLess(
            setup_cell.index("MANIFEST_VERIFIED = False"),
            setup_cell.index("verify_release_manifest.py"),
        )
        self.assertLess(
            setup_cell.index("SETUP_COMPLETED = False"),
            setup_cell.index("verify_release_manifest.py"),
        )
        self.assertGreater(
            setup_cell.index("SETUP_COMPLETED = True"),
            setup_cell.index("pip', 'install"),
        )
        self.assertLess(
            runner_cell.index("REPRODUCTION_COMPLETED = False"),
            runner_cell.index("subprocess.run"),
        )
        self.assertGreater(
            runner_cell.rindex("REPRODUCTION_COMPLETED = True"),
            runner_cell.rindex("subprocess.run"),
        )
        self.assertLess(
            acceptance_cell.index("ACCEPTANCE_PATH.unlink(missing_ok=True)"),
            acceptance_cell.index(
                "assert globals().get('REPRODUCTION_COMPLETED') is True"
            ),
        )

        with tempfile.TemporaryDirectory() as temporary:
            test_root = Path(temporary)
            required = (
                "results_summary.json",
                "RELEASE_MANIFEST.json",
                "ieee39_transmission/results/convention_test_summary.json",
                "results/live/strict_evidence_audit.json",
                "results/live/physical_decode_audit.json",
                "results/live/certified_hardware_analysis.json",
            )
            for rel in required:
                source = ROOT / rel
                target_copy = test_root / rel
                target_copy.parent.mkdir(parents=True, exist_ok=True)
                target_copy.write_bytes(source.read_bytes())
            for name in (
                "figure1_architecture.png",
                "figure1_architecture.pdf",
                "figure2_results.png",
                "figure2_results.pdf",
            ):
                target_figure = test_root / "figures" / name
                target_figure.parent.mkdir(parents=True, exist_ok=True)
                target_figure.write_bytes(b"freshly-regenerated-test-figure")

            acceptance_path = test_root / "results/reproduction_acceptance.json"
            base_environment = {
                "ROOT": test_root,
                "MANIFEST_VERIFIED": True,
                "SETUP_COMPLETED": True,
                "ACCEPTANCE_PATH": acceptance_path,
                "ACCEPTANCE_BUILDING": acceptance_path.with_suffix(".json.building"),
                "os": os,
            }
            failed_environment = {
                **base_environment,
                "REPRODUCTION_COMPLETED": False,
            }
            acceptance_path.write_bytes(b"stale-certificate")
            with self.assertRaises(AssertionError):
                exec(compile(acceptance_cell, "<acceptance-cell>", "exec"),
                     failed_environment)
            self.assertFalse(acceptance_path.exists())

            passed_environment = {
                **base_environment,
                "REPRODUCTION_COMPLETED": True,
            }
            with mock.patch("builtins.print"):
                exec(compile(acceptance_cell, "<acceptance-cell>", "exec"),
                     passed_environment)
            certificate = json.loads(acceptance_path.read_text())
            self.assertEqual(certificate["status"], "PASS")
            self.assertTrue(certificate["reproduction_commands_completed"])
            self.assertEqual(
                certificate["consolidated_claim_audit"],
                {"passed": 39, "total": 39},
            )
            self.assertEqual(
                certificate["strict_raw_evidence"]["responses_passed"], 11
            )
            self.assertEqual(
                certificate["physical_decode_diagnostic"][
                    "hourly_cap_feasible_samples"
                ],
                72,
            )

    def test_constant_restored_raw_scoring(self):
        frozen = live.prepare()
        descriptor = next(d for d in live.CAMPAIGN if d["id"] == "ieee33_stage3b_balanced_cubic")
        payload = live.normalized_payload(descriptor)
        fake = {
            "status": "COMPLETED",
            "job_info": {"job_id": "synthetic", "job_result": {"device_usage_s": 1.25}},
            "results": {"solutions": [[1.0]], "energies": [-63.796636], "counts": [25]},
            "_local_submission_record": {
                "id": descriptor["id"], "job_id": "synthetic",
                "protocol_sha256": frozen["protocol_sha256"],
                "payload_sha256": live.sha256(payload["path"]),
                "job_type": payload["job_type"], "job_params": payload["job_params"],
                "allocation_after": {"seconds": 100.0, "metered": True},
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=ROOT / "results", delete=False) as handle:
            json.dump(fake, handle); path = Path(handle.name)
        try:
            scored = live.score_response(descriptor, path, frozen, 25)
        finally:
            path.unlink(missing_ok=True)
        self.assertTrue(scored["audit_pass"])
        self.assertAlmostEqual(scored["gap"], 0.0, places=9)
        self.assertNotEqual(scored["device_reported_energy_at_best"],
                            scored["best_energy_local_float64_constant_restored"])

    def test_infeasible_sample_cannot_be_repaired_into_a_pass(self):
        frozen = live.prepare()
        descriptor = next(d for d in live.CAMPAIGN if d["id"] == "ieee33_stage3b_balanced_cubic")
        payload = live.normalized_payload(descriptor)
        fake = {
            "status": "COMPLETED",
            "job_info": {"job_id": "synthetic-bad", "job_result": {"device_usage_s": 1.0}},
            "results": {"solutions": [[1.4]], "energies": [0.0], "counts": [25]},
            "_local_submission_record": {
                "id": descriptor["id"], "job_id": "synthetic-bad",
                "protocol_sha256": frozen["protocol_sha256"],
                "payload_sha256": live.sha256(payload["path"]),
                "job_type": payload["job_type"], "job_params": payload["job_params"],
                "allocation_after": {"seconds": 100.0, "metered": True},
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=ROOT / "results", delete=False) as handle:
            json.dump(fake, handle); path = Path(handle.name)
        try:
            scored = live.score_response(descriptor, path, frozen, 25)
        finally:
            path.unlink(missing_ok=True)
        self.assertFalse(scored["audit_pass"])
        self.assertIn("no raw feasible returned state", scored["audit_reasons"])

    def test_continuous_balance_violation_cannot_pass(self):
        frozen = live.prepare()
        descriptor = next(d for d in live.CAMPAIGN if d["id"] == "ieee33_stage3s_balanced_continuous")
        payload = live.normalized_payload(descriptor)
        fake = {
            "status": "COMPLETED",
            "job_info": {"job_id": "synthetic-continuous-bad", "job_result": {"device_usage_s": 1.0}},
            "results": {"solutions": [[0.2, 0.2]], "energies": [0.0], "counts": [25]},
            "_local_submission_record": {
                "id": descriptor["id"], "job_id": "synthetic-continuous-bad",
                "protocol_sha256": frozen["protocol_sha256"],
                "payload_sha256": live.sha256(payload["path"]),
                "job_type": payload["job_type"], "job_params": payload["job_params"],
                "allocation_after": {"seconds": 100.0, "metered": True},
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=ROOT / "results", delete=False) as handle:
            json.dump(fake, handle); path = Path(handle.name)
        try:
            scored = live.score_response(descriptor, path, frozen, 25)
        finally:
            path.unlink(missing_ok=True)
        self.assertFalse(scored["audit_pass"])
        self.assertIn("no raw feasible returned state", scored["audit_reasons"])

    def test_configuration_tamper_is_detected(self):
        frozen = live.prepare()
        descriptor = next(d for d in live.CAMPAIGN if d["id"] == "ieee33_stage3b_balanced_cubic")
        payload = live.normalized_payload(descriptor)
        wrong = dict(payload["job_params"]); wrong["relaxation_schedule"] = 4
        fake = {
            "status": "COMPLETED",
            "job_info": {"job_id": "synthetic-tamper", "job_result": {"device_usage_s": 1.0}},
            "results": {"solutions": [[1.0]], "energies": [0.0], "counts": [25]},
            "_local_submission_record": {
                "id": descriptor["id"], "job_id": "synthetic-tamper",
                "protocol_sha256": frozen["protocol_sha256"],
                "payload_sha256": live.sha256(payload["path"]),
                "job_type": payload["job_type"], "job_params": wrong,
                "allocation_after": {"seconds": 100.0, "metered": True},
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=ROOT / "results", delete=False) as handle:
            json.dump(fake, handle); path = Path(handle.name)
        try:
            scored = live.score_response(descriptor, path, frozen, 25)
        finally:
            path.unlink(missing_ok=True)
        self.assertFalse(scored["audit_pass"])
        self.assertIn("job_params.relaxation_schedule mismatch", scored["audit_reasons"])

    def test_probe_api_rejection_is_audited_characterization(self):
        frozen = live.prepare()
        descriptor = next(d for d in live.CAMPAIGN if d["id"] == "ieee39_cubic_resolution_probe")
        payload = live.normalized_payload(descriptor)
        fake = {
            "status": "SUBMISSION_REJECTED", "job_info": {}, "results": None,
            "submission_error": {"error_type": "HTTPError", "error": "synthetic rejection"},
            "_local_submission_record": {
                "id": descriptor["id"], "job_id": None,
                "protocol_sha256": frozen["protocol_sha256"],
                "payload_sha256": live.sha256(payload["path"]),
                "job_type": payload["job_type"], "job_params": payload["job_params"],
                "allocation_after": None,
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", dir=ROOT / "results", delete=False) as handle:
            json.dump(fake, handle); path = Path(handle.name)
        try:
            scored = live.score_response(descriptor, path, frozen, 25)
        finally:
            path.unlink(missing_ok=True)
        self.assertTrue(scored["audit_pass"])
        self.assertEqual(scored["characterization_outcome"], "SUBMISSION_REJECTED")
        total, count, mean = integrate.measured_usage([
            {"device_usage_s": 2.5}, {"device_usage_s": None}
        ])
        self.assertEqual((total, count, mean), (2.5, 1, 2.5))


class StructuralContingencyTests(unittest.TestCase):
    def test_faulted_stage2_candidates_are_removed_not_penalized(self):
        h1, items, _ = build_stage1_design_hamiltonian("balanced_critical")
        design = decode_design(h1.exact_solve_binary()["x"], items)
        contingency = next(c for c in CONTINGENCIES if c.name == "compound_two_lateral_fault")
        h2, selected, metadata = build_stage2_islanding_hamiltonian(
            design, contingency, "balanced_critical"
        )
        self.assertEqual(h2.nvars, len(selected))
        self.assertTrue(metadata["structurally_excluded_unavailable_candidates"])
        self.assertTrue(all(contingency.name not in item["unavailable_in_contingencies"]
                            for item in selected))


if __name__ == "__main__":
    unittest.main()
