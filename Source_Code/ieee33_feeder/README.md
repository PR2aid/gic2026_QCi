# IEEE-33 three-stage scientific component

This directory is the feeder component of the integrated QCi Phase 3 package. **Do not submit hardware from this subdirectory.** The root `../run_live_dirac3.py` preserves the immutable original campaign, while `../run_judge_reproduction.py` provides guarded independent reruns in a separate namespace. Both enforce allocation checks and fail-closed scoring.

Credential-free component reproduction:

```bash
python scripts/run_pipeline.py
python scripts/run_stage0.py
python scripts/run_baselines.py
python scripts/run_ac_audit.py
python scripts/run_chronology_audit.py
python -m unittest discover -s tests -v
```

The component generates 27 Dirac-3 payloads across:

- Stage 1: three exact investment policies;
- Stage 2: four contingencies for each policy, with faulted candidates structurally removed;
- Stage 3B: exact-balance native cubic ternary-qudit objectives;
- Stage 3S: matched continuous cubic simplex objectives.

All 27 payloads pass both coefficient-spread and distinct-separation audits. The exact static frontier is:

| Plan | Upfront cost | Expected annual critical ENS | Static critical pocket-hours |
|---|---:|---:|---:|
| A — cost-efficient | USD 13.42M | 21.0 MWh/year | 16 |
| B — balanced-critical | USD 16.44M | 4.2 MWh/year | 8 |
| C — robust-critical | USD 19.46M | 0.0 MWh/year | 0 |

The 96-case chronology audit is deliberately separate: static Plan C retains 0.877 MWh worst-case critical shortfall at conservative SOC. See the root README for claims, limitations, and the live protocol.
