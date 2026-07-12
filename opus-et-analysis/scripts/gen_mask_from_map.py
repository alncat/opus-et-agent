#!/usr/bin/env python3
"""Derive a soft molecule mask from a reconstruction (Gate-4 half-map QC helper).

A gold-standard FSC between two independent half-maps needs a mask that follows
the actual density, not a soft *sphere*: a sphere mask correlates with itself at
high frequency (mask self-correlation), which shows up as a spurious FSC rise at
the high-frequency end and inflates the apparent resolution. A mask thresholded
from the density and given a soft (raised-cosine) edge avoids that artifact and
doubles as the M_MASK needed for downstream M refinement.

Pipeline (`molecule_mask`): pick a threshold (mean + threshold_sigma*std of the
volume, unless an explicit `threshold` is given) -> binarize -> keep only the
largest connected component (drops solvent/ice specks that pass threshold but
are not part of the particle) -> binary-dilate by `dilate_px` (grow onto density
just below threshold) -> raised-cosine soft edge of width `soft_edge_px`, built
from a distance transform so voxels inside the dilated mask are 1, voxels beyond
`soft_edge_px` are 0, and the ramp in between is C^1-continuous like RELION's.

The numerical core (molecule_mask) is numpy(+scipy.ndimage)-only and unit-tested;
mrcfile and matplotlib stay lazy inside the I/O helpers below. Reuses
compare_to_template.lowpass for the optional pre-threshold low-pass (reduces
noise specks that would otherwise survive thresholding and get mistaken for a
tiny second connected component).
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_to_template as ct


# ----------------------------------------------------------------------------
# Numerical core (numpy + scipy.ndimage only)
# ----------------------------------------------------------------------------
def molecule_mask(vol, threshold=None, threshold_sigma=1.0, dilate_px=3, soft_edge_px=3):
    """Soft [0, 1] molecule mask derived from a density map.

    threshold: absolute density threshold; if None, uses mean + threshold_sigma*std.
    dilate_px: binary dilation radius (voxels) applied to the largest component.
    soft_edge_px: width (voxels) of the raised-cosine soft edge beyond the
        (dilated) binary mask; 0 disables the ramp (mask stays hard-edged).
    """
    vol = np.asarray(vol, np.float64)

    if threshold is None:
        threshold = float(vol.mean() + threshold_sigma * vol.std())

    binary = vol > threshold
    if not binary.any():
        return np.zeros(vol.shape, np.float32)

    from scipy.ndimage import label
    labeled, n = label(binary)
    if n > 1:
        sizes = np.bincount(labeled.ravel())
        sizes[0] = 0  # background label never wins
        largest = int(np.argmax(sizes))
        binary = labeled == largest

    if dilate_px and dilate_px > 0:
        from scipy.ndimage import binary_dilation
        binary = binary_dilation(binary, iterations=int(dilate_px))

    if soft_edge_px and soft_edge_px > 0:
        from scipy.ndimage import distance_transform_edt
        dist = distance_transform_edt(~binary)
        soft = np.zeros(vol.shape, np.float64)
        soft[binary] = 1.0
        ramp = (~binary) & (dist < soft_edge_px)
        soft[ramp] = 0.5 * (1.0 + np.cos(np.pi * dist[ramp] / float(soft_edge_px)))
    else:
        soft = binary.astype(np.float64)

    return soft.astype(np.float32)


# ----------------------------------------------------------------------------
# Overlay QC (mask envelope over the density)
# ----------------------------------------------------------------------------
def overlay_slices(vol, mask):
    """Central slices of `vol` and `mask` along Z, Y, X (the XY / XZ / YZ views).

    Returns a list of three (density_2d, mask_2d) pairs — the material for a
    "does the mask envelope wrap the density without clipping it?" QC figure.
    """
    vol = np.asarray(vol)
    mask = np.asarray(mask)
    if vol.shape != mask.shape:
        raise ValueError(f"vol {vol.shape} and mask {mask.shape} must have the same shape")
    nz, ny, nx = vol.shape
    return [
        (vol[nz // 2], mask[nz // 2]),                 # XY (central Z)
        (vol[:, ny // 2], mask[:, ny // 2]),           # XZ (central Y)
        (vol[:, :, nx // 2], mask[:, :, nx // 2]),     # YZ (central X)
    ]


def write_overlay_png(vol, mask, out_png, apix=None, contour=0.5):
    """Render the mask boundary (at `contour`) over the density central slices.

    Grayscale density with a red mask outline in three orthogonal views — the
    Gate-4 visual proof that the mask follows the molecule and doesn't clip it.
    matplotlib is imported lazily (Agg) so the numerical core stays import-light.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    panels = overlay_slices(vol, mask)
    titles = ["XY (central Z)", "XZ (central Y)", "YZ (central X)"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.2))
    for ax, (dens, msk), title in zip(axes, panels, titles):
        finite = dens[np.isfinite(dens)]
        vmin, vmax = (np.percentile(finite, [2, 98]) if finite.size else (0.0, 1.0))
        if vmax <= vmin:
            vmax = vmin + 1.0
        ax.imshow(dens, cmap="gray", vmin=vmin, vmax=vmax, origin="lower")
        if float(msk.max()) > float(msk.min()):        # mask envelope outline
            ax.contour(msk, levels=[contour], colors="#ff3b30", linewidths=1.2)
        ax.set_title(title, fontsize=10)
        ax.set_xticks([]); ax.set_yticks([])
    frac = float(np.asarray(mask).sum() / np.asarray(mask).size)
    sub = f"enclosed fraction {frac:.3f}"
    if apix:
        sub += f"  ·  {float(apix):.2f} Å/px"
    fig.suptitle(f"mask–density overlay QC   ({sub})", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    return out_png


# ----------------------------------------------------------------------------
# I/O (lazy heavy imports)
# ----------------------------------------------------------------------------
def write_mrc(path, data, apix):
    """Write a float32 MRC, setting the voxel size (apix) in the header."""
    import mrcfile
    with mrcfile.new(path, overwrite=True) as m:
        m.set_data(np.asarray(data, np.float32))
        if apix:
            m.voxel_size = float(apix)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", default="", help="reconstruction MRC to threshold")
    ap.add_argument("--half1", default="", help="half-map 1 (used with --half2 to average)")
    ap.add_argument("--half2", default="", help="half-map 2 (used with --half1 to average)")
    ap.add_argument("--threshold", type=float, default=None,
                    help="absolute density threshold (default: mean + threshold-sigma*std)")
    ap.add_argument("--threshold-sigma", type=float, default=1.0,
                    help="threshold = mean + threshold_sigma*std when --threshold is unset")
    ap.add_argument("--dilate", type=int, default=3, help="binary dilation radius in voxels")
    ap.add_argument("--soft-edge", type=int, default=3,
                    help="raised-cosine soft-edge width in voxels (0 = hard edge)")
    ap.add_argument("--lowpass", type=float, default=0.0,
                    help="low-pass to this resolution (A) before thresholding, to "
                         "suppress noise specks (default: 0 = off)")
    ap.add_argument("-o", "--out", required=True, help="output mask MRC")
    ap.add_argument("--qc", default="",
                    help="also write a mask–density overlay QC PNG here (3 orthogonal "
                         "central slices with the mask envelope outlined)")
    args = ap.parse_args()

    if args.map:
        vol, apix = ct.load_mrc(args.map)
    elif args.half1 and args.half2:
        v1, apix1 = ct.load_mrc(args.half1)
        v2, apix2 = ct.load_mrc(args.half2)
        vol = (v1.astype(np.float64) + v2.astype(np.float64)) / 2.0
        apix = apix1 or apix2
    else:
        raise SystemExit("must provide --map, or both --half1 and --half2")

    src = vol
    if args.lowpass:
        src = ct.lowpass(vol, apix or 1.0, args.lowpass)

    mask = molecule_mask(src, threshold=args.threshold, threshold_sigma=args.threshold_sigma,
                         dilate_px=args.dilate, soft_edge_px=args.soft_edge)

    write_mrc(args.out, mask, apix)

    frac = float(mask.sum() / mask.size)
    print(f"wrote {args.out}")
    print(f"enclosed fraction (mask.sum()/mask.size): {frac:.4f}")

    if args.qc:
        write_overlay_png(vol, mask, args.qc, apix=apix)
        print(f"wrote QC overlay {args.qc}")


if __name__ == "__main__":
    main()
