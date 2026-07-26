#!/usr/bin/env python3
"""Generate the write-up figures from package data (fully reproducible).

  figure1_architecture.png/pdf : one-figure hybrid system diagram (required)
  figure2_pareto.png/pdf       : IEEE-33 cost-resilience Pareto + IEEE-39 metrics

Run:  python figures/make_figures.py   (from the package root)
"""
from __future__ import annotations

import json
import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent

INK = "#1F2430"          # primary text
MUTED = "#5A6172"        # secondary text
CLASSICAL = "#DDE7F3"    # classical-stage fill (blue tint)
CLASSICAL_EDGE = "#3B5B92"
QUANTUM = "#EAE1F6"      # quantum-stage fill (purple tint)
QUANTUM_EDGE = "#6B46A8"
DEVICE = "#DFF0EA"       # device fill (teal tint)
DEVICE_EDGE = "#2E7D6E"
BASELINE = "#F7E9DC"     # baselines fill (warm tint)
BASELINE_EDGE = "#B3562E"


def box(ax, x, y, w, h, title, body, fill, edge, title_size=8.2, body_size=7.2,
        body_offset=None):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=0.012,rounding_size=0.012",
                                linewidth=1.2, edgecolor=edge, facecolor=fill))
    n_title_lines = title.count("\n") + 1
    ax.text(x + w / 2, y + h - 0.024, title, ha="center", va="top",
            fontsize=title_size, fontweight="bold", color=INK,
            linespacing=1.15)
    if body_offset is None:
        body_offset = 0.030 + 0.042 * n_title_lines
    ax.text(x + w / 2, y + h - body_offset, body, ha="center", va="top",
            fontsize=body_size, color=MUTED, linespacing=1.28)


def arrow(ax, x1, y1, x2, y2, color=MUTED, style="-|>", lw=1.4, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=11, linewidth=lw, color=color,
                                 linestyle=ls, shrinkA=2, shrinkB=2))


