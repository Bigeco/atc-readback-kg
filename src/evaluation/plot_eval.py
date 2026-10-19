"""Evaluation figures for the team report (docs/evaluation/figures/*.png).

    python src/evaluation/plot_eval.py --run katl_moss38 --e2-run katl_moss11 --old-run katl_all
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))
from common.paths import PROJECT_ROOT, run_dir

for f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"):
    if Path(f).exists():
        font_manager.fontManager.addfont(f)
# reference palette (dataviz skill): categorical slots 1-2, text and surface tokens
BLUE, ORANGE, SURF, INK, INK2, GRID = "#2a78d6", "#eb6834", "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
plt.rcParams.update({"font.family": "Noto Sans CJK KR", "axes.facecolor": SURF, "figure.facecolor": SURF,
                     "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                     "text.color": INK, "axes.spines.top": False, "axes.spines.right": False, "font.size": 10})
W = 0.36


def load(path):
    return json.loads(Path(path).read_text())


def hbar(ax, labels, values, cis=None, color=BLUE, fmt="{:.1f}%", xmax=100.0):
    y = list(range(len(labels)))[::-1]
    ax.barh(y, values, height=0.55, color=color, zorder=2)
    ends = list(values)
    if cis:
        for yi, (lo, hi) in zip(y, cis):
            ax.plot([lo, hi], [yi, yi], color=INK2, lw=1.2, zorder=3)
            for xx in (lo, hi):
                ax.plot([xx, xx], [yi - .12, yi + .12], color=INK2, lw=1.2, zorder=3)
        ends = [hi for _, hi in cis]
    for yi, v, e in zip(y, values, ends):
        ax.text(min(e, xmax) + xmax * 0.015, yi, fmt.format(v), va="center", color=INK, fontsize=9.5)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlim(0, xmax * 1.14)
    ax.grid(axis="x", color=GRID, zorder=0)
    ax.tick_params(axis="y", length=0)


def grouped(ax, labels, a, b, la, lb, ci_a=None):
    x = np.arange(len(labels))
    ax.bar(x - W / 2 - 0.01, a, W, color=BLUE, label=la, zorder=2)
    ax.bar(x + W / 2 + 0.01, b, W, color=ORANGE, label=lb, zorder=2)
    if ci_a:
        for xi, (lo, hi) in zip(x - W / 2, ci_a):
            ax.plot([xi, xi], [lo, hi], color=INK2, lw=1.1, zorder=3)
    for xi, v in zip(x - W / 2, a):
        ax.text(xi - (0.06 if ci_a else 0), v + 2, f"{v:.0f}", ha="right" if ci_a else "center", fontsize=8.5)
    for xi, v in zip(x + W / 2, b):
        ax.text(xi, v + 2, f"{v:.0f}", ha="center", fontsize=8.5)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylim(0, 112)
    ax.grid(axis="y", color=GRID, zorder=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="katl_moss38")
    ap.add_argument("--e2-run", default="katl_moss11")
    ap.add_argument("--old-run", default="katl_all")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "docs" / "evaluation" / "figures")
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    ev = run_dir(args.run) / "eval"

    # Fig 1: E3 detection rates
    e3 = load(ev / "e3_injection.json")
    det = dict(e3["detection"])
    applicable = ev / "e3_injection_lr_swap_taxiway_swap.json"
    if applicable.exists():
        det.update(load(applicable)["detection"])
    names = {"heading_change": "방위 변경 (+20°)", "drop_hold_short": "hold short 빠뜨림", "callsign_digit": "콜사인 숫자 바꿈",
             "altitude_change": "고도 변경 (+1,000ft)", "drop_callsign": "콜사인 빠뜨림", "lr_swap": "활주로 좌우 바꿈 (L↔R)",
             "taxiway_swap": "유도로 바꿔치기"}
    order = sorted(names, key=lambda k: -det[k]["rate"])
    fig, ax = plt.subplots(figsize=(7.6, 3.9))
    hbar(ax, [f"{names[k]}  (n={det[k]['n']})" for k in order], [det[k]["rate"] * 100 for k in order],
         [(det[k]["ci95"][0] * 100, det[k]["ci95"][1] * 100) for k in order])
    ax.set_xlabel("탐지율 (%)  ·  막대 끝 선 = 95% 신뢰구간")
    fa = e3["control_false_alarm"]
    ax.set_title(f"E3 오류 주입 탐지율 (MOSS 38개 파일 KG, LLM 추출)\n정상 복창 대조군 오경보율 {fa['rate'] * 100:.1f}% "
                 f"(n={fa['n']})", loc="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "fig1_e3_detection.png", dpi=170)
    plt.close(fig)

    # Fig 2: before vs after
    old = load(run_dir(args.old_run) / "eval" / "e3_injection.json")["detection"]
    ks = ["callsign_digit", "drop_callsign", "taxiway_swap", "lr_swap"]
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    grouped(ax, ["콜사인 숫자 바꿈", "콜사인 빠뜨림", "유도로 바꿔치기", "활주로 좌우 바꿈"],
            [old[k]["rate"] * 100 for k in ks], [det[k]["rate"] * 100 for k in ks],
            "이전: Whisper 전체 + 이전 비교 규칙", "현재: MOSS 38개 + 수정한 비교 규칙")
    ax.set_ylabel("탐지율 (%)")
    ax.legend(frameon=False, loc="upper right", fontsize=9)
    ax.set_title("E3 탐지율 변화 (이전 실행 대비)", loc="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "fig2_e3_before_after.png", dpi=170)
    plt.close(fig)

    # Fig 3: E2 precision by relation
    e2 = load(run_dir(args.e2_run) / "eval" / "e2_precision.json")["label"]
    rel = {"VIA": "VIA\n(경로)", "TARGET": "TARGET\n(지시 대상)", "READBACK_OF": "READBACK_OF\n(복창 연결)",
           "ADDRESSES": "ADDRESSES\n(항공편)", "INSTRUCTION_TYPE": "지시 종류"}
    ks = list(rel)
    st = [e2["by_relation"][k]["strict"]["rate"] * 100 for k in ks] + [e2["overall"]["strict"]["rate"] * 100]
    le = [e2["by_relation"][k]["lenient"]["rate"] * 100 for k in ks] + [e2["overall"]["lenient"]["rate"] * 100]
    ci = [e2["by_relation"][k]["strict"]["ci95"] for k in ks] + [e2["overall"]["strict"]["ci95"]]
    fig, ax = plt.subplots(figsize=(7.6, 3.9))
    grouped(ax, [rel[k] for k in ks] + ["전체"], st, le, "엄격 (원문에 명시)", "관대 (오인식 보정 포함)",
            [(lo * 100, hi * 100) for lo, hi in ci])
    ax.set_ylabel("정밀도 (%)")
    ax.set_ylim(0, 125)
    ax.legend(frameon=False, loc="upper left", ncol=2, fontsize=9)
    ax.set_title("E2 근거 대비 정밀도 (MOSS 11개 파일, 관계별 표본 10개, 잠정 라벨)\n세로선 = 엄격 기준 95% 신뢰구간",
                 loc="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "fig3_e2_precision.png", dpi=170)
    plt.close(fig)

    # Fig 4: E0 intrinsic quality
    e0 = load(ev / "e0_intrinsic.json")
    x0, g0 = e0["extraction_raw_llm"], e0["graph"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.4, 3.2), gridspec_kw={"width_ratios": [1.25, 1]})
    hbar(a1, ["스키마 준수 간선", "유효 구간 정상", "복창 간선 조건 충족", "온톨로지 준수 (OC)", "KB에 있는 대상"],
         [g0["schema_valid_edges"]["rate"] * 100, g0["valid_interval"]["rate"] * 100,
          g0["readback_edge_consistent"]["rate"] * 100, x0["OC"]["rate"] * 100, g0["kb_grounded_target_via"]["rate"] * 100])
    a1.set_title("높을수록 좋음", loc="left", fontsize=10, color=INK2)
    a1.set_xlabel("%")
    hbar(a2, ["대상 hallucination (OH)", "콜사인 hallucination (SH)", "관계 hallucination (RH)"],
         [x0["OH"]["rate"] * 100, x0["SH"]["rate"] * 100, x0["RH"]["rate"] * 100], color=ORANGE, xmax=30)
    a2.set_title("낮을수록 좋음 (검증 전 LLM 원 출력)", loc="left", fontsize=10, color=INK2)
    a2.set_xlabel("%")
    fig.suptitle("E0 KG 내재 품질 (MOSS 38개 파일, 정답 없이 계산)", x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "fig4_e0_intrinsic.png", dpi=170)
    plt.close(fig)

    # Fig 5: readback status and alerts
    s = load(run_dir(args.run) / "summary.json")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.6, 3.4), gridspec_kw={"width_ratios": [1, 1.45]})
    rb = s["readback_status"]
    hbar(a1, ["MATCH", "PARTIAL", "MISMATCH"], [rb["MATCH"], rb["PARTIAL"], rb["MISMATCH"]], fmt="{:,.0f}",
         xmax=max(rb.values()))
    a1.set_title(f"복창 연결 {sum(rb.values())}개의 판정", loc="left", fontsize=10, color=INK2)
    a1.set_xlabel("개수")
    al = s["alerts"]
    nm = {"NO_READBACK": "복창 없음", "SIMILAR_CALLSIGN": "비슷한 콜사인", "POST_HANDOFF": "이양 후 지시",
          "PARTIAL": "일부 빠뜨림", "KB_VIOLATION": "KB에 없는 대상", "MISMATCH": "다르게 복창",
          "NO_CALLSIGN": "콜사인 없는 복창", "RWY_OVERLAP": "활주로 동시 허가"}
    ks = sorted(al, key=lambda k: -al[k])
    hbar(a2, [f"{nm.get(k, k)} ({k})" for k in ks], [al[k] for k in ks], fmt="{:,.0f}", xmax=max(al.values()))
    a2.set_title("M5 경고 종류별 개수", loc="left", fontsize=10, color=INK2)
    a2.set_xlabel("개수")
    fig.suptitle("Temporal KG 복창 판정과 경고 (MOSS 38개 파일)", x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "fig5_readback_alerts.png", dpi=170)
    plt.close(fig)
    print(f"[plot] -> {out}")


if __name__ == "__main__":
    main()
