#!/usr/bin/env python3
"""Summarize only the concrete IEEE-39 Dirac-3 payload ladder.

This report intentionally excludes IEEE-118 extrapolations and formulation-
dependent QUBO ancilla counts.  It records the actual payloads generated and
audited in this release, including the distinction between registered
in-spec evidence, local-only verification, and out-of-guidance
characterization.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PAYLOAD_DIR = REPO / "qci" / "ieee39_flagship"


def summarize_group(family: str, entries: list[dict]) -> dict:
    payloads = [json.loads((PAYLOAD_DIR / entry["file"]).read_text()) for entry in entries]
    stats = [payload["device_spec_lint"]["stats"] for payload in payloads]
    roles = sorted({entry["role"] for entry in entries})
    samples = sorted({int(payload["job_params"]["num_samples"]) for payload in payloads})
    schedules = sorted({int(payload["job_params"]["relaxation_schedule"]) for payload in payloads})
    return {
        "case": "case39",
        "payload_family": family,
        "jobs": len(payloads),
        "variables_min": min(int(payload["num_variables"]) for payload in payloads),
        "variables_max": max(int(payload["num_variables"]) for payload in payloads),
        "polynomial_degree": max(int(payload["degree"]) for payload in payloads),
        "terms_min": min(int(row["n_terms"]) for row in stats),
        "terms_max": max(int(row["n_terms"]) for row in stats),
        "samples_per_job": samples[0] if len(samples) == 1 else "mixed",
        "relaxation_schedule": schedules[0] if len(schedules) == 1 else "mixed",
        "hard_device_envelope_pass": all(
            payload["device_spec_lint"]["ok"] for payload in payloads
        ),
        "coefficient_resolution_pass": all(
            row["coefficient_resolution_pass"] for row in stats
        ),
        "evidence_role": ";".join(roles),
    }


def main() -> int:
    manifest = json.loads((PAYLOAD_DIR / "payload_manifest.json").read_text())
    grouped: dict[str, list[dict]] = {}
    for entry in manifest["payloads"]:
        grouped.setdefault(entry["family"], []).append(entry)
    rows = [summarize_group(family, entries) for family, entries in grouped.items()]
    out = REPO / "results" / "phase3_resource_ladder.csv"
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        print(row)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