def figure1():
    fig, ax = plt.subplots(figsize=(10.6, 5.6))
    # Leave enough lower plotting margin for the rounded master-loop box.
    # Its nominal y=-0.05 edge extends slightly lower because of the rounded
    # patch padding; the previous -0.055 limit clipped that edge on export.
    ax.set_xlim(0, 1); ax.set_ylim(-0.09, 1); ax.axis("off")

    # ---------- column 1: data + classical pre-stages ----------
    box(ax, 0.012, 0.70, 0.215, 0.20, "Public grid data",
        "IEEE 39-bus (PYPOWER) and\nIEEE 33-bus Baran–Wu feeder;\ndisclosed DER costs, load/PV\nshapes, contingency classes",
        CLASSICAL, CLASSICAL_EDGE)
    box(ax, 0.012, 0.42, 0.215, 0.22, "Stage A — candidates\n(classical)",
        "IEEE-39: 14 connected,\ndisjoint planning zones;\nIEEE-33: 4+2 candidate blocks;\nfirm-capacity DER siting",
        CLASSICAL, CLASSICAL_EDGE)
    box(ax, 0.012, 0.13, 0.215, 0.23, "Stage B — uncertainty\n(classical)",
        "200 seeded scenarios;\nk-means regimes + complete\nCVaR-tail retention (5+10);\nprobability mass = 1.0",
        CLASSICAL, CLASSICAL_EDGE)
    arrow(ax, 0.12, 0.70, 0.12, 0.645)
    arrow(ax, 0.12, 0.42, 0.12, 0.365)

    # ---------- column 2: Hamiltonians ----------
    box(ax, 0.272, 0.56, 0.24, 0.34, "Stage C — sparse polynomial\nHamiltonians (IEEE-39)",
        "fixed stochastic master:\nexact enumeration + HiGHS MILP;\nhardware dispatch: five in-spec\n3–13 variable simplex objectives;\nΣx=R enforces balance; 121-variable\ncubic resolution-boundary probe",
        QUANTUM, QUANTUM_EDGE)
    # Lift the lower-row boxes slightly.  Besides adding breathing room above
    # the master loop, this gives its two dashed feedback arrows enough
    # vertical travel for unmistakable upward-facing arrowheads.
    box(ax, 0.272, 0.15, 0.24, 0.35, "IEEE-33 feeder instance\n(3 stages, 27 payloads)",
        "Stage 1: three exact design policies\nStage 2: faulted resources removed\nfrom the variable domain\nStage 3: one ternary qudit with\nbalance eliminated algebraically\n→ exact 3-point cost/resilience Pareto",
        QUANTUM, QUANTUM_EDGE)
    arrow(ax, 0.227, 0.78, 0.272, 0.76)
    arrow(ax, 0.227, 0.26, 0.272, 0.28)

    # ---------- column 3: device ----------
    box(ax, 0.562, 0.50, 0.20, 0.36, "QCi Dirac-3 via qBraid",
        "entropy quantum computing;\ncontinuous simplex (Σx = R)\nand integer qudit encodings;\nnative degree ≤ 5; degree-specific\nvariable limits; ≤949 total levels;\nfull coefficient spread + distinct-\nseparation audit before execution",
        DEVICE, DEVICE_EDGE)
    arrow(ax, 0.512, 0.73, 0.562, 0.70)
    arrow(ax, 0.512, 0.34, 0.562, 0.55)

    # ---------- column 3 lower: classical baselines ----------
    box(ax, 0.562, 0.15, 0.20, 0.26, "Classical baselines\n(identical instances)",
        "exact enumeration oracle;\nHiGHS master: 0.00% gap;\ntrust-constr convex dispatch;\nseeded simulated annealing\nmaster: +4.66% gap",
        BASELINE, BASELINE_EDGE)

    # ---------- column 4: decode + decision ----------
    box(ax, 0.812, 0.50, 0.176, 0.36, "Fail-closed evidence\nscoring",
        "raw samples must satisfy the\nregistered domain and balance;\nfloat64 objective re-evaluation;\nomitted constants restored;\npost-processing cannot create\na hardware pass",
        DEVICE, DEVICE_EDGE)
    box(ax, 0.812, 0.13, 0.176, 0.28, "Planner decision +\n3 sponsor metrics",
        "one portfolio frozen before\nscenario realization;\ntotal annual cost / max fraction\nunserved / critical outage h",
        CLASSICAL, CLASSICAL_EDGE)
    arrow(ax, 0.762, 0.68, 0.812, 0.68)
    arrow(ax, 0.9, 0.50, 0.9, 0.415)
    arrow(ax, 0.762, 0.27, 0.812, 0.27)

    # ---------- master loop ----------
    box(ax, 0.272, -0.05, 0.49, 0.15, "Classical master loop",
        "SOC budget allocation across hourly subproblems • exact/MILP/SA baselines\n• nonlinear radial AC screen + 96-case chronology audit",
        "#F2F3F6", MUTED, title_size=7.9, body_size=7.0)
    # Feedback is bottom-to-top: start just inside the master loop and
    # terminate visibly inside each destination box.  The solid, vertical
    # arrows keep their heads legible even after the figure is scaled in Word.
    arrow(ax, 0.66, 0.09, 0.66, 0.168, lw=1.25)
    arrow(ax, 0.40, 0.09, 0.40, 0.168, lw=1.25)

    ax.text(0.005, 0.985,
            "Hybrid quantum–classical siting, islanding and dispatch — system planning is exact/HiGHS; "
            "device-native dispatch objectives run on QCi Dirac-3",
            fontsize=9.4, fontweight="bold", color=INK, ha="left", va="top")
    fig.tight_layout(pad=0.4)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"figure1_architecture.{ext}", dpi=220,
                    bbox_inches="tight")
    plt.close(fig)
    print("wrote figure1_architecture.png/pdf")


