#!/usr/bin/env python3
"""Score OPUS-ET conformational-state maps against a reference template (and the
internal consensus) to flag which k-means clusters are genuine particles vs junk.

Gate-3 helper. For each cluster-center reconstruction it reports a masked real-space
correlation to (a) an external reference template (e.g. the ribosome map used for
template matching) and (b) the consensus (mean of all cluster maps). Junk/artifact
clusters (edge slabs, ice fringes, near-empty) fall to the bottom on both.

Alignment note: OPUS-ET cluster maps are reconstructed in the consensus pose frame,
which coincides with the template-matching template's frame, so a DIRECT correlation
is valid (empirically the identity orientation beats every flip/transpose). No
rotational search is performed — if a future dataset is not pre-aligned, that
assumption must be revisited.

Grids are reconciled by FFT resampling (numpy only): the template is resampled onto
the cluster maps' pixel size and box, and both are optionally low-pass filtered to the
template's resolution so the comparison is fair (a coarse template is not penalised for
lacking the high-frequency detail present in the maps).

The numerical core (masked_cc, fourier_resize, resample_to_apix, center_fit, lowpass,
prepare_reference, consensus_map, compare_maps) is numpy-only and unit-tested; mrcfile
and matplotlib are imported lazily inside the I/O / rendering helpers.
"""
import argparse
import glob
import os
import re

import numpy as np


