from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import certify_live_results as certified  # noqa: E402
import run_judge_reproduction as judge  # noqa: E402


class CertifiedLiveAnalysisTests(unittest.TestCase):
    def test_window_kkt_oracle_and_raw_hardware_gap(self):
        analysis = certified.build_analysis()
        self.assertTrue(analysis["certification_pass"])
        self.assertAlmostEqual(analysis["oracle"]["energy"], -754053.3836439128, places=6)
        self.assertLess(analysis["oracle"]["simplex_residual"], 1e-12)
        self.assertLess(
            analysis["oracle"]["max_active_stationarity_residual"], 1e-8
        )
        self.assertAlmostEqual(
            analysis["hardware"]["relative_gap"], 0.010387498783776941, places=12
        )
        self.assertEqual(analysis["hardware"]["counted_samples"], 25)
        self.assertTrue(analysis["hardware"]["all_raw_states_feasible"])

    def test_nonconverged_slsqp_is_not_used_as_ground_truth(self):
        analysis = certified.build_analysis()
        diagnostic = analysis["superseded_nonconverged_slsqp_diagnostic"]
        self.assertFalse(diagnostic["stored_converged"])
        self.assertGreater(diagnostic["rounded_vector_sum"], 40.0)
        self.assertNotAlmostEqual(
            float(diagnostic["stored_energy"]), analysis["oracle"]["energy"], places=3
        )

    def test_judge_rerun_modes_preserve_evidence_boundary(self):
        smoke, smoke_samples = judge.descriptors_for("smoke")
        evidence, evidence_samples = judge.descriptors_for("evidence")
        probe, probe_samples = judge.descriptors_for("characterization")
        self.assertEqual((len(smoke), smoke_samples), (1, 3))
        self.assertEqual((len(evidence), evidence_samples), (9, 25))
        self.assertTrue(all(row["class"] == "REGISTERED_EVIDENCE" for row in evidence))
        self.assertEqual((len(probe), probe_samples), (1, 25))
        self.assertTrue(probe[0]["class"].startswith("CHARACTERIZATION"))
        # Resume mode may submit only descriptors with no existing receipt,
        # result, timeout snapshot, or submission-error artifact.
        with mock.patch.object(
            judge.frozen, "artifact_paths",
            side_effect=lambda run_id, _sha: [Path("receipt.json")]
            if run_id == evidence[0]["id"] else [],
        ):
            missing = judge.descriptors_without_artifacts(evidence, "a" * 64)
        self.assertEqual(len(missing), 8)
        self.assertNotIn(evidence[0], missing)


if __name__ == "__main__":
    unittest.main()
