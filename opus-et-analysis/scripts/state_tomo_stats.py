#!/usr/bin/env python3
"""Per-tomogram statistics for a Gate-3 k-means cluster selection.

Gate-3 helper. After OPUS-ET clusters particles into k-means states and the user
picks the "good" (real-particle) clusters, this reports, per tomogram: how many of
its picks landed in the selected clusters (n_selected) and what fraction of its total
picks that is (frac_selected).

Why this matters: WARP/RELION template matching keeps a fixed number of top-scoring
picks per tomogram (a per-tomogram cap). If a tomogram's frac_selected is high, that
cap is very likely truncating REAL particles before they even reach k-means — i.e.
the tomogram is particle-dense and picking more (raising its cap / re-picking) would
likely recover additional genuine particles. A low frac_selected instead means most
of that tomogram's picks are junk/artifacts, so raising its cap would mostly add noise.

The numerical core (per_tomo_stats) is numpy-only and unit-tested; starfile, pickle
and matplotlib are imported lazily inside the I/O / rendering helpers.
"""
import argparse
import os
import re

import numpy as np


# ----------------------------------------------------------------------------
# Numerical core (numpy only)
# ----------------------------------------------------------------------------
def per_tomo_stats(tomo_of_particle, cluster_of_particle, selected_clusters):
    """Per-tomogram selection stats.

    tomo_of_particle: length-N array-like, a tomogram id per particle.
    cluster_of_particle: length-N array-like, a k-means cluster id per particle
        (same order as tomo_of_particle).
    selected_clusters: iterable of cluster ids considered "good" (the user's selection).

    Returns a list of dicts, one per distinct tomogram, each with keys
    tomo, n_total, n_selected, frac_selected — sorted by n_selected descending
    (ties broken by tomogram id ascending, for a deterministic order).
    """
    tomo_of_particle = np.asarray(tomo_of_particle)
    cluster_of_particle = np.asarray(cluster_of_particle)
    if tomo_of_particle.shape[0] != cluster_of_particle.shape[0]:
        raise ValueError(
            f"tomo_of_particle ({tomo_of_particle.shape[0]}) and cluster_of_particle "
            f"({cluster_of_particle.shape[0]}) must be the same length"
        )

    selected = set(int(c) for c in selected_clusters)
    if selected:
        is_selected = np.isin(cluster_of_particle, list(selected))
    else:
        is_selected = np.zeros(cluster_of_particle.shape[0], dtype=bool)

    results = []
    for t in sorted(set(tomo_of_particle.tolist())):
        mask = tomo_of_particle == t
        n_total = int(mask.sum())
        n_selected = int(np.logical_and(mask, is_selected).sum())
        frac_selected = (n_selected / n_total) if n_total > 0 else 0.0
        results.append({
            "tomo": t,
            "n_total": n_total,
            "n_selected": n_selected,
            "frac_selected": frac_selected,
        })

    results.sort(key=lambda d: (-d["n_selected"], d["tomo"]))
    return results


# ----------------------------------------------------------------------------
# I/O + rendering (lazy heavy imports)
# ----------------------------------------------------------------------------
def _tomo_name(name):
    """Strip directory and a trailing .tomostar/.mrc/.star extension from an
    rlnMicrographName value, e.g. '.../TS_028.tomostar' -> 'TS_028'."""
    base = str(name).rsplit("/", 1)[-1]
    base = re.sub(r"\.(tomostar|mrc|star)$", "", base)
    return base


def read_particles(star_path, labels_path):
    """Return (tomo_of_particle, cluster_of_particle) numpy arrays, in the same
    particle order as the star file.

    `star_path` is a RELION/WARP particles star (read with starfile); the per-particle
    tomogram id is parsed from rlnMicrographName. `labels_path` is a k-means labels.pkl
    (pickled 1-D array of cluster ids), assumed to be in the same particle order as the
    star (as produced by the OPUS-ET analyze/kmeans pipeline).
    """
    import pickle

    import starfile

    df = starfile.read(star_path)
    if not hasattr(df, "columns"):
        # some star files split into multiple blocks (e.g. optics + particles)
        for block in df.values():
            if hasattr(block, "columns") and "rlnMicrographName" in block.columns:
                df = block
                break
        else:
            raise ValueError(f"no particle block with rlnMicrographName in {star_path}")

    tomo_of_particle = df["rlnMicrographName"].map(_tomo_name).to_numpy()

    with open(labels_path, "rb") as f:
        cluster_of_particle = np.asarray(pickle.load(f)).ravel()

    if tomo_of_particle.shape[0] != cluster_of_particle.shape[0]:
        raise ValueError(
            f"particle count mismatch: {star_path} has {tomo_of_particle.shape[0]} rows, "
            f"{labels_path} has {cluster_of_particle.shape[0]}"
        )

    return tomo_of_particle, cluster_of_particle


