#!/usr/bin/env python3
"""Template-free consistency check between OPUS-ET conformational-state maps.

Gate-3 helper. Correlating each k-means cluster-center reconstruction to a single
external (and often much lower-resolution) template rewards smooth, blob-like maps
and penalises genuinely high-resolution but merely differently-shaped states — a bad
metric for judging *within-dataset* consistency. Since all cluster maps share the same
box/apix and were reconstructed in one consensus pose frame, a direct pairwise masked
correlation between reconstructions is fair and template-free: it asks "how much do
these states agree with EACH OTHER", not "how much do they look like a template".

This module builds the full N x N map-to-map correlation table, orders it by
hierarchical-clustering leaf order (so consistent groups of states appear as
contiguous, high-correlation blocks), and renders a labelled heatmap.

The numerical core (consistency_matrix, order_by_linkage) is numpy(+scipy)-only and
unit-tested; mrcfile and matplotlib are imported lazily inside the I/O / rendering
helpers. Reuses masked_cc / soft_sphere_mask / lowpass / load_mrc / cluster_sizes_from_labels
from compare_to_template.py rather than reimplementing them.
"""
import argparse
import glob
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_to_template as ct


# ----------------------------------------------------------------------------
# Numerical core (numpy [+ scipy for order_by_linkage] only)
# ----------------------------------------------------------------------------
def consistency_matrix(maps, mask=None, lowpass_A=None, apix=None):
    """All-pairs masked correlation between same-shape maps.

    Returns a symmetric (N, N) float ndarray with 1.0 on the diagonal. If `lowpass_A`
    is given, each map is low-pass filtered to that resolution (Angstrom) before
    correlating — `apix` is then required.
    """
    if lowpass_A:
        if apix is None:
            raise ValueError("apix is required when lowpass_A is set")
        maps = [ct.lowpass(m, apix, lowpass_A) for m in maps]
    else:
        maps = [np.asarray(m, np.float64) for m in maps]

    n = len(maps)
    mat = np.ones((n, n), float)
    for i in range(n):
        for j in range(i + 1, n):
            cc = ct.masked_cc(maps[i], maps[j], mask)
            mat[i, j] = cc
            mat[j, i] = cc
    return mat


def order_by_linkage(matrix):
    """Leaf order (list of indices) from hierarchical clustering that groups
    mutually-consistent classes together. Distance = 1 - correlation (condensed),
    average linkage, scipy's optimal `leaves_list` ordering. scipy lazy-imported.
    """
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform

    matrix = np.asarray(matrix, float)
    n = matrix.shape[0]
    if n <= 1:
        return list(range(n))
    dist = 1.0 - matrix
    dist = (dist + dist.T) / 2.0  # enforce exact symmetry against float noise
    np.fill_diagonal(dist, 0.0)
    dist = np.clip(dist, 0.0, None)
    condensed = squareform(dist, checks=False)
    Z = linkage(condensed, method="average")
    return list(leaves_list(Z))


