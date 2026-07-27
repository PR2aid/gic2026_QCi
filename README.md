# Quantum Pattern Recognition — QCi Phase 3

**Project:** Device-Native Dirac-3 Optimization for Cost and Resilience in
Hierarchical Microgrids

**Track:** Global Industry Challenge 2026 — Quantum Computing Inc., *Cost
Optimization in Resilient Power Grids*

**Team:** Quantum Pattern Recognition

##  reproduction 

https://github.com/PR2aid/gic2026_QCi

The best way is to download this package from Github, then upload to qBrain and run the bellow commants:

### To run locally

```bash

unzip gic2026_QCi-main.zip

cd ~/gic2026_QCi-main/Source_Code

python3 -m venv .venv-local
source .venv-local/bin/activate
python -m pip install -r requirements.txt

python run_all_local.py

```

then the result can be seen both on the screen and is saved in Source_Code/results_summary.json
It also regenerates and saves detailed JSON/CSV results inside:

ieee39_transmission/results/
ieee33_feeder/results/
results/live/

### To run on Quantum hardware / i.e. Dirac-3 from qBraid:

```bash

cd ~/gic2026_QCi-main/Source_Code
source .venv-local/bin/activate
python -m pip install -r requirements.txt

unset QCI_TOKEN QCI_API_URL
read -rsp "Paste the QCi portal API token: " QCI_TOKEN
echo
export QCI_TOKEN
export QCI_API_URL="https://api.qci-prod.com"

```


## Principal results

| Question | Result to inspect | File |
|---|---:|---|
| Did every numerical and structural claim reproduce? | 39/39 PASS | `Source_Code/results_summary.json` |
| Did every IEEE-39 convention and invariant pass? | 15/15 | `Source_Code/ieee39_transmission/results/convention_test_summary.json` |
| Did the submitted hardware evidence pass strict audit? | 11/11 responses | `Source_Code/results/live/strict_evidence_audit.json` |
| Were all counted campaign samples feasible in the registered machine domain? | 250/250 | `Source_Code/results/live/hardware_summary.json` |
| What is the separate physical equipment-cap diagnostic? | 72/100 hourly raw samples; 1/4 hourly best states | `Source_Code/results/live/physical_decode_audit.json` |
| Did the four-hour convex reference certification pass? | PASS; 25 samples | `Source_Code/results/live/certified_hardware_analysis.json` |
| Were both manuscript figures regenerated? | 2/2 in PNG and PDF | `Source_Code/figures/` |
| Did the entire judge path pass in this run? | PASS | `Source_Code/results/reproduction_acceptance.json` |

The counters have distinct meanings:

- `39/39` is the consolidated numerical and structural claim audit.
- `11/11 responses` is the ten-response campaign plus the separate smoke
  response.
- `250/250` is registered integer/simplex machine-domain feasibility across
  the counted campaign samples. It does not mean 250 optimal or fully physical
  dispatches.
- `72/100` is the separately disclosed hourly equipment-cap diagnostic. Raw
  states are not repaired or projected.

## What the single entry point does

The program performs the following sequence automatically:

1. verifies every released file by path, byte size, and SHA-256;
2. regenerates the IEEE-39 study, classical baselines, invariants, dispatch
   results, and QCi payloads;
3. regenerates the IEEE-33 three-stage study, Pareto designs, exact baselines,
   AC screen, 96-case chronology audit, and QCi payloads;
4. verifies cross-host payload equivalence and restores the exact submitted
   local payload artifacts before hash-bound evidence scoring;
5. re-scores the archived QCi responses and checks receipts, returned
   configurations, sample counts, protocol bindings, and objectives;
6. runs the physical-decode and convex-reference audits;
7. executes all scientific and release regression tests;
8. regenerates both figures and checks the manuscript evidence; and
9. writes the acceptance certificate atomically only after all assertions pass.

Dependency installation is the only step requiring network access. Scientific
reproduction and archived hardware re-scoring are credential-free.

## Environment

- Python 3.11 or 3.12
- qBraid Subscription Small (2 vCPU, 4 GB) is sufficient
- no QCi token, repository credential, paid hardware call, or secret is needed

The qBraid notebook is the recommended route. For a terminal-only review, start
from a fresh clone or fresh archive extraction and run:

```bash
python -m venv ~/qci-phase3-judge-venv
source ~/qci-phase3-judge-venv/bin/activate
python -m pip install --disable-pip-version-check -r Source_Code/requirements-docs.txt
python Source_Code/run_judge_acceptance.py
```

Do not run the manifest verifier again in the same working directory after
reproduction: the run intentionally creates a fresh acceptance certificate
and regenerates results, timing metadata, and figures. Use a fresh clone or
fresh extraction to verify release integrity again.

## Submitted contents

The submission archive `QuantumPatternRecognition_QCI_Phase3.zip` has exactly
three root entries:

| Entry | Purpose |
|---|---|
| `QuantumPatternRecognition__Phase3_Version14.pdf` | Official cover, five technical pages, and references |
| `Source_Code/` | Source, pinned dependencies, public inputs, tests, frozen hardware evidence, and licence |
| `README.md` | This judge reproduction guide |

The credential-free path re-scores the submitted QCi evidence. A new stochastic
Dirac-3 run is optional, allocation-dependent, and not part of acceptance.
Guarded optional rerun tooling is retained in
`Source_Code/run_judge_reproduction.py`, but judges do not need to use it.

## Evidence boundaries and limitations

- The ten scored campaign responses contain nine registered evidence jobs and
  one separately labelled out-of-resolution characterization probe.
- The smoke response is excluded from campaign scoring.
- The 121-variable cubic payload is characterization, not in-spec optimization
  evidence or a scalability demonstration.
- IEEE-39 hourly capacities are soft objective walls, and the four-hour payload
  is a total-energy relaxation; the physical-decode audit reports these
  boundaries separately.
- The study does not claim computational speedup, full utility-scale
  stochastic AC-SCUC, transient-stability certification, protection
  coordination, or asymptotic scalability.
- Dirac-3 uses discrete variables; gate-model qubit count, circuit depth, and
  shots are not applicable.

## Intellectual-property and licensing boundary

The supplied implementation and evidence are limited to the files in this
release and are sufficient for the credential-free reproduction above.
Rights in submitted material are governed by the applicable Global Industry
Challenge rules, participant agreement, and Aqora terms. This notice does not
limit, replace, or modify those rights. Apart from those rights and applicable
third-party licences, no additional public software or patent licence is
granted; see `Source_Code/LICENSE`.

Certain subject matter in and related to this submission is disclosed in
Australian Provisional Patent Application No. 2026906569 and other pending
Australian provisional patent applications; no granted patent is claimed. For
enquiries about a separate licence concerning rights not already granted under
the competition instruments, contact <admin@pr2aid.com> or
<azadeh.alavi@rmit.edu.au>.

## Troubleshooting

- If qBraid opens an old clone, launch again or clone the repository into a new
  directory; do not run a previously edited notebook.
- If the first manifest gate fails, stop and use a fresh clone or extraction.
- If the Python version is unsupported, use Python 3.11 or 3.12.
- If dependency installation was interrupted, remove only
  `~/qci-phase3-judge-venv`, restart the notebook kernel, and run all cells.
- If any required command fails, no PASS acceptance certificate is retained.
