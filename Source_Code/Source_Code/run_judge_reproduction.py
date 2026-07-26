#!/usr/bin/env python3
"""Rerun frozen QCi jobs without touching the submitted evidence ledger.

This judge-facing wrapper imports the hash-bound submission/scoring functions
from ``run_live_dirac3.py`` but redirects every new receipt and response to a
fresh ``results/judge_reruns/<label>/`` namespace.  It submits nothing unless
``--submit`` and the mode-specific confirmation phrase are both present.

The original smoke, ten-job campaign, receipts, raw responses, and summaries
are immutable and are never overwritten by this script.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import run_live_dirac3 as frozen


ROOT = Path(__file__).resolve().parent
RERUN_ROOT = ROOT / "results" / "judge_reruns"
CONFIRMATIONS = {
    "smoke": "SUBMIT JUDGE REPRODUCTION SMOKE 3 SAMPLES",
    "evidence": "SUBMIT JUDGE REPRODUCTION EVIDENCE 9 JOBS 225 SAMPLES",
    "characterization": (
        "SUBMIT JUDGE REPRODUCTION CHARACTERIZATION 1 JOB 25 SAMPLES"
    ),
}
CONFIRM_RESUME = "RESUME JUDGE REPRODUCTION MISSING JOBS"


def descriptors_for(mode: str) -> tuple[list[dict], int]:
    if mode == "smoke":
        return [frozen.SMOKE], frozen.SMOKE_SAMPLES
    if mode == "evidence":
        return [
            row for row in frozen.CAMPAIGN
            if row["class"] == "REGISTERED_EVIDENCE"
        ], frozen.SAMPLES
    if mode == "characterization":
        return [
            row for row in frozen.CAMPAIGN
            if row["class"].startswith("CHARACTERIZATION")
        ], frozen.SAMPLES
    raise ValueError(f"unknown reproduction mode: {mode}")


def validate_label(label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", label):
        raise SystemExit("--run-label must contain 1-40 letters, digits, '_' or '-'")


def configure_namespace(label: str) -> Path:
    run_dir = RERUN_ROOT / label
    frozen.LIVE_DIR = run_dir
    frozen.RECEIPTS = run_dir / "receipts"
    frozen.LOCK = run_dir / ".submission.lock"
    return run_dir


def print_plan(mode: str, label: str, protocol: dict) -> None:
    descriptors, samples = descriptors_for(mode)
    print(f"frozen protocol: {protocol['protocol_sha256']}")
    print(f"judge namespace: results/judge_reruns/{label}")
    print(f"mode: {mode}; jobs={len(descriptors)}; samples/job={samples}; total={len(descriptors)*samples}")
    for descriptor in descriptors:
        payload = frozen.normalized_payload(descriptor, samples=samples)
        print(
            f"  {descriptor['id']}: {payload['num_variables']} variables, "
            f"degree {payload['degree']}, class={descriptor['class']}"
        )
    if mode == "characterization":
        print("WARNING: this payload is outside coefficient-resolution guidance.")


def score_namespace(run_dir: Path, protocol: dict) -> dict:
    plan_path = run_dir / "reproduction_plan.json"
    if not plan_path.exists():
        raise SystemExit(f"missing reproduction plan: {plan_path.relative_to(ROOT)}")
    plan = json.loads(plan_path.read_text())
    descriptor_map = {row["id"]: row for row in frozen.CAMPAIGN + [frozen.SMOKE]}
    records = []
    for run_id in plan["run_ids"]:
        descriptor = descriptor_map[run_id]
        matches = sorted(run_dir.rglob(
            f"{run_id}__{protocol['protocol_sha256'][:12]}__*__result.json"
        ))
        if len(matches) != 1:
            records.append({
                "id": run_id,
                "status": "MISSING" if not matches else "DUPLICATE",
                "audit_pass": False,
                "audit_reasons": [f"expected one result, found {len(matches)}"],
            })
            continue
        records.append(
            frozen.score_response(
                descriptor, matches[0], protocol, int(plan["samples_per_job"])
            )
        )
    summary = {
        "evidence_class": "INDEPENDENT_JUDGE_REPRODUCTION",
        "source_protocol_sha256": protocol["protocol_sha256"],
        "run_label": plan["run_label"],
        "mode": plan["mode"],
        "planned_jobs": len(plan["run_ids"]),
        "audit_valid_jobs": sum(bool(row.get("audit_pass")) for row in records),
        "records": records,
    }
    (run_dir / "reproduction_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(
        f"judge reproduction audit: {summary['audit_valid_jobs']}/"
        f"{summary['planned_jobs']} valid"
    )
    print(f"wrote {(run_dir / 'reproduction_summary.json').relative_to(ROOT)}")
    return summary


def descriptors_without_artifacts(descriptors: list[dict], protocol_sha: str) -> list[dict]:
    """Return only never-attempted descriptors; any artifact blocks a retry."""
    return [
        descriptor for descriptor in descriptors
        if not frozen.artifact_paths(descriptor["id"], protocol_sha)
    ]


def submit(mode: str, label: str, confirmation: str | None,
           reserve: float, poll: float, timeout: float) -> int:
    protocol = frozen.load_frozen()
    print_plan(mode, label, protocol)
    expected = CONFIRMATIONS[mode]
    if confirmation != expected:
        raise SystemExit(f"refusing device execution; pass --confirm {expected!r}")
    run_dir = configure_namespace(label)
    if run_dir.exists():
        raise SystemExit(
            f"refusing to reuse judge namespace: {run_dir.relative_to(ROOT)}; "
            "choose a new --run-label or collect the existing attempt"
        )
    descriptors, samples = descriptors_for(mode)
    run_dir.mkdir(parents=True)
    frozen.write_new(run_dir / "reproduction_plan.json", {
        "evidence_class": "INDEPENDENT_JUDGE_REPRODUCTION",
        "source_protocol_sha256": protocol["protocol_sha256"],
        "run_label": label,
        "mode": mode,
        "samples_per_job": samples,
        "run_ids": [row["id"] for row in descriptors],
    })
    client, allocations = frozen.client_and_allocation()
    frozen.print_allocation(allocations)
    frozen.acquire_lock(f"judge_{mode}")
    try:
        for descriptor in descriptors:
            frozen.submit_one(
                client, descriptor, samples, protocol["protocol_sha256"],
                reserve, poll, timeout,
            )
    finally:
        frozen.release_lock()
    summary = score_namespace(run_dir, protocol)
    return 0 if summary["audit_valid_jobs"] == summary["planned_jobs"] else 1


def collect(label: str) -> int:
    protocol = frozen.load_frozen()
    run_dir = configure_namespace(label)
    if not run_dir.exists():
        raise SystemExit(f"judge namespace does not exist: {run_dir.relative_to(ROOT)}")
    frozen.collect()
    summary = score_namespace(run_dir, protocol)
    return 0 if summary["audit_valid_jobs"] == summary["planned_jobs"] else 1


def resume_missing(label: str, confirmation: str | None,
                   reserve: float, poll: float, timeout: float) -> int:
    """Resume only descriptors that have no receipt, result, error, or snapshot."""
    protocol = frozen.load_frozen()
    run_dir = configure_namespace(label)
    plan_path = run_dir / "reproduction_plan.json"
    if not plan_path.exists():
        raise SystemExit(f"missing reproduction plan: {plan_path.relative_to(ROOT)}")
    plan = json.loads(plan_path.read_text())
    descriptor_map = {row["id"]: row for row in frozen.CAMPAIGN + [frozen.SMOKE]}
    try:
        descriptors = [descriptor_map[run_id] for run_id in plan["run_ids"]]
    except KeyError as exc:
        raise SystemExit(f"reproduction plan contains unknown run ID: {exc.args[0]}") from exc
    if confirmation != CONFIRM_RESUME:
        raise SystemExit(
            f"refusing device execution; pass --confirm {CONFIRM_RESUME!r}"
        )

    # First collect any already-submitted terminal jobs. A receipt, timeout
    # snapshot, result, or submission error always blocks resubmission.
    frozen.collect()
    missing = descriptors_without_artifacts(descriptors, protocol["protocol_sha256"])
    print(
        f"resume audit: planned={len(descriptors)}, never-attempted={len(missing)}, "
        f"protected-existing={len(descriptors)-len(missing)}"
    )
    if missing:
        client, allocations = frozen.client_and_allocation()
        frozen.print_allocation(allocations)
        frozen.acquire_lock("judge_resume_missing")
        try:
            for descriptor in missing:
                frozen.submit_one(
                    client, descriptor, int(plan["samples_per_job"]),
                    protocol["protocol_sha256"], reserve, poll, timeout,
                )
        finally:
            frozen.release_lock()
    summary = score_namespace(run_dir, protocol)
    return 0 if summary["audit_valid_jobs"] == summary["planned_jobs"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--evidence", action="store_true")
    mode.add_argument("--characterization", action="store_true")
    mode.add_argument("--collect", action="store_true")
    mode.add_argument("--resume-missing", action="store_true")
    mode.add_argument("--check-allocation", action="store_true")
    parser.add_argument("--run-label", default="judge")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--reserve-seconds", type=float, default=60.0)
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    validate_label(args.run_label)

    if args.check_allocation:
        if args.submit or args.confirm:
            raise SystemExit("--check-allocation does not accept --submit or --confirm")
        client, allocations = frozen.client_and_allocation()
        frozen.print_allocation(allocations)
        return 0
    if args.collect:
        if args.submit or args.confirm:
            raise SystemExit("--collect does not accept --submit or --confirm")
        return collect(args.run_label)
    if args.resume_missing:
        if not args.submit:
            raise SystemExit("--resume-missing requires --submit and the exact confirmation phrase")
        return resume_missing(
            args.run_label, args.confirm, args.reserve_seconds,
            args.poll_seconds, args.timeout_seconds,
        )

    selected = (
        "smoke" if args.smoke else
        "evidence" if args.evidence else
        "characterization"
    )
    protocol = frozen.load_frozen()
    if not args.submit:
        print_plan(selected, args.run_label, protocol)
        print("DRY PLAN: no device contacted; add --submit and the exact confirmation phrase to run.")
        return 0
    return submit(
        selected, args.run_label, args.confirm,
        args.reserve_seconds, args.poll_seconds, args.timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