# ----------------------------------------------------------------------------
# I/O + rendering (lazy heavy imports)
# ----------------------------------------------------------------------------
def render_heatmap(matrix, order, labels, sizes, out_png, highlight=frozenset(), title=""):
    """Clustered NxN heatmap PNG. Rows/cols are reordered by `order`; tick labels
    are annotated with cluster id (+ occupancy if `sizes` given); classes in
    `highlight` (a set of the *original* row/col indices) get a bold red tick label.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(order)
    reordered = matrix[np.ix_(order, order)]

    fig, ax = plt.subplots(figsize=(0.5 * n + 3, 0.5 * n + 3))
    im = ax.imshow(reordered, vmin=-1.0, vmax=1.0, cmap="RdBu_r")

    tick_text = []
    for idx in order:
        lab = labels[idx] if labels is not None else str(idx)
        if sizes is not None:
            lab = f"{lab} (n={sizes[idx]})"
        tick_text.append(lab)

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(tick_text, rotation=90, fontsize=7)
    ax.set_yticklabels(tick_text, fontsize=7)

    for tick_i, idx in enumerate(order):
        is_hl = idx in highlight
        color = "crimson" if is_hl else "black"
        weight = "bold" if is_hl else "normal"
        ax.get_xticklabels()[tick_i].set_color(color)
        ax.get_xticklabels()[tick_i].set_fontweight(weight)
        ax.get_yticklabels()[tick_i].set_color(color)
        ax.get_yticklabels()[tick_i].set_fontweight(weight)

    # box the contiguous run(s) of highlighted classes in the reordered matrix
    hl_positions = sorted(i for i, idx in enumerate(order) if idx in highlight)
    if hl_positions:
        run_start = hl_positions[0]
        prev = hl_positions[0]
        for p in hl_positions[1:] + [None]:
            if p is not None and p == prev + 1:
                prev = p
                continue
            lo, hi = run_start - 0.5, prev + 0.5
            ax.add_patch(plt.Rectangle((lo, lo), hi - lo, hi - lo,
                                        fill=False, edgecolor="crimson", linewidth=2))
            if p is not None:
                run_start = prev = p

    fig.colorbar(im, ax=ax, shrink=0.8, label="masked CC")
    if title:
        ax.set_title(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _cluster_id(path):
    m = re.findall(r"(\d+)", os.path.basename(path))
    return int(m[-1]) if m else 0


def _print_block(matrix, order, labels, sizes, highlight):
    hdr = " " * 9 + "".join(f"{labels[i]:>7}" for i in order)
    print(hdr)
    for i in order:
        row = "".join(f"{matrix[i, j]:7.2f}" for j in order)
        mark = "*" if i in highlight else " "
        occ = f"  n={sizes[i]}" if sizes is not None else ""
        print(f"{labels[i]:<7}{mark} {row}{occ}")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maps", required=True,
                    help="glob for cluster maps, e.g. 'kmeans20/reference*.mrc'")
    ap.add_argument("--labels", default="", help="kmeans labels.pkl for occupancy (optional)")
    ap.add_argument("--lowpass", type=float, default=0.0,
                    help="low-pass all maps to this resolution (A) before correlating "
                         "(default: 0 = off, raw maps)")
    ap.add_argument("--highlight", default="",
                    help="comma-separated cluster ids to mark, e.g. '8,9,10,11'")
    ap.add_argument("--mask-radius", type=float, default=0.0,
                    help="soft-sphere mask radius in voxels (default: 0.42*box)")
    ap.add_argument("--apix", type=float, default=0.0,
                    help="pixel size (default: read from the first map's header)")
    ap.add_argument("-o", "--out-prefix", required=True)
    args = ap.parse_args()

    paths = sorted(glob.glob(args.maps), key=_cluster_id)
    if not paths:
        raise SystemExit(f"no maps match {args.maps}")

    maps, apixes = zip(*[ct.load_mrc(p) for p in paths])
    maps = list(maps)
    ids = [_cluster_id(p) for p in paths]
    box = maps[0].shape[0]
    apix = args.apix or apixes[0] or 1.0
    k = len(maps)

    mask_radius = args.mask_radius or 0.42 * box
    mask = ct.soft_sphere_mask(box, mask_radius)

    lowpass_A = args.lowpass or None
    mat = consistency_matrix(maps, mask=mask, lowpass_A=lowpass_A, apix=apix)

    sizes = None
    if args.labels:
        sizes_by_cluster = ct.cluster_sizes_from_labels(args.labels, max(ids) + 1)
        sizes = [sizes_by_cluster[cid] for cid in ids]

    order = order_by_linkage(mat)

    highlight_ids = {int(x) for x in args.highlight.split(",") if x.strip() != ""}
    highlight = frozenset(i for i, cid in enumerate(ids) if cid in highlight_ids)

    labels_txt = [f"k{cid}" for cid in ids]

    tsv = args.out_prefix + "_matrix.tsv"
    with open(tsv, "w") as f:
        f.write("\t" + "\t".join(labels_txt) + "\n")
        for i in range(k):
            f.write(labels_txt[i] + "\t" +
                    "\t".join(f"{mat[i, j]:.4f}" for j in range(k)) + "\n")

    lp_txt = f" (lowpass {args.lowpass:g} A)" if args.lowpass else " (raw)"
    title = os.path.basename(args.out_prefix) + " — state consistency" + lp_txt

    png = args.out_prefix + "_heatmap.png"
    render_heatmap(mat, order, labels_txt, sizes, png, highlight=highlight, title=title)

    print(f"wrote {tsv}")
    print(f"wrote {png}")
    print()
    print("linkage-ordered block structure" + lp_txt + ":")
    _print_block(mat, order, labels_txt, sizes, highlight)


if __name__ == "__main__":
    main()