# ----------------------------------------------------------------------------
# Numerical core (numpy only)
# ----------------------------------------------------------------------------
def masked_cc(a, b, mask=None):
    """Weighted Pearson correlation of two volumes over an optional (soft) mask."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    w = np.ones_like(a) if mask is None else np.asarray(mask, np.float64)
    W = w.sum()
    if W <= 0:
        return 0.0
    am = (w * a).sum() / W
    bm = (w * b).sum() / W
    da, db = a - am, b - bm
    cov = (w * da * db).sum()
    va = (w * da * da).sum()
    vb = (w * db * db).sum()
    denom = np.sqrt(va * vb)
    return float(cov / denom) if denom > 0 else 0.0


def soft_sphere_mask(box, radius, edge=3.0):
    """Sphere mask centred in the box: 1 inside `radius`, cosine-free linear ramp to 0
    over `edge` voxels, 0 beyond radius+edge."""
    c = (box - 1) / 2.0
    zz, yy, xx = np.mgrid[:box, :box, :box]
    r = np.sqrt((zz - c) ** 2 + (yy - c) ** 2 + (xx - c) ** 2)
    edge = max(float(edge), 1e-6)
    m = np.clip((radius + edge - r) / edge, 0.0, 1.0)
    return m.astype(np.float32)


def fourier_resize(vol, new_N):
    """Resize a cubic volume to new_N^3 by symmetric Fourier crop/pad, preserving
    physical extent (i.e. changing the pixel size). Real input -> real output."""
    N = vol.shape[0]
    if new_N == N:
        return np.asarray(vol, np.float32).copy()
    F = np.fft.fftshift(np.fft.fftn(np.asarray(vol, np.float64)))
    if new_N > N:
        pad = new_N - N
        lo = pad // 2
        hi = pad - lo
        F2 = np.pad(F, ((lo, hi), (lo, hi), (lo, hi)))
    else:
        crop = N - new_N
        lo = crop // 2
        F2 = F[lo:lo + new_N, lo:lo + new_N, lo:lo + new_N]
    out = np.fft.ifftn(np.fft.ifftshift(F2)).real
    out *= (float(new_N) ** 3) / (float(N) ** 3)  # keep amplitudes (DC) invariant
    return out.astype(np.float32)


def resample_to_apix(vol, apix_in, apix_out):
    """Resample a cubic volume from apix_in to apix_out (box scales inversely)."""
    N = vol.shape[0]
    new_N = int(round(N * apix_in / apix_out))
    new_N = max(new_N, 1)
    return fourier_resize(vol, new_N), float(apix_out)


def center_fit(vol, box):
    """Center-crop or center-pad (zeros) a cubic volume to box^3."""
    N = vol.shape[0]
    if N == box:
        return np.asarray(vol).copy()
    if N > box:
        lo = (N - box) // 2
        return np.asarray(vol)[lo:lo + box, lo:lo + box, lo:lo + box].copy()
    out = np.zeros((box, box, box), np.asarray(vol).dtype)
    lo = (box - N) // 2
    out[lo:lo + N, lo:lo + N, lo:lo + N] = vol
    return out


def lowpass(vol, apix, res_A):
    """Gaussian low-pass filter to a target resolution (Angstrom), in Fourier space.
    DC is preserved (gain 1 at zero frequency), so the mean is unchanged."""
    N = vol.shape[0]
    f = np.fft.fftfreq(N, d=float(apix))  # cycles / Angstrom
    fz, fy, fx = np.meshgrid(f, f, f, indexing="ij")
    fr = np.sqrt(fz ** 2 + fy ** 2 + fx ** 2)
    fc = 1.0 / float(res_A)               # cutoff frequency
    sigma = fc / 2.0                      # ~e^-2 attenuation at the cutoff
    g = np.exp(-(fr ** 2) / (2.0 * sigma ** 2))
    F = np.fft.fftn(np.asarray(vol, np.float64)) * g
    return np.fft.ifftn(F).real.astype(np.float32)


def prepare_reference(template, tmpl_apix, dst_apix, dst_box, lowpass_A=None):
    """Put an external template onto the cluster maps' grid: resample to dst_apix,
    center-fit to dst_box, and optionally low-pass to lowpass_A."""
    v, _ = resample_to_apix(template, tmpl_apix, dst_apix)
    v = center_fit(v, dst_box)
    if lowpass_A:
        v = lowpass(v, dst_apix, lowpass_A)
    return v


def consensus_map(maps):
    """Mean of a list of same-shape maps (the internal, alignment-free reference)."""
    return np.mean(np.stack([np.asarray(m, np.float32) for m in maps], 0), 0)


def compare_maps(maps, reference, mask=None):
    """Masked CC of each map to `reference`. Returns an np.ndarray of floats."""
    return np.array([masked_cc(m, reference, mask) for m in maps], float)


# ----------------------------------------------------------------------------
# I/O + rendering (lazy heavy imports)
# ----------------------------------------------------------------------------
def load_mrc(path):
    """Return (data float32, apix float). apix falls back to 0.0 if unset."""
    import mrcfile
    with mrcfile.open(path, permissive=True) as m:
        data = np.asarray(m.data, np.float32)
        try:
            apix = float(m.voxel_size.x)
        except Exception:
            apix = 0.0
    return data, apix


def _cluster_id(path):
    m = re.findall(r"(\d+)", os.path.basename(path))
    return int(m[-1]) if m else 0


def cluster_sizes_from_labels(labels_pkl, k):
    """Occupancy per cluster from a kmeans labels.pkl (pickled array of length N)."""
    import pickle
    with open(labels_pkl, "rb") as f:
        labels = np.asarray(pickle.load(f)).ravel()
    return [int((labels == i).sum()) for i in range(k)]


def render_montage(maps, order, cc_t, cc_c, sizes, out_png, cols=5, title=""):
    """Central-Z-slice montage, panels ordered by `order`, annotated with scores."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    n = len(order)
    cols = min(cols, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(2.6 * cols, 2.8 * rows), squeeze=False)
    for ax_i, ax in enumerate(axes.flat):
        ax.axis("off")
        if ax_i >= n:
            continue
        ci = order[ax_i]
        img = maps[ci][maps[ci].shape[0] // 2]
        lo, hi = np.percentile(img, [2, 98])
        if hi > lo:
            img = np.clip((img - lo) / (hi - lo), 0, 1)
        ax.imshow(img, cmap="gray", origin="lower")
        lab = f"k{ci}"
        if sizes is not None:
            lab += f"  n={sizes[ci]}"
        sub = f"tmpl {cc_t[ci]:.2f}"
        if cc_c is not None:
            sub += f" | cons {cc_c[ci]:.2f}"
        ax.set_title(f"{lab}\n{sub}", fontsize=8)
    if title:
        fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--maps", required=True, help="glob for cluster maps, e.g. 'kmeans20/reference*.mrc'")
    ap.add_argument("--template", default="", help="external reference MRC (blank = consensus only)")
    ap.add_argument("--template-apix", type=float, default=0.0,
                    help="template pixel size (default: read from its MRC header)")
    ap.add_argument("-o", "--out-prefix", required=True)
    ap.add_argument("--mask", default="", help="optional mask MRC on the maps' grid")
    ap.add_argument("--mask-radius", type=float, default=0.0,
                    help="soft-sphere mask radius in voxels (default: 0.42*box)")
    ap.add_argument("--lowpass", type=float, default=0.0,
                    help="low-pass both to this resolution in A (default: 2*template_apix)")
    ap.add_argument("--labels", default="", help="kmeans labels.pkl for occupancy")
    ap.add_argument("--sort", choices=["template", "consensus", "cluster"], default="template")
    ap.add_argument("--cols", type=int, default=5)
    args = ap.parse_args()

    paths = sorted(glob.glob(args.maps), key=_cluster_id)
    if not paths:
        raise SystemExit(f"no maps match {args.maps}")
    maps, apixes = zip(*[load_mrc(p) for p in paths])
    maps = list(maps)
    box = maps[0].shape[0]
    map_apix = apixes[0] or 1.0
    k = len(maps)

    cons = consensus_map(maps)

    if args.mask:
        mask, _ = load_mrc(args.mask)
        mask = center_fit(mask, box)
    else:
        radius = args.mask_radius or 0.42 * box
        mask = soft_sphere_mask(box, radius)

    cc_c = compare_maps(maps, cons, mask)

    cc_t = None
    if args.template:
        tmpl, t_apix_hdr = load_mrc(args.template)
        t_apix = args.template_apix or t_apix_hdr or map_apix
        lp = args.lowpass or (2.0 * t_apix)
        ref = prepare_reference(tmpl, t_apix, map_apix, box, lowpass_A=lp)
        maps_lp = [lowpass(m, map_apix, lp) for m in maps]
        cc_t = compare_maps(maps_lp, ref, mask)

    sizes = None
    if args.labels:
        sizes = cluster_sizes_from_labels(args.labels, k)

    primary = cc_t if (args.sort == "template" and cc_t is not None) else cc_c
    if args.sort == "cluster":
        order = list(range(k))
    else:
        order = list(np.argsort(-primary))

    tsv = args.out_prefix + "_scores.tsv"
    with open(tsv, "w") as f:
        cols = ["cluster", "occupancy", "cc_template", "cc_consensus"]
        f.write("\t".join(cols) + "\n")
        for ci in order:
            f.write("\t".join([
                f"k{ci}",
                str(sizes[ci]) if sizes else "",
                f"{cc_t[ci]:.4f}" if cc_t is not None else "",
                f"{cc_c[ci]:.4f}",
            ]) + "\n")

    png = args.out_prefix + "_montage.png"
    render_montage(maps, order,
                   cc_t if cc_t is not None else cc_c, cc_c, sizes, png,
                   cols=args.cols,
                   title=os.path.basename(args.out_prefix) + " — states vs template/consensus")

    print(f"wrote {tsv}")
    print(f"wrote {png}")
    hdr = f"{'cluster':8} {'occ':>6} {'cc_tmpl':>8} {'cc_cons':>8}"
    print(hdr)
    print("-" * len(hdr))
    for ci in order:
        occ = str(sizes[ci]) if sizes else "-"
        t = f"{cc_t[ci]:.3f}" if cc_t is not None else "-"
        print(f"k{ci:<7} {occ:>6} {t:>8} {cc_c[ci]:>8.3f}")


if __name__ == "__main__":
    main()
