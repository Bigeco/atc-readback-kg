"""Paper-style evaluation figures (English, no in-figure titles; captions live in docs/evaluation/results_figures.md).

    python src/evaluation/plot_eval.py --run katl_moss38 --e2-run katl_moss11 --old-run katl_all

Writes docs/evaluation/figures/fig{1..5}_*.{pdf,png}.
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))
from common.paths import PROJECT_ROOT, run_dir

# reference palette (dataviz skill): categorical slots 1-2, text tokens
C1, C2, INK, INK2, GRID = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#dddcd8"
plt.rcParams.update({
    "font.family": "serif", "font.serif": ["STIXGeneral", "DejaVu Serif"], "mathtext.fontset": "stix",
    "font.size": 8.5, "axes.labelsize": 8.5, "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "axes.edgecolor": INK2, "axes.linewidth": 0.6, "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK,
    "xtick.major.width": 0.6, "ytick.major.width": 0.6, "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.bbox": "tight", "savefig.pad_inches": 0.02,
})
SINGLE, DOUBLE = 3.4, 7.0   # column widths in inches


def load(path):
    return json.loads(Path(path).read_text())


def save(fig, out, name):
    for ext in ("pdf", "png"):
        fig.savefig(out / f"{name}.{ext}", dpi=300)
    plt.close(fig)


def hbar(ax, labels, values, cis=None, color=C1, fmt="{:.1f}", xmax=100.0, xlabel=""):
    y = np.arange(len(labels))[::-1]
    ax.barh(y, values, height=0.6, color=color, zorder=2)
    ends = list(values)
    if cis:
        lo = [v - c[0] for v, c in zip(values, cis)]
        hi = [c[1] - v for v, c in zip(values, cis)]
        ax.errorbar(values, y, xerr=[lo, hi], fmt="none", ecolor=INK2, elinewidth=0.8, capsize=2, zorder=3)
        ends = [c[1] for c in cis]
    for yi, v, e in zip(y, values, ends):
        ax.text(min(e, xmax) + xmax * 0.035, yi, fmt.format(v), va="center", fontsize=7.5)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlim(0, xmax * 1.2)
    ax.set_xlabel(xlabel)
    ax.xaxis.grid(True, color=GRID, lw=0.5, zorder=0)
    ax.tick_params(axis="y", length=0)


def grouped(ax, labels, a, b, la, lb, ci_a=None, ci_b=None, ylabel=""):
    x = np.arange(len(labels))
    w = 0.38
    ax.bar(x - w / 2 - 0.01, a, w, color=C1, label=la, zorder=2)
    ax.bar(x + w / 2 + 0.01, b, w, color=C2, label=lb, zorder=2)
    for xs, vals, ci in ((x - w / 2 - 0.01, a, ci_a), (x + w / 2 + 0.01, b, ci_b)):
        if ci:
            ax.errorbar(xs, vals, yerr=[[v - c[0] for v, c in zip(vals, ci)], [c[1] - v for v, c in zip(vals, ci)]],
                        fmt="none", ecolor=INK2, elinewidth=0.8, capsize=2, zorder=3)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    for xs, vals in ((x - w / 2 - 0.01, a), (x + w / 2 + 0.01, b)):
        for xi, v in zip(xs, vals):
            if v == 0:                       # a zero bar is invisible: print the value
                ax.text(xi - 0.07, 1.5, "0", ha="right", va="bottom", fontsize=7.5)
    ax.set_ylim(0, 110)
    ax.set_ylabel(ylabel)
    ax.yaxis.grid(True, color=GRID, lw=0.5, zorder=0)
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, handlelength=1.2, columnspacing=1.2)


def pct_ci(r):
    return r["rate"] * 100, (r["ci95"][0] * 100, r["ci95"][1] * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="katl_moss38")
    ap.add_argument("--e2-run", default="katl_moss11")
    ap.add_argument("--old-run", default="katl_all")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "docs" / "evaluation" / "figures")
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("fig*"):
        old.unlink()
    ev = run_dir(args.run) / "eval"

    # Figure 1: E3 injection detection rates
    e3 = load(ev / "e3_injection.json")
    det = dict(e3["detection"])
    applicable = ev / "e3_injection_lr_swap_taxiway_swap.json"
    if applicable.exists():
        det.update(load(applicable)["detection"])
    names = {"heading_change": "Heading change (+20°)", "drop_hold_short": "Hold-short omitted",
             "callsign_digit": "Callsign digit changed", "altitude_change": "Altitude change (+1,000 ft)",
             "drop_callsign": "Callsign omitted", "lr_swap": "Runway side swapped (L/R)",
             "taxiway_swap": "Taxiway substituted"}
    order = sorted(names, key=lambda k: -det[k]["rate"])
    vals, cis = zip(*[pct_ci(det[k]) for k in order])
    fa_v, fa_ci = pct_ci(e3["control_false_alarm"])
    fig, ax = plt.subplots(figsize=(SINGLE, 2.6))
    hbar(ax, [f"{names[k]} (n={det[k]['n']})" for k in order] + [f"Unperturbed control (n={e3['control_false_alarm']['n']})"],
         list(vals) + [fa_v], list(cis) + [fa_ci], xlabel="Detection rate (%)")
    ax.patches[-1].set_color(C2)          # control bar = false-alarm rate
    save(fig, out, "fig3_e3_detection")

    # Figure 2: before vs after
    old = load(run_dir(args.old_run) / "eval" / "e3_injection.json")["detection"]
    ks = ["callsign_digit", "drop_callsign", "taxiway_swap", "lr_swap"]
    labels = ["Callsign\ndigit", "Callsign\nomitted", "Taxiway\nsubstituted", "Runway side\nswapped"]
    fig, ax = plt.subplots(figsize=(SINGLE, 2.3))
    grouped(ax, labels, [old[k]["rate"] * 100 for k in ks], [det[k]["rate"] * 100 for k in ks],
            "Baseline", "Ours",
            [pct_ci(old[k])[1] for k in ks], [pct_ci(det[k])[1] for k in ks], ylabel="Detection rate (%)")
    save(fig, out, "fig4_e3_baseline_vs_ours")

    # Figure 3: E2 precision by relation
    e2 = load(run_dir(args.e2_run) / "eval" / "e2_precision.json")["label"]
    rel = {"VIA": "VIA", "TARGET": "TARGET", "READBACK_OF": "READ-\nBACK_OF", "ADDRESSES": "ADDRE-\nSSES",
           "INSTRUCTION_TYPE": "Instr.\ntype"}
    ks = list(rel)
    blocks = [e2["by_relation"][k] for k in ks] + [e2["overall"]]
    fig, ax = plt.subplots(figsize=(4.2, 2.4))
    grouped(ax, [rel[k] for k in ks] + ["Overall"], [b["strict"]["rate"] * 100 for b in blocks],
            [b["lenient"]["rate"] * 100 for b in blocks], "Strict", "Lenient",
            [pct_ci(b["strict"])[1] for b in blocks], None, ylabel="Precision (%)")
    save(fig, out, "fig2_e2_precision")

    # Figure 4: E0 intrinsic quality
    e0 = load(ev / "e0_intrinsic.json")
    x0, g0 = e0["extraction_raw_llm"], e0["graph"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(DOUBLE, 1.9), gridspec_kw={"width_ratios": [1.15, 1], "wspace": 1.0})
    hbar(a1, ["Schema-valid edges", "Valid intervals", "Consistent readback edges", "Ontology conformance (OC)",
              "Targets in airport KB"],
         [g0["schema_valid_edges"]["rate"] * 100, g0["valid_interval"]["rate"] * 100,
          g0["readback_edge_consistent"]["rate"] * 100, x0["OC"]["rate"] * 100, g0["kb_grounded_target_via"]["rate"] * 100],
         xlabel="Rate (%), higher is better")
    hbar(a2, ["Object hallucination (OH)", "Subject hallucination (SH)", "Relation hallucination (RH)"],
         [x0["OH"]["rate"] * 100, x0["SH"]["rate"] * 100, x0["RH"]["rate"] * 100], color=C2, xmax=30,
         xlabel="Rate (%), lower is better")
    for ax, tag in ((a1, "(a)"), (a2, "(b)")):
        ax.set_title(tag, loc="left", fontsize=8.5, pad=3)
    save(fig, out, "fig1_e0_intrinsic")

    # Figure 5: readback status and alerts
    s = load(run_dir(args.run) / "summary.json")
    rb, al = s["readback_status"], s["alerts"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(DOUBLE, 2.0), gridspec_kw={"width_ratios": [0.8, 1.3], "wspace": 0.75})
    hbar(a1, ["MATCH", "PARTIAL", "MISMATCH"], [rb["MATCH"], rb["PARTIAL"], rb["MISMATCH"]], fmt="{:,.0f}",
         xmax=max(rb.values()), xlabel="Readback links")
    ks = sorted(al, key=lambda k: -al[k])
    hbar(a2, ks, [al[k] for k in ks], fmt="{:,.0f}", xmax=max(al.values()), xlabel="Alerts")
    for ax, tag in ((a1, "(a)"), (a2, "(b)")):
        ax.set_title(tag, loc="left", fontsize=8.5, pad=3)
    save(fig, out, "fig5_readback_alerts")
    print(f"[plot] -> {out}")


if __name__ == "__main__":
    main()
