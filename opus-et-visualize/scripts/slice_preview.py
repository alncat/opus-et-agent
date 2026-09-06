#!/usr/bin/env python3
"""Central-slice preview PNGs from a tomogram MRC — for Gate 1 alignment/recon QC.

Renders the central XY (top-down) and XZ (side) slices of a 3D MRC volume as small
grayscale PNGs with percentile contrast, so alignment/reconstruction quality can be
eyeballed (and judged by QC agents) without pulling whole volumes off the cluster.

Pure-numpy slice/normalize logic is import-light and unit-tested anywhere; the PNG
writer imports matplotlib lazily (present in the cluster analysis envs), so this
module imports fine even where matplotlib is absent.
"""
import argparse

import numpy as np


def central_slices(volume, z=None, y=None):
    """(xy, xz) slices of a 3D array shaped (Z, Y, X).

    xy = the plane at ``z`` (default: central Z), top-down;
    xz = the plane at ``y`` (default: central Y), side.

    Indices are clamped into range, so asking for a depth beyond the volume
    yields its edge slice rather than raising.
    """
    vol = np.asarray(volume)
    if vol.ndim != 3:
        raise ValueError(f"expected a 3D volume, got shape {vol.shape}")
    nz, ny = vol.shape[:2]
    zi = nz // 2 if z is None else max(0, min(int(z), nz - 1))
    yi = ny // 2 if y is None else max(0, min(int(y), ny - 1))
    return vol[zi, :, :], vol[:, yi, :]


def normalize(img, low=1.0, high=99.0):
    """Percentile-clip to [0, 1] for display contrast (robust to outliers/hot pixels)."""
    arr = np.asarray(img, dtype=np.float32)
    lo, hi = np.percentile(arr, [low, high])
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def save_slice_png(img, out_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.imsave(out_path, normalize(img), cmap="gray")
    return out_path


def preview_mrc(mrc_path, out_prefix, z=None, y=None):
    """Write <out_prefix>_xy.png and <out_prefix>_xz.png; return the two paths."""
    import mrcfile

    with mrcfile.open(mrc_path, permissive=True) as m:
        vol = np.asarray(m.data, dtype=np.float32)
    xy, xz = central_slices(vol, z=z, y=y)
    return [save_slice_png(xy, f"{out_prefix}_xy.png"),
            save_slice_png(xz, f"{out_prefix}_xz.png")]


def main():
    ap = argparse.ArgumentParser(description="Central-slice preview PNGs from a tomogram MRC.")
    ap.add_argument("mrc", help="input tomogram MRC")
    ap.add_argument("-o", "--out-prefix", required=True, help="output PNG path prefix")
    ap.add_argument("--z", type=int, default=None,
                    help="Z index for the XY slice (default: central)")
    ap.add_argument("--y", type=int, default=None,
                    help="Y index for the XZ slice (default: central)")
    args = ap.parse_args()
    for p in preview_mrc(args.mrc, args.out_prefix, z=args.z, y=args.y):
        print(p)


if __name__ == "__main__":
    main()
