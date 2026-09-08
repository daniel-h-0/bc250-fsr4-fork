#!/usr/bin/env python3
"""Render the standalone README SVG/PNG; requires matplotlib (tested with 3.11.1)."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from reconstruct import reconstruct

HERE = Path(__file__).resolve().parent
DATA = reconstruct()
BG, INK, MUTED, GRID = "#fbfcfe", "#17283b", "#516176", "#dbe2ea"
V3, V4 = "#63758a", "#007b76"
plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "none", "svg.hashsalt": "fsr4-cost-v1"})
fig = plt.figure(figsize=(11, 8.3), facecolor=BG)
fig.text(.06, .937, "FSR4 takes less of your GPU budget", fontsize=25, weight="bold", color=INK)
fig.text(.06, .892, "Estimated pass cost · v3 → v4 · lower is better", fontsize=16, color=MUTED)
fig.text(.06, .850, "BC250 · 40 CUs · 1850 MHz GPU maximum · FSR 4.1.1 INT8", fontsize=12, color=MUTED)
ax = fig.add_axes([.15, .27, .66, .51], facecolor=BG)
ax.set_xlim(0, 20)
ax.set_ylim(-.7, 5.0)
ax.set_xticks([0, 5, 10, 15, 20], labels=["0", "5", "10", "15", "20 ms"])
ax.tick_params(axis="x", colors=MUTED, labelsize=11, length=0, pad=10)
ax.tick_params(axis="y", length=0, pad=17)
ax.set_yticks([4, 2, 0], labels=["1080p", "1440p", "4K"])
for label in ax.get_yticklabels():
    label.set_fontsize(15)
    label.set_fontweight("bold")
    label.set_color(INK)
for spine in ax.spines.values():
    spine.set_visible(False)
ax.set_axisbelow(True)
ax.grid(axis="x", color=GRID, linewidth=.8)
ax.axvline(0, color=GRID, linewidth=1.1)
for index, row in enumerate(DATA["estimates"]):
    y = 4 - index * 2
    for offset, key, color, name in [(.24, "v3_cost_ms", V3, "v3"), (-.24, "v4_estimated_cost_ms", V4, "v4")]:
        value = row[key]
        measured = name == "v3" and index == 1
        ax.barh(y + offset, value, height=.36, color=color if measured else BG,
                edgecolor=color, linewidth=1.4, hatch=None if measured else "////")
        ax.text(value + .22, y + offset, f"≈{value:.1f}", va="center", color=color, fontsize=15, weight="bold")
    ax.text(1.075, y, f"≈{row['measured_whole_frame_gpu_saving_ms']:.1f} ms", transform=ax.get_yaxis_transform(),
            va="center", fontsize=18, color=V4, weight="bold")
    ax.text(1.075, y - .37, "reclaimed", transform=ax.get_yaxis_transform(), va="center", fontsize=11, color=MUTED)
ax.legend(handles=[Patch(facecolor=V3, label="v3"), Patch(facecolor=V4, label="v4")],
          loc="lower left", bbox_to_anchor=(0, 1.01), ncol=2, frameon=False, fontsize=12,
          borderaxespad=0, handlelength=1.2, labelcolor=INK)
fig.text(.06, .186, "Solid: measured 1440p v3 anchor. Hatched: estimated costs.", fontsize=12, color=INK)
fig.text(.06, .144, "Reconstruction: output-pixel scaling + matched game GPU savings.", fontsize=12, color=MUTED)
fig.text(.06, .106, "Historical anchor: 8.02 ms FFX dispatch at 1440p Balanced; later savings use Quality.", fontsize=11, color=MUTED)
fig.text(.06, .058, "September 2026 recordings · assumptions & source data: docs/fsr-cost.md", fontsize=11, color=MUTED)
assets = HERE.parents[1] / "assets"
for suffix in ("svg", "png"):
    destination = assets / f"fsr4-v3-v4-cost.{suffix}"
    fig.savefig(destination, dpi=180, facecolor=BG,
                metadata={"Date": None} if suffix == "svg" else {})
    if suffix == "svg":
        destination.write_text("\n".join(line.rstrip() for line in destination.read_text().splitlines()) + "\n")
plt.close(fig)
