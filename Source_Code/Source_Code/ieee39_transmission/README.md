# IEEE-39 transmission scientific component

This directory is the transmission/scenario component of the integrated QCi
Phase 3 package. **Do not submit hardware from this subdirectory.**
`../run_live_dirac3.py` preserves and scores the immutable original campaign and
must not be resubmitted. Optional independent reviewer reruns must use
`../run_judge_reproduction.py`, which writes to a separate namespace and cannot
overwrite the submitted evidence.

Credential-free reproduction:

```bash
python scripts/test_conventions.py
python scripts/run_end_to_end.py
python scripts/summarize_resources.py
python scripts/build_dirac3_payload.py --relaxation-schedule 1 --num-samples 25
```

The public PYPOWER/MATPOWER IEEE-39 case is augmented with disclosed DER and
critical-load assumptions. The planning layer assigns every network bus to one
of 14 connected, pairwise-disjoint zones. Consequently, all 21 positive-load
buses have exactly one owner, candidate loads sum to the system load, and the
fixed investment portfolio cannot double count customers, load, or upgrade
cost. Two hundred seeded scenarios are compressed to five regime
representatives plus all ten CVaR-tail cases; retained probability is exactly
1.0.

The system portfolio is chosen once by a classical two-stage stochastic
master, then frozen across all 15 retained scenarios. Exact enumeration and
HiGHS solve the identical 14-variable binary polynomial. This planning result
is separate from the immutable Dirac-3 study: the device runs solve or benchmark
dispatch objectives on one disclosed candidate footprint and do not validate
the system-level portfolio.

| Objective family | Variables | Degree | Full coefficient-resolution status | Integrated live role |
|---|---:|---:|---|---|
| Dispatch-benchmark selection artifact | 14 | 2 | fails pairwise distinct-separation guidance | local exact + HiGHS only |
| Hourly dispatch ×4 | 3-4 | 2 | pass | registered evidence |
| Four-hour window | 13 | 2 | pass | registered evidence |
| Full-day cubic probe | 121 | 3 | fails coefficient guidance (spread 5.22e8) | characterization only |

The selection artifact remains a valid local benchmark but is not presented as
an in-spec analog experiment or as the stochastic planning master. The five
dispatch objectives use Dirac-3's continuous sum constraint as balance. The
full-day probe fits the documented degree-3 variable count (121 <= 135) but
intentionally tests a coefficient-resolution boundary.

Credential-free fixed-portfolio results over 15 retained scenarios:

| Metric | No islanding | Fixed portfolio {0,3} |
|---|---:|---:|
| Expected 24 h energy + VoLL cost | USD 6.811M | USD 5.537M |
| Annualized upgrade cost | USD 0 | USD 221.234M |
| Expected total annual cost | USD 2.486B | USD 2.242B |
| Expected total annual saving | - | USD 243.702M (9.80%) |
| Maximum system fraction unserved/hour | 0.1932 | 0.0672 |
| Expected critical outage hours | 0.080 | 0.000 |
| HiGHS vs exact master objective | - | 0.00% gap |
| Seeded simulated annealing master gap | - | +4.66% |

The selected zones contain 4/21 load buses and 27.31% of system MW. These are
measured portfolio-coverage values, not a full-system microgrid-coverage claim.

See the root README for hardware execution, evidence interpretation, physical limitations, and submission gates.
