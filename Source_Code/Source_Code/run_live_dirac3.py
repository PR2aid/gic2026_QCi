#!/usr/bin/env python3
"""Guarded qBraid runner for the frozen QCi Dirac-3 Phase 3 campaign.

Nothing is submitted by default.  Paid execution requires both ``--submit``
and an exact confirmation phrase.  A single three-sample smoke result is kept
separate from the ten-job, 250-sample evidence/characterization campaign.

Recommended sequence on qBraid::

    python run_all_local.py
    python run_live_dirac3.py --prepare
    python run_live_dirac3.py --check-allocation
    python run_live_dirac3.py --smoke --submit \
      --confirm "SUBMIT QCI SMOKE 3 SAMPLES"
    python run_live_dirac3.py --evidence --submit \
      --confirm "SUBMIT QCI EVIDENCE 10 JOBS 250 SAMPLES"
    python run_live_dirac3.py --collect
    python run_live_dirac3.py --score

Dirac-3 is an analog qudit optimizer.  Its returned samples are not gate-QPU
shots, and qBraid credits are not converted here to device seconds.  The QCi
allocation endpoint is authoritative.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
IEEE39 = ROOT / "ieee39_transmission"
IEEE33 = ROOT / "ieee33_feeder"
PROTOCOL_DIR = ROOT / "results" / "live_protocol"
LIVE_DIR = ROOT / "results" / "live"
RECEIPTS = LIVE_DIR / "receipts"
LOCK = PROTOCOL_DIR / ".submission.lock"

SAMPLES = 25
SCHEDULE = 1
SMOKE_SAMPLES = 3
CONFIRM_SMOKE = "SUBMIT QCI SMOKE 3 SAMPLES"
CONFIRM_EVIDENCE = "SUBMIT QCI EVIDENCE 10 JOBS 250 SAMPLES"
FINAL_STATUSES = {"COMPLETED", "ERRORED", "CANCELLED"}

# Five IEEE-39 dispatch payloads pass both documented coefficient-resolution
# checks.  The cubic payload is deliberately a characterization probe: its 121
# variables fit the degree-3 count limit, but its coefficient resolution does
# not.  Four v6 feeder payloads cover Stages 1/2/3 and the matched cubic-qudit /
# continuous exact-balance comparison.
CAMPAIGN = [
    {"id": "ieee39_hour_h16", "source": "ieee39", "group": "IEEE39 hourly h16-h19",
     "path": "ieee39_transmission/qci/ieee39_flagship/hdisp_c0_s171_h16_polynomial.json",
     "class": "REGISTERED_EVIDENCE"},
    {"id": "ieee39_hour_h17", "source": "ieee39", "group": "IEEE39 hourly h16-h19",
     "path": "ieee39_transmission/qci/ieee39_flagship/hdisp_c0_s171_h17_polynomial.json",
     "class": "REGISTERED_EVIDENCE"},
    {"id": "ieee39_hour_h18", "source": "ieee39", "group": "IEEE39 hourly h16-h19",
     "path": "ieee39_transmission/qci/ieee39_flagship/hdisp_c0_s171_h18_polynomial.json",
     "class": "REGISTERED_EVIDENCE"},
    {"id": "ieee39_hour_h19", "source": "ieee39", "group": "IEEE39 hourly h16-h19",
     "path": "ieee39_transmission/qci/ieee39_flagship/hdisp_c0_s171_h19_polynomial.json",
     "class": "REGISTERED_EVIDENCE"},
    {"id": "ieee39_window_h16_h19", "source": "ieee39", "group": "IEEE39 4-hour window",
     "path": "ieee39_transmission/qci/ieee39_flagship/wdisp_c0_s171_w16_polynomial.json",
     "class": "REGISTERED_EVIDENCE"},
    {"id": "ieee39_cubic_resolution_probe", "source": "ieee39",
     "group": "IEEE39 coefficient-resolution probe",
     "path": "ieee39_transmission/qci/ieee39_flagship/dispatch_c0_s171_polynomial.json",
     "class": "CHARACTERIZATION_OUTSIDE_COEFFICIENT_GUIDANCE"},
    {"id": "ieee33_stage1_balanced", "source": "ieee33", "group": "IEEE33 Stages 1-2",
     "path": "ieee33_feeder/qci_payloads/stage1_balanced_critical_design.json",
     "class": "REGISTERED_EVIDENCE"},
    {"id": "ieee33_stage2_balanced_compound", "source": "ieee33", "group": "IEEE33 Stages 1-2",
     "path": "ieee33_feeder/qci_payloads/stage2_balanced_critical_compound_two_lateral_fault.json",
     "class": "REGISTERED_EVIDENCE"},
    {"id": "ieee33_stage3b_balanced_cubic", "source": "ieee33", "group": "IEEE33 matched Stage 3",
     "path": "ieee33_feeder/qci_payloads/stage3b_balanced_critical_upstream_PCC_outage_MG_lateral_22_24.json",
     "class": "REGISTERED_EVIDENCE"},
    {"id": "ieee33_stage3s_balanced_continuous", "source": "ieee33", "group": "IEEE33 matched Stage 3",
     "path": "ieee33_feeder/qci_payloads/stage3s_balanced_critical_upstream_PCC_outage_MG_lateral_22_24.json",
     "class": "REGISTERED_EVIDENCE"},
]

SMOKE = {
    "id": "smoke_single_cubic_qudit", "source": "ieee33", "group": "isolated smoke",
    "path": "ieee33_feeder/qci_payloads/stage3b_balanced_critical_upstream_PCC_outage_MG_lateral_22_24.json",
    "class": "SMOKE_NOT_EVIDENCE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, default=str)
        handle.write("\n")


def resolution_audit(terms: list[dict], ratio: float = 200.0) -> dict:
    values = sorted({float(term["val"]) for term in terms
                     if float(term["val"]) != 0.0})
    if not values:
        return {"pass": False, "reason": "no nonzero coefficients"}
    max_abs = max(abs(v) for v in values)
    min_abs = min(abs(v) for v in values)
    min_sep = min((b - a for a, b in zip(values, values[1:])), default=None)
    required = max_abs / ratio
    return {
        "pass": bool(max_abs / min_abs <= ratio + 1e-12 and
                     (min_sep is None or min_sep + 1e-12 >= required)),
        "coefficient_spread": max_abs / min_abs,
        "max_abs_coefficient": max_abs,
        "min_pairwise_distinct_separation": min_sep,
        "required_min_pairwise_distinct_separation": required,
    }


def normalized_payload(descriptor: dict, samples: int = SAMPLES) -> dict:
    path = ROOT / descriptor["path"]
    raw = json.loads(path.read_text())
    if descriptor["source"] == "ieee39":
        terms = raw["polynomial"]
        file_config = {"polynomial": {
            "num_variables": int(raw["num_variables"]), "min_degree": 1,
            "max_degree": int(raw["degree"]), "data": terms,
        }}
        encoding = raw.get("encoding", "continuous")
        job_type = "sample-hamiltonian-integer" if encoding == "integer" else "sample-hamiltonian"
        params = {"device_type": "dirac-3", "num_samples": samples,
                  "relaxation_schedule": SCHEDULE}
        if job_type == "sample-hamiltonian":
            params["sum_constraint"] = float(raw["job_params"]["sum_constraint"])
        else:
            params["num_levels"] = [int(x) for x in raw["job_params"]["num_levels"]]
        companion = path.with_name(path.name.replace("_polynomial.json", "_summary.json"))
        constant = json.loads(companion.read_text()).get("constant_offset", 0.0)
        reference = raw.get("classical_reference") or {}
        exact = reference.get("energy") if reference.get("converged") else None
        problem_name = raw["problem_name"]
    else:
        terms = raw["file_config"]["polynomial"]["data"]
        file_config = raw["file_config"]
        hint = raw.get("job_params_hint", {})
        job_type = hint.get("job_type", "sample-hamiltonian-integer")
        params = {"device_type": "dirac-3", "num_samples": samples,
                  "relaxation_schedule": SCHEDULE}
        if job_type == "sample-hamiltonian":
            params["sum_constraint"] = float(hint["sum_constraint"])
        else:
            params["num_levels"] = [int(x) for x in hint["num_levels"]]
        constant = float(raw.get("constant_offset_local_only", 0.0))
        exact = (raw.get("local_exact_ground_state") or {}).get("energy")
        problem_name = raw.get("file_name", path.stem)
    return {
        "descriptor": descriptor, "path": path, "raw": raw, "terms": terms,
        "file": {"file_name": f"{problem_name}_phase3", "file_config": file_config},
        "job_type": job_type, "job_params": params, "constant": float(constant),
        "exact_energy": exact, "resolution": resolution_audit(terms),
        "num_variables": int(file_config["polynomial"]["num_variables"]),
        "degree": int(file_config["polynomial"]["max_degree"]),
    }


def protocol_body() -> dict:
    rows = []
    for descriptor in CAMPAIGN:
        payload = normalized_payload(descriptor)
        in_spec = descriptor["class"] == "REGISTERED_EVIDENCE"
        if in_spec and not payload["resolution"]["pass"]:
            raise RuntimeError(f"registered payload fails coefficient resolution: {descriptor['id']}")
        if not in_spec and payload["resolution"]["pass"]:
            raise RuntimeError("characterization probe unexpectedly passed; review its classification")
        rows.append({
            "id": descriptor["id"], "group": descriptor["group"],
            "evidence_class": descriptor["class"], "payload": descriptor["path"],
            "payload_sha256": sha256(payload["path"]), "job_type": payload["job_type"],
            "job_params": payload["job_params"], "num_variables": payload["num_variables"],
            "degree": payload["degree"], "constant_offset_local_only": payload["constant"],
            "classical_reference_energy": payload["exact_energy"],
            "coefficient_resolution_audit": payload["resolution"],
        })
    return {
        "protocol_version": "qpr_qci_phase3_v7_budget3000_250samples",
        "challenge": "GIC 2026 QCi - Cost Optimization in Resilient Power Grids",
        "device": "QCi Dirac-3 via qBraid", "schedule": SCHEDULE,
        "samples_per_job": SAMPLES, "planned_jobs": len(rows),
        "planned_returned_samples": len(rows) * SAMPLES,
        "registered_evidence_jobs": sum(r["evidence_class"] == "REGISTERED_EVIDENCE" for r in rows),
        "characterization_jobs": sum(r["evidence_class"] != "REGISTERED_EVIDENCE" for r in rows),
        "smoke": {"jobs": 1, "samples": SMOKE_SAMPLES,
                  "evidence_class": "SMOKE_NOT_EVIDENCE"},
        "allocation_rule": ("Query allocations.dirac.seconds before every job; stop if a metered "
                            "balance is at or below the user-selected reserve. Credits are not "
                            "treated as shots or converted to seconds."),
        "scoring_rule": ("Use raw returned vectors; reject infeasible vectors; locally re-evaluate "
                         "float64 polynomial energy with the omitted constant restored; never use "
                         "a repaired vector to create a pass."),
        "no_retry_rule": ("Every receipt/result/error is immutable. A failed or timed-out attempt "
                          "is retained and is not resubmitted automatically."),
        "runner_and_scorer_sha256": sha256(Path(__file__)),
        "requirements_sha256": sha256(ROOT / "requirements.txt"),
        "jobs": rows,
    }


def prepare() -> dict:
    PROTOCOL_DIR.mkdir(parents=True, exist_ok=True)
    body = protocol_body()
    digest = hashlib.sha256(canonical(body)).hexdigest()
    frozen = {**body, "protocol_sha256": digest}
    frozen_path = PROTOCOL_DIR / "frozen_campaign.json"
    if frozen_path.exists():
        existing = json.loads(frozen_path.read_text())
        if existing != frozen:
            raise RuntimeError("frozen campaign differs from current payloads/code; do not overwrite it")
    else:
        write_new(frozen_path, frozen)
        registry = PROTOCOL_DIR / "planned_run_registry.csv"
        with registry.open("x", newline="") as handle:
            fields = ["protocol_sha256", "id", "group", "evidence_class", "payload",
                      "payload_sha256", "schedule", "num_samples", "status"]
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in frozen["jobs"]:
                writer.writerow({
                    "protocol_sha256": digest, "id": row["id"], "group": row["group"],
                    "evidence_class": row["evidence_class"], "payload": row["payload"],
                    "payload_sha256": row["payload_sha256"], "schedule": SCHEDULE,
                    "num_samples": SAMPLES, "status": "PLANNED_NOT_EXECUTED",
                })
    print(f"frozen campaign: {digest}")
    print(f"10 jobs x {SAMPLES} samples = {10*SAMPLES}; smoke = {SMOKE_SAMPLES} separate samples")
    return frozen


def load_frozen() -> dict:
    frozen = prepare()
    current = protocol_body()
    if hashlib.sha256(canonical(current)).hexdigest() != frozen["protocol_sha256"]:
        raise RuntimeError("payload/protocol hash changed after freeze")
    return frozen


def client_and_allocation():
    token = os.environ.get("QCI_TOKEN")
    if not token:
        raise SystemExit("Set QCI_TOKEN in the qBraid terminal; never paste it into the package.")
    from qci_client import QciClient
    client = QciClient(url=os.environ.get("QCI_API_URL", "https://api.qci-prod.com"),
                       api_token=token)
    allocations = client.get_allocations()
    return client, allocations


def dirac_balance(allocations: dict) -> dict:
    record = allocations.get("allocations", {}).get("dirac")
    if not isinstance(record, dict) or "seconds" not in record:
        raise RuntimeError("QCi response has no allocations.dirac.seconds field")
    return {"seconds": float(record["seconds"]), "metered": bool(record.get("metered", True))}


def print_allocation(allocations: dict) -> dict:
    balance = dirac_balance(allocations)
    suffix = "metered" if balance["metered"] else "unmetered"
    print(f"QCi Dirac allocation: {balance['seconds']:.3f} device seconds ({suffix})")
    return balance


def acquire_lock(mode: str) -> None:
    PROTOCOL_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"submission lock exists: {LOCK}; use --collect, or inspect before --unlock") from exc
    with os.fdopen(fd, "w") as handle:
        json.dump({"pid": os.getpid(), "mode": mode, "created_utc": utc_now()}, handle)


def release_lock() -> None:
    LOCK.unlink(missing_ok=True)


def artifact_paths(run_id: str, protocol_sha: str) -> list[Path]:
    prefix = f"{run_id}__{protocol_sha[:12]}"
    return list(LIVE_DIR.rglob(f"{prefix}*"))


def submit_one(client, descriptor: dict, samples: int, protocol_sha: str,
               reserve: float, poll_seconds: float, timeout_seconds: float) -> dict:
    payload = normalized_payload(descriptor, samples=samples)
    run_id = descriptor["id"]
    existing = artifact_paths(run_id, protocol_sha)
    if existing:
        raise RuntimeError(f"immutable attempt already exists for {run_id}: {[p.name for p in existing]}")
    before = print_allocation(client.get_allocations())
    if before["metered"] and before["seconds"] <= reserve:
        raise RuntimeError(f"Dirac allocation {before['seconds']:.3f}s is at/below {reserve:.3f}s reserve")

    class_tag = ("CHARACTERIZATION" if descriptor["class"].startswith("CHARACTERIZATION")
                 else descriptor["class"])
    job_tags = ["GIC2026", "QCI_Phase3", class_tag, run_id[:40]]
    prefix = f"{run_id}__{protocol_sha[:12]}"
    try:
        file_response = client.upload_file(file=payload["file"])
        body = client.build_job_body(
            job_type=payload["job_type"], job_name=f"qpr_phase3_{run_id}",
            job_tags=job_tags, job_params=payload["job_params"],
            polynomial_file_id=file_response["file_id"],
        )
        submission = client.submit_job(job_body=body)
        job_id = submission["job_id"]
        receipt = {
            "id": run_id, "job_id": job_id, "submitted_utc": utc_now(),
            "protocol_sha256": protocol_sha, "evidence_class": descriptor["class"],
            "payload": descriptor["path"], "payload_sha256": sha256(payload["path"]),
            "job_type": payload["job_type"], "job_params": payload["job_params"],
            "allocation_before": before, "remote_submission_response": submission,
        }
        write_new(RECEIPTS / f"{prefix}__{job_id}__receipt.json", receipt)
        print(f"submitted {run_id}: job_id={job_id}")
    except Exception as exc:
        error = {"id": run_id, "created_utc": utc_now(), "protocol_sha256": protocol_sha,
                 "error_type": type(exc).__name__, "error": str(exc),
                 "allocation_before": before}
        write_new(RECEIPTS / f"{prefix}__submission_error.json", error)
        if descriptor["class"].startswith("CHARACTERIZATION"):
            try:
                after = dirac_balance(client.get_allocations())
            except Exception:
                after = None
            response = {
                "status": "SUBMISSION_REJECTED", "job_info": {}, "results": None,
                "submission_error": error,
                "_local_submission_record": {
                    "id": run_id, "job_id": None, "submitted_utc": utc_now(),
                    "protocol_sha256": protocol_sha, "evidence_class": descriptor["class"],
                    "payload": descriptor["path"], "payload_sha256": sha256(payload["path"]),
                    "job_type": payload["job_type"], "job_params": payload["job_params"],
                    "allocation_before": before, "allocation_after": after,
                    "collected_utc": utc_now(),
                },
            }
            out = LIVE_DIR / "characterization" / f"{prefix}__NO_JOB_ID__result.json"
            write_new(out, response)
            print(f"recorded probe submission boundary: {out.relative_to(ROOT)}")
            return response
        raise

    deadline = time.monotonic() + timeout_seconds
    status = "SUBMITTED"
    while status not in FINAL_STATUSES and time.monotonic() < deadline:
        remote = client.get_job_status(job_id=job_id)
        status = str(remote.get("status"))
        print(f"{run_id}: {status}")
        if status not in FINAL_STATUSES:
            time.sleep(poll_seconds)
    response = client.get_job_results(job_id=job_id)
    after = print_allocation(client.get_allocations())
    if status not in FINAL_STATUSES:
        response["_local_submission_record"] = {
            **receipt, "collected_utc": utc_now(), "allocation_after": after,
            "wait_timed_out": True,
        }
        snapshot = RECEIPTS / f"{prefix}__{job_id}__timeout_snapshot.json"
        write_new(snapshot, response)
        raise RuntimeError(f"{run_id} did not reach a terminal status before timeout; "
                           "receipt/snapshot preserved, run --collect later")
    response["_local_submission_record"] = {
        **receipt, "collected_utc": utc_now(), "allocation_after": after,
        "wait_timed_out": False,
    }
    category = ("smoke" if descriptor["class"] == "SMOKE_NOT_EVIDENCE" else
                "characterization" if descriptor["class"].startswith("CHARACTERIZATION") else
                "evidence")
    out = LIVE_DIR / category / f"{prefix}__{job_id}__result.json"
    write_new(out, response)
    print(f"saved immutable response: {out.relative_to(ROOT)}")
    return response


def submit_campaign(mode: str, confirmation: str | None, reserve: float,
                    poll: float, timeout: float) -> None:
    frozen = load_frozen()
    if mode == "smoke":
        expected, descriptors, samples = CONFIRM_SMOKE, [SMOKE], SMOKE_SAMPLES
    else:
        expected, descriptors, samples = CONFIRM_EVIDENCE, CAMPAIGN, SAMPLES
    if confirmation != expected:
        raise SystemExit(f"refusing paid execution; pass --confirm {expected!r}")
    client, allocations = client_and_allocation()
    print_allocation(allocations)
    acquire_lock(mode)
    try:
        for descriptor in descriptors:
            submit_one(client, descriptor, samples, frozen["protocol_sha256"],
                       reserve, poll, timeout)
    finally:
        release_lock()


def collect() -> None:
    frozen = load_frozen()
    client, allocations = client_and_allocation()
    print_allocation(allocations)
    for receipt_path in sorted(RECEIPTS.glob("*__receipt.json")):
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("protocol_sha256") != frozen["protocol_sha256"]:
            print(f"skip foreign protocol receipt: {receipt_path.name}")
            continue
        job_id, run_id = receipt["job_id"], receipt["id"]
        if list(LIVE_DIR.rglob(f"{run_id}__{frozen['protocol_sha256'][:12]}__{job_id}__result.json")):
            continue
        response = client.get_job_results(job_id=job_id)
        if response.get("status") not in FINAL_STATUSES:
            print(f"{run_id}: {response.get('status')} (not final)")
            continue
        response["_local_submission_record"] = {
            **receipt, "collected_utc": utc_now(),
            "allocation_after": dirac_balance(client.get_allocations()),
            "wait_timed_out": False,
        }
        descriptor = next((d for d in CAMPAIGN + [SMOKE] if d["id"] == run_id), None)
        category = ("smoke" if descriptor and descriptor["class"] == "SMOKE_NOT_EVIDENCE" else
                    "characterization" if descriptor and descriptor["class"].startswith("CHARACTERIZATION") else
                    "evidence")
        out = LIVE_DIR / category / f"{run_id}__{frozen['protocol_sha256'][:12]}__{job_id}__result.json"
        write_new(out, response)
        print(f"collected {out.relative_to(ROOT)}")


def evaluate(terms: list[dict], solution: list[float], constant: float) -> float:
    total = float(constant)
    for term in terms:
        product = 1.0
        for index in term["idx"]:
            if int(index) > 0:
                product *= float(solution[int(index) - 1])
        total += float(term["val"]) * product
    return float(total)


def score_response(descriptor: dict, path: Path, frozen: dict, samples: int) -> dict:
    response = json.loads(path.read_text())
    local = response.get("_local_submission_record", {})
    payload = normalized_payload(descriptor, samples=samples)
    reasons = []
    status = response.get("status")
    is_characterization = descriptor["class"].startswith("CHARACTERIZATION")
    accepted_characterization_boundary = is_characterization and status in {
        "ERRORED", "CANCELLED", "SUBMISSION_REJECTED"
    }
    if status != "COMPLETED" and not accepted_characterization_boundary:
        reasons.append(f"status={status!r}")
    if not local.get("job_id") and not (is_characterization and status == "SUBMISSION_REJECTED"):
        reasons.append("missing job_id")
    if local.get("protocol_sha256") != frozen["protocol_sha256"]:
        reasons.append("protocol hash mismatch")
    if local.get("payload_sha256") != sha256(payload["path"]):
        reasons.append("payload hash mismatch")
    if local.get("job_type") != payload["job_type"]:
        reasons.append("job type mismatch")
    params = local.get("job_params", {})
    for key, expected in payload["job_params"].items():
        if params.get(key) != expected:
            reasons.append(f"job_params.{key} mismatch")
    if local.get("allocation_after") is None and not accepted_characterization_boundary:
        reasons.append("missing post-job allocation record")
    usage = (response.get("job_info", {}).get("job_result", {}) or {}).get("device_usage_s")
    if usage is None and not accepted_characterization_boundary:
        reasons.append("missing device_usage_s")

    results = response.get("results") or {}
    solutions = results.get("solutions") or []
    counts = results.get("counts") or results.get("num_occurrences") or []
    if counts:
        total_samples = sum(int(x) for x in counts)
    else:
        total_samples = len(solutions)
        counts = [1] * len(solutions)
    if status == "COMPLETED" and total_samples != samples:
        reasons.append(f"returned sample count={total_samples}, expected={samples}")
    if accepted_characterization_boundary and total_samples != 0:
        reasons.append(f"rejected characterization unexpectedly contains {total_samples} samples")

    feasible, energies = [], []
    num_levels = payload["job_params"].get("num_levels")
    sum_constraint = payload["job_params"].get("sum_constraint")
    for index, solution in enumerate(solutions):
        ok = len(solution) == payload["num_variables"]
        if ok and num_levels is not None:
            ok = all(abs(float(x) - round(float(x))) <= 1e-6 and
                     0 <= round(float(x)) < num_levels[i]
                     for i, x in enumerate(solution))
        elif ok:
            tolerance = 1e-3 * max(1.0, float(sum_constraint))
            ok = all(float(x) >= -1e-8 for x in solution) and abs(
                sum(float(x) for x in solution) - float(sum_constraint)
            ) <= tolerance
        if ok:
            feasible.append(index)
            energies.append((evaluate(payload["terms"], solution, payload["constant"]), index))
    best_energy, best_index = min(energies) if energies else (None, None)
    reference = payload["exact_energy"]
    gap = best_energy - float(reference) if best_energy is not None and reference is not None else None
    rel_gap = gap / max(1.0, abs(float(reference))) if gap is not None else None
    if status == "COMPLETED" and not feasible:
        reasons.append("no raw feasible returned state")
    return {
        "id": descriptor["id"], "group": descriptor["group"],
        "evidence_class": descriptor["class"], "result_file": str(path.relative_to(ROOT)),
        "status": status, "job_id": local.get("job_id"),
        "job_type": local.get("job_type"), "num_samples_requested": samples,
        "total_samples_counted": total_samples, "device_usage_s": usage,
        "feasible_unique_states": len(feasible), "returned_unique_states": len(solutions),
        "best_feasible_index": best_index,
        "device_reported_energy_at_best": ((results.get("energies") or [None] * len(solutions))[best_index]
                                           if best_index is not None and len(results.get("energies") or []) > best_index
                                           else None),
        "best_energy_local_float64_constant_restored": best_energy,
        "classical_reference_energy": reference, "gap": gap, "relative_gap": rel_gap,
        "coefficient_resolution_audit": payload["resolution"],
        "characterization_outcome": ("SAMPLES_RETURNED" if is_characterization and status == "COMPLETED"
                                      else status if is_characterization else None),
        "audit_pass": not reasons, "audit_reasons": reasons,
    }


def score() -> int:
    frozen = load_frozen()
    records = []
    for descriptor in CAMPAIGN:
        matches = sorted(LIVE_DIR.rglob(
            f"{descriptor['id']}__{frozen['protocol_sha256'][:12]}__*__result.json"))
        if len(matches) != 1:
            records.append({"id": descriptor["id"], "group": descriptor["group"],
                            "evidence_class": descriptor["class"], "status": "MISSING" if not matches else "DUPLICATE",
                            "audit_pass": False,
                            "audit_reasons": [f"expected one immutable result, found {len(matches)}"]})
            continue
        records.append(score_response(descriptor, matches[0], frozen, SAMPLES))

    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    summary = {
        "protocol_sha256": frozen["protocol_sha256"], "created_utc": utc_now(),
        "planned_jobs": len(CAMPAIGN), "planned_samples": len(CAMPAIGN) * SAMPLES,
        "completed_jobs": sum(r.get("status") == "COMPLETED" for r in records),
        "valid_audited_jobs": sum(bool(r.get("audit_pass")) for r in records),
        "registered_jobs_valid": sum(r.get("audit_pass") and r["evidence_class"] == "REGISTERED_EVIDENCE" for r in records),
        "device_usage_s_total": sum(float(r.get("device_usage_s") or 0.0) for r in records),
        "records": records,
    }
    (LIVE_DIR / "hardware_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    fields = ["id", "group", "evidence_class", "status", "job_id", "num_samples_requested",
              "total_samples_counted", "device_usage_s", "best_energy_local_float64_constant_restored",
              "classical_reference_energy", "relative_gap", "audit_pass", "audit_reasons"]
    with (LIVE_DIR / "hardware_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow({**row, "audit_reasons": "; ".join(row.get("audit_reasons", []))})

    groups = []
    for group in dict.fromkeys(row["group"] for row in CAMPAIGN):
        subset = [r for r in records if r["group"] == group]
        completed = sum(r.get("status") == "COMPLETED" for r in subset)
        valid = sum(bool(r.get("audit_pass")) for r in subset)
        samples = sum(int(r.get("total_samples_counted") or 0) for r in subset)
        seconds = sum(float(r.get("device_usage_s") or 0.0) for r in subset)
        gaps = [float(r["relative_gap"]) for r in subset if r.get("relative_gap") is not None]
        gap_text = (f"{min(gaps):.3g} to {max(gaps):.3g}" if gaps else "n/a")
        groups.append((group, len(subset), completed, valid, samples, seconds, gap_text))
    lines = ["# Machine-generated live QCi Dirac-3 summary", "",
             f"Protocol SHA-256: `{frozen['protocol_sha256']}`", "",
             "| Registered table row | jobs | completed | audit-valid | counted samples | device s | relative gap range |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for row in groups:
        lines.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} | {row[5]:.3f} | {row[6]} |")
    lines += ["", "The cubic probe is outside the documented coefficient-resolution guidance and is not counted as in-spec evidence.",
              "All scored energies are local float64 re-evaluations of raw feasible states with omitted constants restored."]
    (LIVE_DIR / "LIVE_RESULTS.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {LIVE_DIR / 'hardware_summary.json'}")
    print(f"valid jobs: {summary['valid_audited_jobs']}/{summary['planned_jobs']}")
    return 0 if summary["valid_audited_jobs"] == summary["planned_jobs"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--check-allocation", action="store_true")
    action.add_argument("--smoke", action="store_true")
    action.add_argument("--evidence", action="store_true")
    action.add_argument("--collect", action="store_true")
    action.add_argument("--score", action="store_true")
    action.add_argument("--unlock", action="store_true")
    ap.add_argument("--submit", action="store_true", help="required for a paid smoke/evidence action")
    ap.add_argument("--confirm", help="exact paid-execution confirmation phrase")
    ap.add_argument("--reserve-dirac-seconds", type=float, default=60.0,
                    help="stop before a job when a metered balance is at/below this reserve")
    ap.add_argument("--poll-seconds", type=float, default=10.0)
    ap.add_argument("--timeout-seconds", type=float, default=3600.0)
    args = ap.parse_args()

    if args.unlock:
        if LOCK.exists():
            print(f"removing inspected stale lock: {LOCK.read_text(errors='replace')}")
            LOCK.unlink()
        return 0
    if args.check_allocation:
        _, allocations = client_and_allocation()
        print_allocation(allocations)
        return 0
    if args.collect:
        collect(); return 0
    if args.score:
        return score()
    if args.smoke or args.evidence:
        mode = "smoke" if args.smoke else "evidence"
        frozen = load_frozen()
        print(f"planned {mode}; protocol {frozen['protocol_sha256']}")
        if not args.submit:
            print("DRY PLAN ONLY: add --submit and the exact --confirm phrase to contact QCi")
            return 0
        submit_campaign(mode, args.confirm, args.reserve_dirac_seconds,
                        args.poll_seconds, args.timeout_seconds)
        return 0
    prepare()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
