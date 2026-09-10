#!/usr/bin/env python3
"""Compose the primary FSR GPU-cost chart from validated measured data."""

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter, MultipleLocator
from summarize import summarize

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT.parents[1] / "assets"
ORDER = ["fsr411", "fsr411b", "v3", "v4r7"]
COLORS = {"fsr411": "#C0C7D2", "fsr411b": "#819ED0", "v3": "#A395DC", "v4r7": "#5DE2B5"}
LABELS = {
    "fsr411": ("FSR 4.1.1", "original shaders"),
    "fsr411b": ("FSR 4.1.1b", ""),
    "v3": ("BC250 v3", ""),
    "v4r7": ("BC250 v4r7", ""),
}
BG, PANEL, INK, MUTED = "#10151D", "#10151D", "#F1F4F8", "#98A5B8"


def render():
    result = summarize()
    config = json.loads((ROOT / "configuration.json").read_text())
    lookup = {(cell["resolution"], cell["variant"]): cell for cell in result["cells"]}
    regular = font_manager.findfont(font_manager.FontProperties(family="Fira Sans"))
    semibold = font_manager.findfont(
        font_manager.FontProperties(family="Fira Sans", weight="semibold")
    )
    font = font_manager.FontProperties(fname=regular)
    bold = font_manager.FontProperties(fname=semibold)
    plt.rcParams.update(
        {
            "svg.fonttype": "path",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.unicode_minus": False,
            "svg.hashsalt": "bc250-fsr4-four-way-cost-20260910",
        }
    )
    fig = plt.figure(figsize=(15, 8.5), facecolor=BG)
    fig.text(
        0.071, 0.924, "FSR 4 — GPU cost", color=INK, fontsize=29, fontproperties=bold, va="top"
    )
    fig.text(
        0.073,
        0.864,
        "AMD BC250  /  40 CUs  /  1850 MHz target  /  INT8 Quality",
        color=MUTED,
        fontsize=12.2,
        fontproperties=font,
        va="top",
    )
    fig.text(
        0.965,
        0.911,
        "LOWER IS BETTER",
        ha="right",
        va="top",
        color=COLORS["v4r7"],
        fontsize=10.5,
        fontproperties=bold,
    )

    legend_x = [0.073, 0.315, 0.545, 0.760]
    for x, variant in zip(legend_x, ORDER):
        fig.add_artist(
            Rectangle(
                (x, 0.784),
                0.0105,
                0.023,
                transform=fig.transFigure,
                facecolor=COLORS[variant],
                edgecolor="none",
            )
        )
        main, detail = LABELS[variant]
        fig.text(x + 0.019, 0.807, main, va="top", color=INK, fontsize=12.2, fontproperties=bold)
        if detail:
            fig.text(
                x + 0.019, 0.779, detail, va="top", color=MUTED, fontsize=10.1, fontproperties=font
            )

    ax = fig.add_axes([0.073, 0.235, 0.895, 0.477], facecolor=PANEL)
    maximum = max(cell["run_max_ms"] for cell in result["cells"])
    step = 5 if maximum <= 35 else 10
    top = math.ceil((maximum * 1.12) / step) * step
    ax.set_ylim(0, top)
    ax.set_xlim(-0.62, 4.62)
    ax.yaxis.set_major_locator(MultipleLocator(step))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    ax.tick_params(axis="y", length=0, pad=12, labelsize=10.5, colors=MUTED)
    for tick in ax.get_yticklabels():
        tick.set_fontproperties(font)
        tick.set_fontsize(10.5)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#617086", alpha=0.21, linewidth=0.65)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.axhline(0, color="#6F7F93", alpha=0.48, linewidth=0.9)
    ax.set_xticks([])
    ax.text(
        -0.62,
        top * 1.035,
        "GPU TIME PER UPSCALE  ·  ms",
        color=MUTED,
        fontsize=10.1,
        fontproperties=bold,
        va="bottom",
    )
    width = 0.244
    gap = 0.044
    centers = [0, 2, 4]
    value_labels = []
    bar_records = []
    for group, (resolution, dimensions) in enumerate(config["sizes"].items()):
        center = centers[group]
        for index, variant in enumerate(ORDER):
            cell = lookup[(resolution, variant)]
            x = center + (index - 1.5) * (width + gap)
            value, lower, upper = cell["gpu_ms"], cell["run_min_ms"], cell["run_max_ms"]
            ax.bar(x, value, width=width, color=COLORS[variant], edgecolor="none", zorder=3)
            ax.errorbar(
                x,
                value,
                yerr=[[value - lower], [upper - value]],
                fmt="none",
                ecolor=INK,
                elinewidth=0.9,
                capsize=2.7,
                capthick=0.9,
                zorder=5,
            )
            label = ax.text(
                x,
                upper + top * 0.028,
                f"{value:.2f}",
                ha="center",
                va="bottom",
                color=COLORS[variant] if variant == "v4r7" else INK,
                fontsize=14.5,
                fontproperties=bold,
            )
            value_labels.append(label)
            bar_records.append(
                {
                    "resolution": resolution,
                    "variant": variant,
                    "x": x,
                    "height_ms": value,
                    "width": width,
                    "label": f"{value:.2f}",
                }
            )
        label = "4K" if resolution == "4k" else resolution
        ax.text(
            center,
            -top * 0.085,
            label,
            ha="center",
            va="top",
            color=INK,
            fontsize=16,
            fontproperties=bold,
            clip_on=False,
        )
        w, h = dimensions["output"]
        ax.text(
            center,
            -top * 0.160,
            f"{w:,} × {h:,}",
            ha="center",
            va="top",
            color=MUTED,
            fontsize=10.3,
            fontproperties=font,
            clip_on=False,
        )

    fig.add_artist(
        Rectangle(
            (0.073, 0.111),
            0.894,
            0.001,
            transform=fig.transFigure,
            facecolor="#384456",
            edgecolor="none",
        )
    )
    fig.text(
        0.073,
        0.082,
        "Complete FSR dispatch · synthetic D3D12 workload · "
        f"{config['runs_per_cell']} runs/bar · "
        f"{config['frames_per_run'] - config['discard_first_frames']} scored frames/run · whiskers: run range",
        color=MUTED,
        fontsize=10.2,
        fontproperties=font,
        va="center",
    )
    fig.text(
        0.073,
        0.049,
        "v3 retains its original Mesa driver; the other three use the same standard Mesa build.",
        color=MUTED,
        fontsize=10.0,
        fontproperties=font,
        va="center",
    )

    # The vector geometry and every printed number come from the measured data.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    extents = [label.get_window_extent(renderer) for label in value_labels]
    if any(a.overlaps(b) for i, a in enumerate(extents) for b in extents[i + 1 :]):
        raise RuntimeError("Value labels overlap; adjust the formulaic layout")
    for label in list(fig.texts) + list(ax.texts):
        bounds = label.get_window_extent(renderer)
        if not fig.bbox.contains(bounds.x0, bounds.y0) or not fig.bbox.contains(
            bounds.x1, bounds.y1
        ):
            raise RuntimeError("Chart label falls outside the export canvas")
    ASSETS.mkdir(parents=True, exist_ok=True)
    stem = ASSETS / "fsr4-four-way-gpu-cost"
    fig.savefig(stem.with_suffix(".svg"), facecolor=BG, metadata={"Date": None})
    svg = stem.with_suffix(".svg")
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()) + "\n")
    fig.savefig(
        stem.with_suffix(".png"),
        facecolor=BG,
        dpi=240,
        metadata={
            "Description": "Directly measured full-upscaler GPU cost on BC250; see accompanying data and method."
        },
    )
    fig.savefig(
        stem.with_suffix(".pdf"),
        facecolor=BG,
        metadata={"CreationDate": None, "ModDate": None, "Title": "FSR 4 GPU cost on AMD BC250"},
    )
    (ROOT / "chart-geometry.json").write_text(
        json.dumps(
            dict(
                canvas_inches=[15, 8.5],
                png_pixels=[3600, 2040],
                y_axis_min_ms=0,
                y_axis_max_ms=top,
                bars=bar_records,
                numeric_label_overlap=False,
            ),
            indent=2,
        )
        + "\n"
    )
    print(stem.with_suffix(".png"))


if __name__ == "__main__":
    render()
