# Quantum Pattern Recognition — QCi Phase 3

- **Team:** Quantum Pattern Recognition
- **Project:** Device-Native Dirac-3 Optimization for Cost and Resilience in Hierarchical Microgrids
- **Challenge track:** Global Industry Challenge 2026 — Quantum Computing Inc., *Cost Optimization in Resilient Power Grids*
- **Submission archive:** `QuantumPatternRecognition_QCI_Phase3.zip`
- **Repository:** <https://github.com/PR2aid/gic2026_QCi>

### Intellectual-property and licensing boundary

This public repository is created as part of Global Industrial Challenge phase 3 submission, 
and is a propetry of the the provisional pattent owners (who are not limitted to the named participants in the attached QuantumPatternRecognition__Phase3_Version14.pdf).
The supplied implementation and evidence are limited to the files in this
archive and are sufficient for the credential-free reproduction described
below.

Certain subject matter in and related to this submission is disclosed in
pending Australian provisional patent applications and their related publication (referenced in the QuantumPatternRecognition__Phase3_Version14.pdf). 
For enquiries about a licence concerning rights not already
granted under the competition instruments, contact <admin@pr2aid.com> or
<azadeh.alavi@rmit.edu.au>.

## Quick start

1. Launch the repository on qBraid or upload and extract the submission ZIP.
2. Open `Source_Code/RUN_ON_QBRAID.ipynb` and run every cell from top to bottom.
3. Accept the reproduction only if the initial release-manifest gate and every
   scientific, evidence, figure, and manuscript gate report `PASS`.

Archived hardware re-scoring is credential-free. A new stochastic QCi run is
optional, allocation-dependent, and not part of the acceptance path.

## Repository availability and Launch on qBraid

The Launch button targets the repository identified above. Public, sign-in-free
repository access is required for one-click launch and must be verified before
submission. The submitted ZIP is the complete independent reproduction path.

