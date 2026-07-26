#!/usr/bin/env python3
"""Strictly audit every packaged QCi response, receipt, and remote job config.

The frozen runner's scorer accepts a completed job when at least one raw state
is feasible.  The manuscript makes the stronger, observed claim that *every*
returned state is feasible.  This credential-free sidecar proves that stronger
claim and independently cross-checks the remote ``job_submission`` fields and
the separately written receipt.  It never changes the frozen runner, payloads,
receipts, or raw responses and makes no network call.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_live_dirac3 as frozen_runner  # noqa: E402


FROZEN = ROOT / "results/live_protocol/frozen_campaign.json"
SUMMARY = ROOT / "results/live/hardware_summary.json"
OUTPUT = ROOT / "results/live/strict_evidence_audit.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition: bool, reasons: list[str], message: str) -> None:
    if not condition:
        reasons.append(message)


def allocation_shape(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("seconds"), (int, float))
        and float(value["seconds"]) >= 0.0
        and isinstance(value.get("metered"), bool)
    )


def state_is_feasible(solution: Any, payload: dict) -> bool:
    if not isinstance(solution, list) or len(solution) != payload["num_variables"]:
        return False
    try:
        values = [float(value) for value in solution]
    except (TypeError, ValueError):
        return False
    levels = payload["job_params"].get("num_levels")
    if levels is not None:
        return all(
            abs(value - round(value)) <= 1e-6
            and 0 <= round(value) < int(levels[index])
            for index, value in enumerate(values)
        )
    budget = float(payload["job_params"]["sum_constraint"])
    tolerance = 1e-3 * max(1.0, budget)
    return (
        all(value >= -1e-8 for value in values)
        and abs(sum(values) - budget) <= tolerance
    )


def remote_expected(payload: dict) -> tuple[str, str, dict]:
    params = payload["job_params"]
    if payload["job_type"] == "sample-hamiltonian-integer":
        problem_key = "qudit_hamiltonian_optimization"
        device_key = "dirac-3_qudit"
        config = {
            "num_levels": params["num_levels"],
            "num_samples": params["num_samples"],
            "relaxation_schedule": params["relaxation_schedule"],
        }
    else:
        problem_key = "normalized_qudit_hamiltonian_optimization"
        device_key = "dirac-3_normalized_qudit"
        config = {
            "num_samples": params["num_samples"],
            "relaxation_schedule": params["relaxation_schedule"],
            "sum_constraint": params["sum_constraint"],
        }
    return problem_key, device_key, config


def audit_result(
    descriptor: dict,
    samples: int,
    protocol: dict,
    result_path: Path,
    summary_row: dict | None,
) -> dict:
    reasons: list[str] = []
    payload = frozen_runner.normalized_payload(descriptor, samples=samples)
    response = json.loads(result_path.read_text())
    local = response.get("_local_submission_record") or {}
    job_info = response.get("job_info") or {}
    submission = job_info.get("job_submission") or {}
    results = response.get("results") or {}
    job_id = local.get("job_id")

    check(response.get("status") == "COMPLETED", reasons, "remote status is not COMPLETED")
    check(isinstance(job_id, str) and bool(re.fullmatch(r"[0-9a-f]{24}", job_id)),
          reasons, "local job ID is missing or malformed")
    check(job_info.get("job_id") == job_id, reasons, "remote/local job ID mismatch")
    check(local.get("protocol_sha256") == protocol["protocol_sha256"],
          reasons, "protocol hash mismatch")
    check(local.get("payload_sha256") == sha256(payload["path"]),
          reasons, "payload hash mismatch")
    check(local.get("job_type") == payload["job_type"], reasons, "local job type mismatch")
    check(local.get("job_params") == payload["job_params"], reasons, "local job params mismatch")
    check(local.get("evidence_class") == descriptor["class"],
          reasons, "evidence class mismatch")
    check(local.get("remote_submission_response", {}).get("job_id") == job_id,
          reasons, "submission response job ID mismatch")
    check(allocation_shape(local.get("allocation_before")),
          reasons, "pre-job allocation record is missing or malformed")
    check(allocation_shape(local.get("allocation_after")),
          reasons, "post-job allocation record is missing or malformed")

    receipts = sorted((ROOT / "results/live/receipts").glob(
        f"{descriptor['id']}__{protocol['protocol_sha256'][:12]}__{job_id}__receipt.json"
    )) if isinstance(job_id, str) else []
    check(len(receipts) == 1, reasons, f"expected one separate receipt, found {len(receipts)}")
    receipt = json.loads(receipts[0].read_text()) if len(receipts) == 1 else {}
    if receipt:
        check(all(local.get(key) == value for key, value in receipt.items()),
              reasons, "raw embedded submission record differs from separate receipt")
        check(receipt.get("payload_sha256") == sha256(payload["path"]),
              reasons, "separate receipt payload hash mismatch")

    problem_key, device_key, expected_device_config = remote_expected(payload)
    problem_config = submission.get("problem_config") or {}
    device_config = submission.get("device_config") or {}
    check(set(problem_config) == {problem_key}, reasons, "remote problem-config type mismatch")
    remote_problem = problem_config.get(problem_key) or {}
    check(bool(re.fullmatch(r"[0-9a-f]{24}", str(remote_problem.get("polynomial_file_id", "")))),
          reasons, "remote polynomial file ID is missing or malformed")
    check(set(device_config) == {device_key}, reasons, "remote device-config type mismatch")
    check(device_config.get(device_key) == expected_device_config,
          reasons, "remote device configuration differs from frozen parameters")
    check(submission.get("job_name") == f"qpr_phase3_{descriptor['id']}",
          reasons, "remote job name mismatch")
    tags = submission.get("job_tags") or []
    class_tag = (
        "CHARACTERIZATION"
        if descriptor["class"].startswith("CHARACTERIZATION")
        else descriptor["class"]
    )
    for expected_tag in ("GIC2026", "QCI_Phase3", class_tag, descriptor["id"]):
        check(expected_tag in tags, reasons, f"remote job tag missing: {expected_tag}")

    status = job_info.get("job_status") or {}
    for key in (
        "submitted_at_rfc3339nano", "queued_at_rfc3339nano",
        "running_at_rfc3339nano", "completed_at_rfc3339nano",
    ):
        check(isinstance(status.get(key), str) and bool(status[key]),
              reasons, f"remote status timestamp missing: {key}")
    usage = (job_info.get("job_result") or {}).get("device_usage_s")
    check(isinstance(usage, (int, float)) and float(usage) >= 0.0,
          reasons, "remote device_usage_s is missing or malformed")

    solutions = results.get("solutions") or []
    counts = results.get("counts") or results.get("num_occurrences") or []
    energies = results.get("energies") or []
    check(len(solutions) > 0, reasons, "no returned solutions")
    check(len(counts) == len(solutions), reasons, "solutions/counts length mismatch")
    check(not energies or len(energies) == len(solutions),
          reasons, "solutions/energies length mismatch")
    count_values: list[int] = []
    try:
        count_values = [int(value) for value in counts]
    except (TypeError, ValueError):
        reasons.append("non-integer sample count")
    check(bool(count_values) and all(value > 0 for value in count_values),
          reasons, "sample counts must be positive integers")
    total_samples = sum(count_values)
    check(total_samples == samples, reasons,
          f"counted samples {total_samples}, expected {samples}")
    feasible = [state_is_feasible(solution, payload) for solution in solutions]
    check(bool(feasible) and all(feasible), reasons,
          "at least one returned unique state is raw-infeasible")
    feasible_samples = sum(
        count for count, ok in zip(count_values, feasible) if ok
    ) if len(count_values) == len(feasible) else 0
    check(feasible_samples == samples, reasons,
          "at least one counted sample is raw-infeasible")

    if summary_row is not None:
        check(summary_row.get("job_id") == job_id, reasons, "summary job ID mismatch")
        check(summary_row.get("status") == response.get("status"),
              reasons, "summary status mismatch")
        check(summary_row.get("total_samples_counted") == total_samples,
              reasons, "summary sample total mismatch")
        check(summary_row.get("returned_unique_states") == len(solutions),
              reasons, "summary returned-state count mismatch")
        check(summary_row.get("feasible_unique_states") == sum(feasible),
              reasons, "summary feasible-state count mismatch")

    return {
        "run_id": descriptor["id"],
        "evidence_class": descriptor["class"],
        "job_id": job_id,
        "payload_sha256": sha256(payload["path"]),
        "result_file": result_path.relative_to(ROOT).as_posix(),
        "result_sha256": sha256(result_path),
        "receipt_file": receipts[0].relative_to(ROOT).as_posix() if len(receipts) == 1 else None,
        "receipt_sha256": sha256(receipts[0]) if len(receipts) == 1 else None,
        "remote_job_type": problem_key,
        "remote_device_config_type": device_key,
        "counted_samples": total_samples,
        "returned_unique_states": len(solutions),
        "raw_feasible_unique_states": sum(feasible),
        "raw_feasible_counted_samples": feasible_samples,
        "strict_audit_pass": not reasons,
        "reasons": reasons,
    }


def main() -> int:
    protocol = json.loads(FROZEN.read_text())
    summary = json.loads(SUMMARY.read_text())
    summary_rows = {row["id"]: row for row in summary["records"]}
    frozen_rows = {row["id"]: row for row in protocol["jobs"]}
    records = []
    for descriptor in frozen_runner.CAMPAIGN:
        row = summary_rows.get(descriptor["id"])
        if row is None:
            raise SystemExit(f"missing campaign summary row: {descriptor['id']}")
        payload = frozen_runner.normalized_payload(descriptor)
        registered = frozen_rows.get(descriptor["id"])
        if registered is None or any((
            registered.get("payload_sha256") != sha256(payload["path"]),
            registered.get("job_type") != payload["job_type"],
            registered.get("job_params") != payload["job_params"],
        )):
            raise SystemExit(f"frozen registry mismatch: {descriptor['id']}")
        result_path = ROOT / row["result_file"]
        records.append(audit_result(
            descriptor, frozen_runner.SAMPLES, protocol, result_path, row
        ))

    smoke_matches = sorted((ROOT / "results/live/smoke").glob(
        f"{frozen_runner.SMOKE['id']}__{protocol['protocol_sha256'][:12]}__*__result.json"
    ))
    if len(smoke_matches) != 1:
        raise SystemExit(f"expected one smoke result, found {len(smoke_matches)}")
    smoke = audit_result(
        frozen_runner.SMOKE, frozen_runner.SMOKE_SAMPLES,
        protocol, smoke_matches[0], None,
    )
    all_rows = records + [smoke]
    report = {
        "audit_version": "qpr_qci_phase3_strict_remote_and_raw_v1",
        "evidence_class": "POST_RUN_STRICT_AUDIT_OF_IMMUTABLE_EVIDENCE",
        "protocol_sha256": protocol["protocol_sha256"],
        "campaign_jobs_audited": len(records),
        "campaign_counted_samples": sum(row["counted_samples"] for row in records),
        "campaign_raw_feasible_counted_samples": sum(
            row["raw_feasible_counted_samples"] for row in records
        ),
        "smoke_counted_samples": smoke["counted_samples"],
        "all_remote_configurations_match": all(
            row["strict_audit_pass"] for row in all_rows
        ),
        "all_returned_states_raw_feasible": all(
            row["raw_feasible_unique_states"] == row["returned_unique_states"]
            for row in all_rows
        ),
        "strict_audit_pass": all(row["strict_audit_pass"] for row in all_rows),
        "records": records,
        "smoke": smoke,
    }
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        f"strict live-evidence audit: {sum(row['strict_audit_pass'] for row in all_rows)}/"
        f"{len(all_rows)} results; campaign raw-feasible samples "
        f"{report['campaign_raw_feasible_counted_samples']}/"
        f"{report['campaign_counted_samples']}"
    )
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0 if report["strict_audit_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
