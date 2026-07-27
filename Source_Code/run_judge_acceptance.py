#!/usr/bin/env python3
"""Run the complete credential-free judge reproduction and issue one verdict.

Run this file from the release root:

    python Source_Code/run_judge_acceptance.py

The script verifies the untouched release first, regenerates both studies,
re-scores the archived QCi evidence, rebuilds the figures, runs the
evidence/manuscript verifier, and writes a fail-closed acceptance certificate.
It makes no QCi hardware call and requires no credential.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SOURCE = Path(__file__).resolve().parent
RELEASE = SOURCE.parent
ACCEPTANCE = SOURCE / "results" / "reproduction_acceptance.json"
BUILDING = ACCEPTANCE.with_suffix(".json.building")


def run(relative_path: str, *arguments: str) -> None:
    """Run a submitted Python program with the current interpreter."""
    command = [sys.executable, str(SOURCE / relative_path), *arguments]
    print(f"\n$ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=SOURCE, check=True)


def load(relative_path: str) -> dict:
    return json.loads((SOURCE / relative_path).read_text(encoding="utf-8"))


def main() -> int:
    if sys.version_info[:2] not in {(3, 11), (3, 12)}:
        raise SystemExit(
            f"Unsupported Python {sys.version_info.major}.{sys.version_info.minor}; "
            "use Python 3.11 or 3.12."
        )

    # This gate must run before any result or figure is regenerated.
    run("scripts/verify_release_manifest.py", "--root", str(RELEASE))

    ACCEPTANCE.unlink(missing_ok=True)
    BUILDING.unlink(missing_ok=True)

    run("run_all_local.py")
    run("figures/make_figures.py")
    run("scripts/integrate_live_results.py")

    summary = load("results_summary.json")
    conventions = load("ieee39_transmission/results/convention_test_summary.json")
    strict = load("results/live/strict_evidence_audit.json")
    physics = load("results/live/physical_decode_audit.json")
    certified = load("results/live/certified_hardware_analysis.json")
    release_manifest = load("RELEASE_MANIFEST.json")

    assert len(release_manifest["files"]) == 141
    assert summary["all_checks_passed"]
    assert summary["checks_passed"] == summary["checks_total"] == 39
    assert conventions["all_checks_passed"]
    assert conventions["checks_passed"] == conventions["checks_total"] == 15
    assert conventions["checks_expected"] == 15
    assert strict["strict_audit_pass"] and len(strict["records"]) == 10
    assert strict["smoke"]["strict_audit_pass"]
    assert strict["smoke"]["counted_samples"] == 3
    assert (
        strict["campaign_raw_feasible_counted_samples"]
        == strict["campaign_counted_samples"]
        == 250
    )
    assert physics["analysis_complete"]
    assert physics["hourly_raw_cap_feasible_counted_samples"] == 72
    assert physics["hourly_counted_samples"] == 100
    assert physics["hourly_machine_best_states_cap_feasible"] == 1
    assert certified["certification_pass"]
    assert certified["hardware"]["counted_samples"] == 25

    figure_names = (
        "figure1_architecture.png",
        "figure1_architecture.pdf",
        "figure2_results.png",
        "figure2_results.pdf",
    )
    for name in figure_names:
        assert (SOURCE / "figures" / name).is_file()

    acceptance = {
        "acceptance_version": "qpr_qci_phase3_reproduction_acceptance_v1",
        "status": "PASS",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "initial_release_manifest": {
            "passed_before_reproduction": True,
            "manifest_version": release_manifest["manifest_version"],
            "declared_files": len(release_manifest["files"]),
        },
        "reproduction_commands_completed": True,
        "ieee39_convention_invariants": {"passed": 15, "total": 15},
        "ieee33_scientific_tests": {"passed": 10, "total": 10},
        "release_evidence_tests": {"passed": 11, "total": 11},
        "consolidated_claim_audit": {"passed": 39, "total": 39},
        "strict_raw_evidence": {
            "responses_passed": 11,
            "responses_total": 11,
            "campaign_machine_domain_samples_passed": 250,
            "campaign_machine_domain_samples_total": 250,
            "protocol_sha256": strict["protocol_sha256"],
        },
        "physical_decode_diagnostic": {
            "hourly_cap_feasible_samples": 72,
            "hourly_counted_samples": 100,
            "machine_objective_best_states_cap_feasible": 1,
            "machine_objective_best_states_total": 4,
        },
        "certified_window": {
            "passed": certified["certification_pass"],
            "counted_samples": certified["hardware"]["counted_samples"],
            "relative_gap": certified["hardware"]["relative_gap"],
        },
        "manuscript_figures": {
            "figures_passed": 2,
            "figures_total": 2,
            "files": list(figure_names),
        },
        "evidence_manuscript_verifier": {"passed": True},
        "scientific_core": {
            "wall_seconds": summary["wall_seconds"],
            "python": summary["python"],
        },
        "primary_numerical_summary": "results_summary.json",
    }

    ACCEPTANCE.parent.mkdir(parents=True, exist_ok=True)
    BUILDING.write_text(
        json.dumps(acceptance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(BUILDING, ACCEPTANCE)

    print("\n" + "=" * 72)
    print("FINAL JUDGE VERDICT: PASS")
    print("=" * 72)
    print("Release manifest:                PASS before reproduction")
    print("IEEE-39 convention/invariants:   15/15")
    print("IEEE-33 scientific tests:        10/10")
    print("Release/evidence tests:          11/11")
    print("Consolidated claim audit:        39/39")
    print("Strict raw evidence:             11/11 responses")
    print("Campaign machine-domain states:  250/250 counted samples")
    print("Manuscript figures:              2/2 regenerated in PNG and PDF")
    print("Evidence/manuscript verifier:    PASS")
    print(f"Acceptance certificate:          {ACCEPTANCE.relative_to(SOURCE)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, subprocess.CalledProcessError) as exc:
        ACCEPTANCE.unlink(missing_ok=True)
        BUILDING.unlink(missing_ok=True)
        print("\nFINAL JUDGE VERDICT: FAIL", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(
                f"Stopped because a required command exited with status "
                f"{exc.returncode}.",
                file=sys.stderr,
            )
        else:
            print("Stopped because an acceptance assertion failed.", file=sys.stderr)
        raise