[<img src="https://qbraid-static.s3.amazonaws.com/logos/Launch_on_qBraid_white.png" width="150">](https://account.qbraid.com?gitHubUrl=https%3A%2F%2Fgithub.com%2FPR2aid%2Fgic2026_QCi.git&redirectUrl=Source_Code%2FRUN_ON_QBRAID.ipynb)

qBraid Lab's **Subscription → Small** instance has 2 vCPU and 4 GB and is
sufficient. The qBraid Lab compute-hour allowance is separate from any QCi
Dirac-3 allocation or metering. No repository credential, QCi token, qBraid
password, paid hardware call, or other secret is required for the submitted
credential-free reproduction.

## 1. Submission contents

The ZIP has exactly three root entries:

| Entry | Purpose |
|---|---|
| `QuantumPatternRecognition__Phase3_Version14.pdf` | Official organizer cover, five technical pages, and one references page; the five technical pages use 11-point Times New Roman and single line spacing |
| `Source_Code/` | Executable source, pinned direct dependencies, public inputs, simulations, classical baselines, immutable QCi evidence, tests, and licence |
| `README.md` | This judge-facing reproduction guide |

The PDF is the only submitted write-up. Supporting verifier inputs are included
under `Source_Code/`. The source is supplied for competition evaluation under
`Source_Code/LICENSE`; no general public software licence is granted.

## 2. Credential-free reproduction on qBraid

**Path convention:** every archive path and command below uses the qBraid/Linux
forward slash (`/`). Run commands from the extracted submission root unless a
section expressly says otherwise.

### Option A — upload the ZIP

1. In qBraid, launch **Lab → Subscription → Small**.
2. Upload `QuantumPatternRecognition_QCI_Phase3.zip`.
3. In qBraid's Linux terminal, run the commands below.
4. Open `~/QCI_P3/Source_Code/RUN_ON_QBRAID.ipynb`.
5. Run all cells from top to bottom.

From the directory containing the uploaded ZIP, use a new extraction directory.
The commands below require `~/QCI_P3` not to exist; if it does, replace
`QCI_P3` consistently with another new directory name.

```bash
mkdir ~/QCI_P3
unzip -q QuantumPatternRecognition_QCI_Phase3.zip -d ~/QCI_P3
cd ~/QCI_P3
python Source_Code/scripts/verify_release_manifest.py
```

The manifest must report `PASS` before the notebook regenerates any output.
Afterward, the manifest is expected to fail in that working directory because
the notebook regenerates results, figures, and timing metadata. Re-extract the
ZIP into a new empty directory before checking release integrity again.

### Option B — use Launch on qBraid

1. Select the Launch button above after the repository is public.
2. qBraid opens `Source_Code/RUN_ON_QBRAID.ipynb`.
3. Run all cells from top to bottom.

The notebook works when qBraid starts in either the repository root or
`Source_Code/`. It first verifies the untouched release manifest, then creates
an isolated virtual environment, installs the pinned direct dependencies,
regenerates both studies and both figures, checks the write-up against the
packaged evidence, and applies every acceptance gate.

Dependency installation is the only credential-free step requiring network
access. The scientific run uses only packaged public inputs and immutable
evidence and never submits hardware work.

### Terminal equivalent

Start at the extracted submission root and remain there:

```bash
python Source_Code/scripts/verify_release_manifest.py

python --version                  # supported: Python 3.11 or 3.12
python -m venv ~/qci-phase3-judge-venv
source ~/qci-phase3-judge-venv/bin/activate
python -m pip install --disable-pip-version-check -r Source_Code/requirements-docs.txt

python Source_Code/run_all_local.py
python Source_Code/figures/make_figures.py
python Source_Code/scripts/integrate_live_results.py
```

`Source_Code/run_all_local.py` makes no network or hardware call. It
regenerates the IEEE-33 and IEEE-39 studies and matched classical baselines,
re-scores every packaged QCi response, checks remote configurations and
receipts, runs the physical-decode boundary audit, certifies the 13-variable
convex reference, and executes all tests.

The terminal sequence runs the same scientific and evidence checks, but only
the recommended notebook creates the notebook-level acceptance certificate
described next.

### Results and acceptance

The notebook prints progress on screen and saves machine-readable results. Its
authoritative outcome is
`Source_Code/results/reproduction_acceptance.json`. That file is intentionally
absent from the submitted archive. The notebook creates it only after the
current run completes every required command and assertion successfully.

A successful run must satisfy both checks:

1. The complete nine-line summary shown below appears on screen.
2. `Source_Code/results/reproduction_acceptance.json` contains `"status":
   "PASS"`.

The principal saved outputs are:

| Output after the run | Purpose and expected result |
|---|---|
| `Source_Code/results/reproduction_acceptance.json` | Fresh acceptance-path certificate; must contain `"status": "PASS"` |
| `Source_Code/results_summary.json` | Regenerated numerical summary; must contain `"all_checks_passed": true`, `"checks_passed": 39`, and `"checks_total": 39` |
| `Source_Code/results/live/hardware_summary.json` | Detailed local re-scoring of the packaged campaign; 10/10 audit-valid jobs and 250 counted campaign samples |
| `Source_Code/results/live/strict_evidence_audit.json` | Receipt, returned-configuration, protocol, payload, and raw-state audit; campaign plus smoke give 11/11 responses |
| `Source_Code/results/live/physical_decode_audit.json` | Separate physical interpretation, including 72/100 hourly raw samples within equipment caps and 1/4 machine-objective best states cap-compliant |
| `Source_Code/results/live/certified_hardware_analysis.json` | Analytic KKT comparison for the 13-variable four-hour window; certification must pass |
| `Source_Code/ieee39_transmission/results/` | Regenerated IEEE-39 portfolio, scenario, invariant, dispatch, and resource results |
| `Source_Code/ieee33_feeder/results/` | Regenerated IEEE-33 Pareto, baseline, AC-screen, and 96-case chronology results |
| `Source_Code/figures/figure1_architecture.png`, `Source_Code/figures/figure1_architecture.pdf`, `Source_Code/figures/figure2_results.png`, and `Source_Code/figures/figure2_results.pdf` | Two regenerated figures in PNG and PDF |

The comparisons are automatic; judges do not need to copy numbers from the
screen or compare files manually:

1. The initial manifest gate compares every manifest-declared released path,
   byte size, and SHA-256 with `Source_Code/RELEASE_MANIFEST.json`.
2. The scientific runner compares regenerated IEEE-33 and IEEE-39 values with
   the 39 predeclared exact or tolerance-gated claim targets and classical
   oracles.
3. Payload regeneration is checked against the 42 shipped, hash-bound payload
   files and those exact shipped bytes are restored before scoring.
4. Archived QCi responses are checked against the frozen protocol, payload and
   receipt hashes, returned configurations, sample counts, registered domains,
   and locally re-evaluated objectives.
5. The figure script reads regenerated results. The final verifier checks the
   packaged write-up source against the audited evidence; it does not rewrite
   the submitted PDF or compare figures pixel-for-pixel.

The notebook's acceptance-summary cell emits these nine invariant lines
exactly:

```text
Release manifest:                PASS before reproduction
IEEE-39 convention/invariants:   15/15
IEEE-33 scientific tests:        10/10
Release/evidence tests:          11/11
Consolidated claim audit:        39/39
Strict raw evidence:             11/11 responses
Campaign machine-domain states:  250/250 counted samples
Manuscript figures:              2/2 regenerated in PNG and PDF
Evidence/manuscript verifier:    PASS
```

It then prints one environment-specific timing line from
`Source_Code/results_summary.json` and the path of the new acceptance
certificate. The terminal path prints the equivalent per-stage lines,
including the 39/39 consolidated audit, the 11/11 strict audit, and both
unittest `OK` results.

The counters mean:

- `39/39` is the numerical and structural claim audit, not a job count.
- `11/11 responses` is the ten-response campaign plus the separate three-sample
  smoke response.
- `250/250 counted samples` is the campaign's 225 in-spec samples plus 25
  separately labelled characterization-probe samples. It excludes smoke and
  means registered integer/simplex machine-domain feasibility—not 250 optima
  and not 250 fully physical dispatches.
- `72/100` is the separate hourly equipment-cap diagnostic. It is reported
  without repairing raw states and is not a failure of the registered
  machine-domain audit.
- `2/2` means both figures were regenerated in both required formats.

The pre-packaged `Source_Code/results_summary.json` is regenerated in place, so
its presence alone does not prove that the current run completed. Reject the
reproduction if the initial manifest fails, a command exits nonzero, a
traceback or failed assertion appears, the nine-line summary is incomplete, or
the new acceptance certificate does not report `PASS`.

Warnings that the selection payload is retained for local exact analysis and
that the 121-variable cubic payload is an out-of-resolution characterization
probe are expected disclosures, not failed acceptance gates. No other
`FAIL` or unexpected warning should be ignored.

The default notebook makes no new QCi hardware call and never replaces the
packaged receipts or raw responses. Its last cell only prints a safe smoke
rerun plan and creates no hardware result. If a judge deliberately authorizes
an optional stochastic QCi rerun, new artifacts are isolated under
`Source_Code/results/judge_reruns/<run-label>/`; after collection, its summary
is `Source_Code/results/judge_reruns/<run-label>/reproduction_summary.json`.
Those stochastic samples need not be byte-identical to the packaged samples.

The final local Python 3.12 audit on 24 July 2026 recorded 6.2 seconds for the
scientific core. This excludes dependency installation and figure rendering;
elapsed time varies by host. Regenerated provenance and timing fields such as
`created_utc`, `wall_seconds`, `selection_milp_wall_s`, and `*_runtime_ms*`
will vary. Scientific objectives, selections, counts, feasibility results,
gaps, and sponsor metrics are deterministic or tolerance-gated by the 39/39
claim audit.

## 3. What the reproduction verifies

| Result class | Main output | Verification |
|---|---|---|
| Consolidated claims | `Source_Code/results_summary.json` | 39 exact or tolerance-gated cost, resilience, ownership, probability, payload, AC, chronology, and evidence claims |
| IEEE-39 planning | `Source_Code/ieee39_transmission/results/end_to_end_case39.json` | 200 seeded scenarios; 15 retained cases with total probability 1; one fixed portfolio checked by exact enumeration and HiGHS |
| IEEE-39 invariants | `Source_Code/ieee39_transmission/results/convention_test_summary.json` | 15/15 gates covering connected disjoint zones, exact bus ownership, full PCC domain, probability, and frozen evaluation |
| Dirac resource ladder | `Source_Code/ieee39_transmission/results/phase3_resource_ladder.csv` | Actual variable counts, polynomial degree, terms, schedule, and coefficient-resolution class |
| IEEE-33 frontier | `Source_Code/ieee33_feeder/results/design_pareto.csv` | Exact three-policy cost/critical-service frontier |
| Matched baselines | `Source_Code/ieee33_feeder/results/classical_baseline_summary.json` | Exact oracles and 30 seeded stochastic runs at exactly 25 objective evaluations per seed |
| Physical simulations | `Source_Code/ieee33_feeder/results/ac_powerflow/` and `Source_Code/ieee33_feeder/results/chronology_audit/` | Nonlinear radial AC screen and 96 deterministic 24-hour SOC/black-start cases |
| Archived QCi scoring | `Source_Code/results/live/hardware_summary.json` | Frozen hashes, job IDs, sample counts, device seconds, domain feasibility, and identical-polynomial gaps |
| Strict evidence audit | `Source_Code/results/live/strict_evidence_audit.json` | Receipts, returned remote configurations, all raw states, and all 250 campaign samples |
| Physical-decode boundary | `Source_Code/results/live/physical_decode_audit.json` | Equipment-cap and hourly-balance diagnostics without repairing hardware evidence |
| Convex/timing certificate | `Source_Code/results/live/certified_hardware_analysis.json` | Analytic KKT oracle and wall/device times derived from immutable UTC receipts |

There is no gate-circuit simulator result. “Simulation” refers to disclosed
scenario generation, grid/AC-power-flow, chronology, exact enumeration, HiGHS,
deterministic continuous-oracle, and seeded simulated-annealing calculations.

Headline outputs are:

| Instance | Reproduced result |
|---|---:|
| IEEE-39 fixed portfolio | `{0,3}`, chosen once before all 15 retained scenarios |
| IEEE-39 expected 24-hour energy-plus-VoLL cost | USD 6.811M → USD 5.537M |
| IEEE-39 expected total annual cost, including upgrades | USD 2.486B → USD 2.242B |
| IEEE-39 expected total annual saving | USD 243.70M/year (9.80%) |
| IEEE-39 maximum load-weighted system fraction unserved per hour | 0.1932 → 0.0672 |
| IEEE-39 expected critical outage hours | 0.080 → 0.000 |
| IEEE-39 selected-zone coverage | 4/21 load buses; 27.31% of system MW |
| IEEE-33 Plan A | USD 13.42M; 21.0 MWh/year expected annual critical energy not served |
| IEEE-33 Plan B | USD 16.44M; 4.2 MWh/year expected annual critical energy not served |
| IEEE-33 Plan C | USD 19.46M; 0.0 MWh/year expected annual critical energy not served |
| Registered coefficient-resolution audits | 27/27 feeder and 5/5 IEEE-39 dispatch payloads pass |

The IEEE-39 candidate set is a deterministic connected partition: every bus
has one owner, overlap is zero, candidate load sums to the 6,254.23 MW system
load, the complete 14-candidate PCC domain is sampled, and retained probability
is exactly 1. The selected portfolio is frozen across all scenarios. The
system-level portfolio is produced by the classical stochastic master;
Dirac-3 solves or benchmarks the registered dispatch and feeder polynomial
subproblems.

## 4. Completed QCi Dirac-3 evidence

The immutable protocol SHA-256 is:

```text
0c97a069f187aa4627458c8a59b9add1673083404cf1a496863ab8294b20a09f
```

The packaged evidence contains:

- one isolated three-sample smoke job;
- nine registered in-spec jobs × 25 samples = 225 evidence samples;
- one separately labelled out-of-guidance characterization job × 25 samples;
- 250/250 campaign samples returned across 10/10 audited jobs;
- 46 QCi-reported device seconds for the nine in-spec jobs;
- 320 device seconds and 427.613 seconds from first submission to final
  collection for all ten campaign jobs; and
- 274 device seconds attributable to the 121-variable characterization probe.

Dirac-3 is an analog qudit optimizer. **Gate-model qubit count, circuit depth,
and shots are not applicable.** Relevant resources are variables/qudits,
polynomial degree, integer levels or continuous sum constraint, relaxation
schedule, returned samples, coefficient resolution, and device seconds.

The largest in-spec job has 13 continuous variables at degree 2. Native-cubic
cases use one ternary qudit or two continuous variables. One ternary qudit
directly represents three valid states; two binary bits contain an unused
fourth state, while one-hot encoding requires three bits plus a constraint.
The 121-variable degree-3 payload fits the variable-count envelope but fails
coefficient-resolution guidance and is characterization only.

The evidence demonstrates platform-native representation and verified solution
quality, not computational speedup. Exact enumeration solves the 2,048-state
IEEE-33 Stage-1 instance in 5.6 ms median over ten repeats.

### Machine-domain and physical feasibility are distinct

All campaign states pass their registered integer or non-negative-simplex
domains. The IEEE-39 hourly simplex enforces raw balance, while equipment
capacities are soft objective walls. The physical-decode audit finds 72/100
hourly raw samples within those caps, and only 1/4 machine-objective best hourly
states is cap-compliant. Best-state overruns are 17.903 MW at h17, 2.127 MW at
h18, and 2.102 MW at h19; h16 has none.

The 13-variable result is a certified matched-objective result for a four-hour
total-energy relaxation, not a certificate of hourly physical dispatch. Its
best state has a 45.425 MW maximum cap overrun and a 300.305 MW maximum
pre-repair per-hour dispatch-minus-load mismatch. The audits never project,
repair, or replace a raw state for hardware credit.

The immutable `protocol_version` value `budget3000` is an opaque hash-bound
identifier and does not describe this track's allocation. The captured QCi
allocation was 259,200 unmetered Dirac seconds; the workflow does not convert
qBraid Lab compute hours or credits into Dirac seconds.

## 5. Optional QCi hardware rerun — not required for acceptance

The submitted results are fully verifiable without new hardware. A fresh QCi
execution is stochastic, may consume the reviewer's allocation, and need not
return identical sample vectors. It reproduces the frozen payload and scoring
protocol, not deterministic samples.

Use only a **QCi API token issued in the QCi access portal**. Do not enter a QCi
account password, qBraid password, qBraid token, or other credential. Never
paste the API token into a notebook, source file, README, chat, or visible
command line.

From the extracted submission root, activate the environment and capture the
token without displaying it:

```bash
source ~/qci-phase3-judge-venv/bin/activate

read -rsp "Paste QCi API token (hidden): " QCI_TOKEN
export QCI_TOKEN
export QCI_API_URL="https://api.qci-prod.com"
echo "Token captured without display."

qbraid account credits
python Source_Code/run_judge_reproduction.py --check-allocation
```

Inspect QCi allocation, metering status, qBraid balance, and any displayed cost
before authorizing a job. A reviewer's allocation or pricing may differ.

First inspect the three-sample smoke plan; this submits nothing:

```bash
python Source_Code/run_judge_reproduction.py --smoke --run-label judge_smoke_1
```

Only after reviewing the plan, submit the isolated smoke:

```bash
python Source_Code/run_judge_reproduction.py --smoke --run-label judge_smoke_1 --submit --confirm "SUBMIT JUDGE REPRODUCTION SMOKE 3 SAMPLES"
```

Recheck cost and allocation:

```bash
qbraid account credits
python Source_Code/run_judge_reproduction.py --check-allocation
```

Only if the smoke and actual charge are acceptable, dry-run and submit the nine
in-spec evidence jobs:

```bash
python Source_Code/run_judge_reproduction.py --evidence --run-label judge_evidence_1

python Source_Code/run_judge_reproduction.py --evidence --run-label judge_evidence_1 --submit --confirm "SUBMIT JUDGE REPRODUCTION EVIDENCE 9 JOBS 225 SAMPLES"
```

If a terminal disconnects, collect existing job IDs:

```bash
python Source_Code/run_judge_reproduction.py --collect --run-label judge_evidence_1
```

If planned jobs remain unsubmitted, guarded resume mode submits only entries
with neither a receipt nor a result:

```bash
python Source_Code/run_judge_reproduction.py --resume-missing --run-label judge_evidence_1 --submit --confirm "RESUME JUDGE REPRODUCTION MISSING JOBS"
```

Successful optional reruns write:

```text
Source_Code/results/judge_reruns/<run-label>/reproduction_plan.json
Source_Code/results/judge_reruns/<run-label>/reproduction_summary.json
```

The summary should report `1/1 valid` for smoke or `9/9 valid` for the in-spec
evidence campaign.

The 121-variable characterization job is unnecessary for scoring or submitted
result verification. If a reviewer deliberately wants a new characterization
result, first recheck allocation and displayed cost, then inspect the dry plan:

```bash
python Source_Code/run_judge_reproduction.py --characterization --run-label judge_characterization_1
```

Only after accepting both cost and out-of-guidance status:

```bash
python Source_Code/run_judge_reproduction.py --characterization --run-label judge_characterization_1 --submit --confirm "SUBMIT JUDGE REPRODUCTION CHARACTERIZATION 1 JOB 25 SAMPLES"
```

Its expected audit is `1/1 valid`; this validates remote configuration, raw
state, and scoring protocol. It does not convert the probe into in-spec
optimization evidence.

Remove secrets from the terminal environment when finished:

```bash
unset QCI_TOKEN QCI_API_URL
```

Do not invoke `Source_Code/run_live_dirac3.py` with `--submit`, `--collect`, or
`--unlock`. It is the immutable campaign runner/scorer. New executions must use
`Source_Code/run_judge_reproduction.py`, which writes only under
`Source_Code/results/judge_reruns/<run-label>/` and cannot overwrite submitted
evidence.

## 6. Inputs and release integrity

The run uses:

- packaged public IEEE-33 and IEEE-39 data;
- disclosed synthetic DER cost, event, critical-load, and profile assumptions;
- the frozen payload registry and protocol;
- unchanged archived QCi receipts and raw responses; and
- fixed random seeds.

It requires no proprietary or inaccessible dataset. The principal result files
and their acceptance values are listed in **Results and acceptance** above.

`Source_Code/RELEASE_MANIFEST.json` records the SHA-256 and byte size of every
released file except itself. Run this integrity check immediately after
extraction and before reproduction:

```bash
python Source_Code/scripts/verify_release_manifest.py
```

The verifier checks hashes, byte sizes, missing entries, and unexpected files.

The notebook regenerates both figures automatically. To regenerate only the
figures from the submission root, run:

```bash
source ~/qci-phase3-judge-venv/bin/activate
python Source_Code/figures/make_figures.py
```

Expected outputs:

```text
Source_Code/figures/figure1_architecture.png
Source_Code/figures/figure1_architecture.pdf
Source_Code/figures/figure2_results.png
Source_Code/figures/figure2_results.pdf
```

The plotting script reads regenerated result files rather than hand-entered
values.

The included write-up/evidence verifier is fail-closed and read-only:

```bash
python Source_Code/scripts/integrate_live_results.py
```

It must exit zero and print a `verified already-integrated` message. It checks
the completed 10/10 ledger, 250/250 raw evidence, receipt timing, certified
window result, and 72/100 physical-cap diagnostic against the packaged write-up
source. It does not rebuild or modify the submitted PDF.

## 7. Source-code organization

```text
Source_Code/
├── .gitignore
├── LICENSE
├── RELEASE_MANIFEST.json
├── RUN_ON_QBRAID.ipynb
├── requirements.txt
├── requirements-docs.txt
├── run_all_local.py
├── run_judge_reproduction.py
├── run_live_dirac3.py
├── results_summary.json
├── docs/
├── figures/
├── ieee33_feeder/
├── ieee39_transmission/
├── results/
├── scripts/
│   └── verify_release_manifest.py
└── tests/
```

The programs resolve their data paths from their own locations, while every
judge command in this guide starts at the extracted submission root. Editable
or duplicate submission documents are excluded.

## 8. Limitations and assumptions

- IEEE-33 uses four aggregate service sections and disclosed synthetic DER
  costs, event rates, critical labels, profiles, loss factors, and
  value-of-lost-load assumptions. Customer blocks are not individual
  customers.
- IEEE-39 uses a deterministic 14-zone connected partition and synthetic
  scenario assumptions. “System fraction unserved” is load-weighted because
  the public case has no literal customer counts.
- Static capacity adequacy is not chronological energy readiness. The 96-case
  SOC audit finds 0.877 MWh worst critical shortfall for static Plan C. A
  separately labelled 2 MW/8 MWh sensitivity removes critical shortfall but is
  not part of the three-point frontier.
- IEEE-39 hourly capacities are soft objective walls, and the four-hour
  payload is a total-energy relaxation. The physical-decode audit reports both
  boundaries.
- Raw hardware states are never repaired or projected for evidence credit.
- The 121-variable cubic payload is characterization, not in-spec optimization
  evidence or a scaling demonstration.
- The study does not certify full utility-scale stochastic AC-SCUC, transient
  stability, protection coordination, inverter reactive limits, utility
  deployment, computational speedup, or asymptotic scalability.

## 9. Troubleshooting

- **A command reports a missing `Source_Code/` path:** return to the extracted
  submission root before running the commands in this guide.
- **Manifest fails immediately after extraction:** do not reproduce or accept
  the release; compare the reported missing, unexpected, size, or hash entry.
- **Python version is unsupported:** use Python 3.11 or 3.12.
- **Dependency conflict:** remove only the dedicated
  `~/qci-phase3-judge-venv`, recreate it, and reinstall
  `Source_Code/requirements-docs.txt`. Do not modify qBraid's base environment.
- **Launch button does not resolve without sign-in:** make the repository public
  before submission; the complete ZIP remains the independent fallback.
- **`Source_Code/run_all_local.py` exits nonzero:** do not accept the result;
  inspect the first failed gate.
- **Why locally hash-bound source artifacts are restored after regeneration:**
  payload regeneration is numerically deterministic but not bit-identical
  across CPU/BLAS microarchitectures. The runner checks every finite numeric
  value at 1e-9, except only `classical_reference.energy` and
  `classical_reference.x_device_units` in the identified non-converged IEEE-39
  window payload, which are checked at 1e-4. It then atomically restores the
  shipped files before the hash-bound scoring, evidence, and certificate gates.
  A non-finite value, changed type or structure, missing or added file, or
  excessive drift fails closed and still triggers restoration. Each payload is
  a locally hash-bound source artifact used to construct the registered SDK
  upload object. This local SHA-256 binding does not establish byte-level
  identity with the SDK's transmitted request.
- **Hardware authentication fails:** verify that the value is a QCi portal API
  token, never an account password or qBraid credential.
- **Connection drops after hardware submission:** use `--collect` with the
  existing run label; do not create another label blindly.

Official resources: [Aqora QCi Phase 3 track](https://aqora.io/challenges/global-industry-challenge-2026/tracks/gic-2026-QCI),
[QCi Dirac-3 Developer Guide](https://quantumcomputinginc.com/learn/module/introduction-to-dirac-3/dirac-3-developer-beginner-guide),
and [Launch on qBraid guidance](https://github.com/qBraid/community/discussions/3).