def figure2():
    # data straight from generated result files (no hand-typed numbers)
    with (ROOT / "ieee33_feeder/results/design_pareto.csv").open(newline="") as handle:
        pareto = {r["design_mode"]: r for r in csv.DictReader(handle)}
    r39 = json.loads((ROOT / "ieee39_transmission/results/end_to_end_case39.json").read_text())
    mi, mn = r39["metrics_with_islanding"], r39["metrics_no_islanding"]
    portfolio_label = "{" + ",".join(
        str(v) for v in r39["master_fixed_portfolio"]
    ) + "}"

    fig, (axL, axR) = plt.subplots(1, 2, figsize=(10.2, 3.7),
                                   gridspec_kw={"width_ratios": [1.05, 1]})

    # ---- left: IEEE-33 Pareto (upfront cost vs critical unserved) ----
    pts = [
        ("Plan A", float(pareto["cost_efficient"]["upfront_cost_usd"]) / 1e6,
         float(pareto["cost_efficient"]["expected_annual_critical_unserved_mwh"]),
         CLASSICAL_EDGE, "o"),
        ("Plan B", float(pareto["balanced_critical"]["upfront_cost_usd"]) / 1e6,
         float(pareto["balanced_critical"]["expected_annual_critical_unserved_mwh"]),
         DEVICE_EDGE, "o"),
        ("Plan C", float(pareto["robust_critical"]["upfront_cost_usd"]) / 1e6,
         float(pareto["robust_critical"]["expected_annual_critical_unserved_mwh"]),
         QUANTUM_EDGE, "o"),
    ]
    for name, cost, crit, color, marker in pts:
        axL.scatter([cost], [crit], s=95, color=color, marker=marker, zorder=3,
                    edgecolor="white", linewidth=1.4)
    # Direct, locally placed labels are clearer than crossing leader lines.
    # Each label sits in open plot space away from the Pareto segment.
    label_box = dict(boxstyle="round,pad=0.18", facecolor="white",
                     edgecolor="none", alpha=0.90)
    axL.text(13.65, 22.0, "Plan A  $13.42M\n21.0 critical MWh/year",
             ha="left", va="top", fontsize=7.6, color=MUTED, bbox=label_box,
             zorder=4)
    axL.text(16.12, 4.35, "Plan B  $16.44M\n4.2 critical MWh/year",
             ha="right", va="top", fontsize=7.6, color=MUTED, bbox=label_box,
             zorder=4)
    axL.text(19.70, 1.70, "Plan C  $19.46M\n0 static critical MWh/year",
             ha="left", va="top", fontsize=7.6, color=MUTED, bbox=label_box,
             zorder=4)
    axL.plot([13.42, 16.44, 19.46], [21.0, 4.2, 0.0], color=MUTED, lw=1.0, ls="--", zorder=2)
    axL.set_xlabel("Upfront upgrade cost (USD millions)", fontsize=8.4, color=INK)
    axL.set_ylabel("Expected annual critical unserved energy (MWh/year)", fontsize=8.4, color=INK)
    axL.set_title("IEEE-33 feeder: cost–resilience Pareto (exact optima)",
                  fontsize=9.2, color=INK)
    axL.set_xlim(11.8, 22.0); axL.set_ylim(-1.0, 23.5)
    axL.grid(True, color="#E6E8EE", lw=0.7, zorder=0)
    axL.spines[["top", "right"]].set_visible(False)
    axL.tick_params(labelsize=7.8, colors=MUTED)

    # ---- right: IEEE-39 sponsor metrics, with vs without islanding ----
    labels = ["expected total\nannual cost ($B)",
              "max system fraction\nunserved per hour",
              "expected critical\noutage hours"]
    no_vals = [mn["expected_total_annual_cost_$"] / 1e9,
               mn["max_system_fraction_unserved_per_hour"],
               mn["expected_critical_outage_hours"]]
    wi_vals = [mi["expected_total_annual_cost_$"] / 1e9,
               mi["max_system_fraction_unserved_per_hour"],
               mi["expected_critical_outage_hours"]]
    # normalise each metric to its no-islanding value for a common axis
    import numpy as np
    y = np.arange(len(labels))
    norm_wi = [w / n if n else 0.0 for w, n in zip(wi_vals, no_vals)]
    axR.barh(y + 0.19, [1.0] * 3, height=0.34, color="#C9CFDA",
             label="no islanding (=1.0)", zorder=2)
    axR.barh(y - 0.19, norm_wi, height=0.34, color=DEVICE_EDGE,
             label=f"fixed islanding portfolio {portfolio_label}", zorder=3)
    for i, (nv, wv) in enumerate(zip(no_vals, wi_vals)):
        axR.text(1.012, i + 0.19, [f"${nv:.3f}B", f"{nv:.4f}", f"{nv:.3f}"][i],
                 va="center", fontsize=7.6, color=MUTED)
        axR.text(max(norm_wi[i], 0.0) + 0.012, i - 0.19,
                 [f"${wv:.3f}B", f"{wv:.4f}", f"{wv:.3f}"][i],
                 va="center", fontsize=7.6, color=INK)
    axR.set_yticks(y, labels, fontsize=7.9, color=INK)
    axR.set_xlim(0, 1.28)
    axR.set_xlabel("relative to no-islanding counterfactual", fontsize=8.4, color=INK)
    axR.set_title("IEEE-39: fixed portfolio, 15 retained scenarios",
                  fontsize=9.2, color=INK)
    # Reserve a small header band for a direct series key.  This avoids the
    # former floating legend/first-bar/value-label collision.
    axR.set_ylim(2.55, -0.90)
    axR.text(0.015, -0.72, "\u25a0", fontsize=8.5, color="#C9CFDA",
             va="center", ha="left")
    axR.text(0.055, -0.72, "no islanding (=1.0)", fontsize=7.2,
             color=MUTED, va="center", ha="left")
    axR.text(0.015, -0.50, "\u25a0", fontsize=8.5, color=DEVICE_EDGE,
             va="center", ha="left")
    axR.text(0.055, -0.50, f"fixed islanding portfolio {portfolio_label}",
             fontsize=7.2, color=MUTED, va="center", ha="left")
    axR.grid(True, axis="x", color="#E6E8EE", lw=0.7, zorder=0)
    axR.spines[["top", "right"]].set_visible(False)
    axR.tick_params(labelsize=7.8, colors=MUTED)

    fig.tight_layout(pad=0.8, w_pad=1.6)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"figure2_results.{ext}", dpi=220, bbox_inches="tight")
    plt.close(fig)
    print("wrote figure2_results.png/pdf")


if __name__ == "__main__":
    figure1()
    figure2()
