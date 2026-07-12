#!/usr/bin/env python3
"""Compose the 'raw movies → molecular sociology' pipeline strip (demo figure).

Five stage panels tiled left-to-right with arrows: raw frame series → tomogram →
particle picks → OPUS-ET 3D states → molecular sociology (two species in the cell).
Panels 1/2/5 are rendered from MRCs on the cluster (see demo/render_commands.md §C);
panels 3/4 reuse the existing Gate-2 / Gate-3 demo figures. Pure matplotlib.
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt

# nicer typography than the matplotlib default (DejaVu Sans)
plt.rcParams["font.family"] = ["Helvetica Neue", "Helvetica", "Arial"]
WORDMARK = "Avenir Next"


def center_square(img):
    h, w = img.shape[:2]
    s = min(h, w)
    y0, x0 = (h - s) // 2, (w - s) // 2
    return img[y0:y0 + s, x0:x0 + s]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panels-dir", default="demo/qc/pipeline_panels",
                    help="dir with panel_raw.png / panel_tomo.png / panel_sociology.png")
    ap.add_argument("--out", default="demo/qc/pipeline_strip.png")
    args = ap.parse_args()
    pd = Path(args.panels_dir)

    # (path, title, subtitle, full) — `full` panels keep their native aspect (no square crop)
    stages = [
        (pd / "panel_raw.png",       "① Raw frame series",    "motion-corrected tilt movies", False),
        (pd / "panel_tomo.png",      "② Tomogram",            "WARP · AreTomo2 reconstruction", False),
        (Path("demo/qc/gate2_ribosome_picks/TS028_good_all-picks.png"),
                                     "③ Particle picks",      "PyTOM template matching", False),
        (Path("demo/qc/gate3_states/ribo_state_gallery_3d.png"),
                                     "④ OPUS-ET heterogeneity analysis", "template-matching set purification", True),
        (pd / "panel_maps.png",      "⑤ M-refined maps",      "joint multi-particle refinement", True),
        (Path("demo/qc/finale/finale_insitu_still.png"),
                                     "⑥ Molecular sociology", "two species, back in the cell (ArtiaX)", False),
    ]
    n = len(stages)

    # prepare images + per-panel aspect (w/h); width_ratios track aspect so nothing distorts
    prepared = []
    for path, title, sub, full in stages:
        im = mpimg.imread(str(path))
        if not full:
            im = center_square(im)
        prepared.append((im, title, sub, im.shape[1] / im.shape[0]))

    arrow = 0.14
    wr = []
    for i, (_, _, _, asp) in enumerate(prepared):
        wr.append(asp)
        if i < n - 1:
            wr.append(arrow)
    panel_h = 3.3
    fig = plt.figure(figsize=(panel_h * sum(wr) + 0.4, panel_h + 0.7))
    gs = fig.add_gridspec(1, len(wr), width_ratios=wr, wspace=0.04)

    col = 0
    for i, (im, title, sub, _) in enumerate(prepared):
        ax = fig.add_subplot(gs[0, col]); col += 1
        ax.imshow(im)                                    # default aspect='equal' → never distorts
        ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_edgecolor("#888"); sp.set_linewidth(0.8)
        name = title.split(" ", 1)[1] if title[:1] in "①②③④⑤⑥" else title
        ax.set_title(f"{i + 1}.   {name}", fontsize=12, fontweight="bold", pad=6)
        ax.text(0.5, -0.055, sub, transform=ax.transAxes, ha="center", va="top",
                fontsize=9, color="#444")
        if i < n - 1:
            aax = fig.add_subplot(gs[0, col]); col += 1; aax.axis("off")
            aax.annotate("", xy=(0.98, 0.5), xytext=(0.02, 0.5), xycoords="axes fraction",
                         arrowprops=dict(arrowstyle="-|>", color="#333", lw=2.2))

    fig.text(0.5, 1.12, "OPUS-ET-AGENT", ha="center", fontsize=25, fontweight="bold",
             family=WORDMARK, color="#1a1a1a")
    fig.text(0.5, 1.03,
             "from raw movies to molecular sociology — a supervised-autonomy cryo-ET pipeline",
             ha="center", fontsize=12.5, color="#555", style="italic")
    fig.savefig(args.out, dpi=150, bbox_inches="tight", facecolor="white")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
