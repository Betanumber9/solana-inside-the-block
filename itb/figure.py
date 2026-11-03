"""reports/figure_distance.png: how the two rules change with the distance between two of our probes in the block.
Reads reports/pairs.csv (run `itb analyze` first). Needs matplotlib (not in the Docker image; optional)."""
import collections
import csv
import json
import pathlib
import random

from .analyze import boot_ci

BINS = [(0, 3, "0–3"), (4, 15, "4–15"), (16, 63, "16–63"), (64, 10 ** 9, "64+")]
FD_9EGR = "9eGrDohdNTAo61DRHyfMuqKWXqYnA3i254Wiszxe8FoY"
# colors: validated categorical slots 1-3 (blue, orange, aqua); identity is also carried by marker and label
GROUPS = [("front_end", "BAM / Harmonic front end (8 validators)", "#2a78d6", "o"),
          ("own_tpu", "own TPU: Jito, Rakurai, Agave, Firedancer JD549 (7)", "#eb6834", "s"),
          ("fd_9egr", "Firedancer 9eGr (own TPU)", "#1baf7a", "^")]


def main(cfg, a):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = pathlib.Path(cfg.data_dir)
    elsewhere = {}
    for d in (data / "runs").glob("*_live"):
        for pk, t in json.loads((d / "meta.json").read_text())["targets"].items():
            elsewhere[pk] = t["tpu_elsewhere"]
    rows = list(csv.DictReader(open("reports/pairs.csv", encoding="utf-8")))
    cells = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for r in rows:
        v = r["validator"]
        g = "fd_9egr" if v == FD_9EGR else ("front_end" if elsewhere.get(v) else "own_tpu")
        d = int(r["second_index"]) - int(r["first_index"])
        b = next(lab for lo, hi, lab in BINS if lo <= d <= hi)
        burst = (r["run"], r["burst"])
        fp, sp = int(r["first_price"]), int(r["second_price"])
        if fp != sp:
            c = cells[(g, b, "fee")][burst]
            c[0] += fp > sp
            c[1] += 1
        c = cells[(g, b, "send")][burst]
        c[0] += int(r["first_send_pos"]) < int(r["second_send_pos"])
        c[1] += 1
    rng = random.Random(3)
    ink, muted, grid, surface = "#0b0b0b", "#898781", "#e1e0d9", "#fcfcfb"
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True, facecolor=surface)
    titles = {"fee": "Higher fee sits earlier", "send": "Sent earlier sits earlier"}
    for ax, what in zip(axes, ("fee", "send")):
        ax.set_facecolor(surface)
        ax.axhline(0.5, color=muted, lw=1, ls=(0, (3, 3)))
        ax.text(-0.35, 0.51, "coin flip", color=muted, fontsize=8, va="bottom")
        for gi, (g, label, color, marker) in enumerate(GROUPS):
            xs, ys, los, his = [], [], [], []
            for i, (_, _, b) in enumerate(BINS):
                share, lo, hi, n = boot_ci(list(cells[(g, b, what)].values()), rng)
                if share is None or n < 10:
                    continue
                xs.append(i + (gi - 1) * 0.08)
                ys.append(share)
                los.append(share - lo)
                his.append(hi - share)
            ax.errorbar(xs, ys, yerr=[los, his], color=color, lw=2, marker=marker, ms=7, capsize=0, elinewidth=1,
                        markeredgecolor=surface, markeredgewidth=1.5, label=label)
        ax.set_xticks(range(len(BINS)), [b[2] for b in BINS])
        ax.set_xlabel("distance between the two probes in the block (positions)", color=muted, fontsize=9)
        ax.set_title(titles[what], color=ink, fontsize=11, loc="left")
        ax.set_ylim(0.2, 1.02)
        ax.grid(axis="y", color=grid, lw=0.8)
        for s in ("top", "right", "left"):
            ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color("#c3c2b7")
        ax.tick_params(colors=muted, labelsize=9, length=0)
    axes[0].set_ylabel("share of pairs (95% interval over bursts)", color=muted, fontsize=9)
    axes[0].legend(loc="lower left", frameon=False, fontsize=8, labelcolor=ink)
    fig.suptitle("Close together in a block: fee decides (front ends, Firedancer 9eGr). Far apart: arrival decides.",
                 color=ink, fontsize=11, x=0.01, ha="left")
    fig.text(0.01, 0.005, "Pairs of our own probes in the same block, main runs of 3 Nov 2026 (epoch 1076). "
             "Source: reports/pairs.csv", color=muted, fontsize=7.5)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    out = pathlib.Path("reports") / "figure_distance.png"
    fig.savefig(out, dpi=150, facecolor=surface)
    print("saved", out)