def render_bar_chart(stats, out_png, title="", top_tier_frac=0.25):
    """Bar chart, one bar per tomogram, ordered as given in `stats` (n_selected desc).

    A light background bar shows n_total; the foreground bar shows n_selected; each bar
    is annotated with frac_selected. The tomograms in the top `top_tier_frac` by
    frac_selected (the densest — likely truncated by the per-tomogram pick cap, and
    thus candidates to re-pick more) are drawn in a distinct color.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(stats)
    tomos = [s["tomo"] for s in stats]
    n_total = np.array([s["n_total"] for s in stats], float)
    n_selected = np.array([s["n_selected"] for s in stats], float)
    frac = np.array([s["frac_selected"] for s in stats], float)

    if n > 0:
        k_dense = max(1, int(np.ceil(top_tier_frac * n)))
        dense_idx = set(np.argsort(-frac)[:k_dense].tolist())
    else:
        dense_idx = set()

    fig, ax = plt.subplots(figsize=(max(6.0, 0.6 * n + 2), 5.0))
    x = np.arange(n)
    ax.bar(x, n_total, color="0.85", zorder=1, label="n_total (all picks)")
    colors = ["crimson" if i in dense_idx else "steelblue" for i in range(n)]
    ax.bar(x, n_selected, color=colors, zorder=2, label="n_selected (selected clusters)")

    ymax = max(n_total.max(), 1.0) if n > 0 else 1.0
    for xi, (ns, fr) in enumerate(zip(n_selected, frac)):
        ax.text(xi, ns + 0.015 * ymax, f"{fr:.0%}", ha="center", va="bottom", fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(tomos, rotation=45, ha="right", fontsize=9)
    ax.set_ylabel("# particles")
    ax.set_title(title or "Gate-3: per-tomogram selected fraction")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _print_table(stats):
    hdr = f"{'tomo':<12}{'n_total':>10}{'n_selected':>12}{'frac_selected':>15}"
    print(hdr)
    print("-" * len(hdr))
    for s in stats:
        print(f"{s['tomo']:<12}{s['n_total']:>10}{s['n_selected']:>12}{s['frac_selected']:>15.4f}")
    print("-" * len(hdr))
    n_total_all = sum(s["n_total"] for s in stats)
    n_selected_all = sum(s["n_selected"] for s in stats)
    frac_all = (n_selected_all / n_total_all) if n_total_all else 0.0
    print(f"{'TOTAL':<12}{n_total_all:>10}{n_selected_all:>12}{frac_all:>15.4f}")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--star", required=True, help="particles star, e.g. ribo_matching.star")
    ap.add_argument("--labels", required=True,
                    help="kmeans labels.pkl (same particle order as --star)")
    ap.add_argument("--select", required=True,
                    help="space/comma-separated 'good' cluster ids, e.g. '6 8 9 10'")
    ap.add_argument("-o", "--out-prefix", required=True)
    args = ap.parse_args()

    selected_clusters = [int(x) for x in re.split(r"[\s,]+", args.select.strip()) if x != ""]

    tomo_of_particle, cluster_of_particle = read_particles(args.star, args.labels)
    stats = per_tomo_stats(tomo_of_particle, cluster_of_particle, selected_clusters)

    tsv = args.out_prefix + "_tomo_stats.tsv"
    with open(tsv, "w") as f:
        f.write("tomo\tn_total\tn_selected\tfrac_selected\n")
        for s in stats:
            f.write(f"{s['tomo']}\t{s['n_total']}\t{s['n_selected']}\t{s['frac_selected']:.4f}\n")

    title = (os.path.basename(args.out_prefix) +
             f" — Gate-3 selected fraction per tomogram (clusters {sorted(selected_clusters)})")
    png = args.out_prefix + "_tomo_stats.png"
    render_bar_chart(stats, png, title=title)

    print(f"wrote {tsv}")
    print(f"wrote {png}")
    print()
    _print_table(stats)


if __name__ == "__main__":
    main()
