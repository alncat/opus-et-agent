#!/usr/bin/env python3
"""Template-matching picks QC: tomogram z-slabs with picks overlaid.

Renders thin z-slabs of a tomogram (mean/min/max projection, ~one particle
thick) with template-matching picks drawn on top, so picking quality can be
eyeballed before export/training. This is the picking analogue of
``slice_preview.py`` (Gate 1) and the visual companion to
``tm_eval_agreement.py`` (which scores picks numerically).

Two PNG sets per slab: all in-slab picks, and the globally top-N-by-score picks
that fall in that slab.

Picks come from a RELION ``.star`` (rlnCoordinateX/Y/Z, score auto-detected) or a
PyTOM ``particles.xml`` (PickPosition + FLCFScore). Coordinates are reconciled to
the tomogram's pixel grid through Angstroms, so bin1 picks over a binned tomogram
and PyTOM picks already on the tomogram grid are handled by one code path.

Pure-numpy core; matplotlib is imported lazily for PNG output (mirroring
slice_preview), so the module imports and its logic tests fine without matplotlib.
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

# Score columns tried, in priority order. Mirrors tm_eval_agreement.SCORE_COLS;
# kept local so opus-et-visualize does not import opus-et-analysis.
SCORE_COLS = ["rlnAutopickFigureOfMerit", "rlnScore", "rlnLCCmax",
              "ccc", "score", "FLCF", "pytom_score"]
PIXEL_COLS = ["rlnPixelSize", "rlnDetectorPixelSize"]

DEFAULT_N_SLABS = 6
DEFAULT_SLAB_THICKNESS = 24      # px; ~one 320 A ribosome at 13.48 A/px
DEFAULT_TOP_N = 200

try:  # reuse Gate-1 percentile contrast when importable (same scripts/ dir)
    from slice_preview import normalize
except Exception:  # pragma: no cover - fallback keeps this module self-contained
    def normalize(img, low=1.0, high=99.0):
        arr = np.asarray(img, dtype=np.float32)
        lo, hi = np.percentile(arr, [low, high])
        if hi <= lo:
            hi = lo + 1.0
        return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


# --------------------------------------------------------------------------- #
# Reading picks
# --------------------------------------------------------------------------- #

def _norm_tomo(name):
    base = str(name).rsplit("/", 1)[-1]
    base = re.sub(r"\.(tomostar|mrc|star)$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"_[0-9.]+Apx$", "", base, flags=re.IGNORECASE)
    return base


def _read_star_block(path):
    """Return the particle DataFrame, robust to blank lines after ``loop_`` that
    some RELION writers (e.g. convert_to_star) emit and that break starfile.read."""
    import os
    import pandas as pd
    import starfile

    try:
        data = starfile.read(str(path))
    except Exception:
        cleaned = "\n".join(ln for ln in Path(path).read_text().splitlines()
                            if ln.strip() != "")
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".star")
        try:
            with os.fdopen(fd, "w") as tf:
                tf.write(cleaned + "\n")
            data = starfile.read(tmp)
        finally:
            os.unlink(tmp)

    if isinstance(data, pd.DataFrame):
        return data
    for block in data.values():
        if hasattr(block, "columns") and "rlnCoordinateX" in block.columns:
            return block
    raise ValueError(f"no particle block with rlnCoordinateX in {path}")


def read_picks_star(path, coords_angpix=None, tomo=None, warn=None):
    df = _read_star_block(path)
    if "rlnMicrographName" in df.columns:
        norm = df["rlnMicrographName"].map(_norm_tomo)
        if tomo is not None:
            df = df[norm == _norm_tomo(tomo)]
        elif norm.nunique() > 1 and warn is not None:
            warn(f"{path}: star spans {norm.nunique()} tomograms and no --tomo filter "
                 f"was given; overlaying ALL of them on this one tomogram")
    df = df.reset_index(drop=True)

    coords = np.stack([
        df["rlnCoordinateX"].to_numpy(float),
        df["rlnCoordinateY"].to_numpy(float),
        df["rlnCoordinateZ"].to_numpy(float),
    ], axis=1)

    src = None
    for c in PIXEL_COLS:
        if c in df.columns:
            src = float(df[c].iloc[0])
            break
    if src is None:
        src = coords_angpix
    if src is None:
        raise ValueError(
            f"{path}: no pixel-size column ({PIXEL_COLS}); pass --coords-angpix")

    scores = None
    for c in SCORE_COLS:
        if c in df.columns:
            scores = df[c].to_numpy(float)
            break
    return coords, scores, src


def read_picks_xml(path, coords_angpix=None):
    root = ET.parse(str(path)).getroot()
    coords, scores = [], []
    any_score = False
    for part in root.iter("Particle"):
        pp = part.find("PickPosition")
        if pp is None:
            continue
        coords.append([float(pp.get("X")), float(pp.get("Y")), float(pp.get("Z"))])
        sc = part.find("Score")
        val = sc.get("Value") if sc is not None else None
        if val in (None, ""):
            scores.append(np.nan)          # per-particle miss; keep the others
        else:
            scores.append(float(val))
            any_score = True
    coords = np.asarray(coords, float).reshape(-1, 3)
    scores = np.asarray(scores, float) if any_score else None
    # coords are already on the tomogram grid unless the caller overrides the
    # source pixel size via --coords-angpix.
    return coords, scores, coords_angpix


def read_picks(path, coords_angpix=None, tomo=None, warn=None):
    """Dispatch by extension. Returns ``(coords_src (N,3), scores (N,) or None,
    source_angpix or None)``."""
    if Path(path).suffix.lower() == ".xml":
        return read_picks_xml(path, coords_angpix=coords_angpix)
    return read_picks_star(path, coords_angpix=coords_angpix, tomo=tomo, warn=warn)


def to_tomo_pixels(coords_src, source_angpix, tomo_angpix):
    """Map source-frame coordinates to tomogram pixels via Angstroms.

    ``source_angpix is None`` means the coordinates are already on the tomogram
    grid (PyTOM), so they pass through unchanged.
    """
    coords = np.asarray(coords_src, float)
    if source_angpix is None:
        return coords.copy()
    return coords * float(source_angpix) / float(tomo_angpix)


# --------------------------------------------------------------------------- #
# Slab geometry
# --------------------------------------------------------------------------- #

def slab_band(z0, thickness, nz):
    """Half-open ``[lo, hi)`` band of width ``thickness`` centered on ``z0``,
    clipped into ``[0, nz)``. Used by BOTH projection and pick-inclusion so a
    marker never lands on a slab it was not projected into."""
    thickness = int(thickness)
    if thickness <= 0:
        raise ValueError(f"thickness must be a positive integer, got {thickness}")
    half = thickness // 2
    lo = max(0, int(round(z0)) - half)
    hi = min(int(nz), lo + thickness)
    lo = max(0, hi - thickness)
    return lo, hi


def slab_project(vol, z0, thickness, mode="mean"):
    vol = np.asarray(vol)
    if vol.ndim != 3:
        raise ValueError(f"expected a 3D volume, got shape {vol.shape}")
    lo, hi = slab_band(z0, thickness, vol.shape[0])
    sub = vol[lo:hi]
    if sub.shape[0] == 0:  # defensive; slab_band keeps hi>lo for nz>=1
        k = min(lo, vol.shape[0] - 1)
        sub = vol[k:k + 1]
    if mode == "mean":
        return sub.mean(axis=0)
    if mode == "min":
        return sub.min(axis=0)
    if mode == "max":
        return sub.max(axis=0)
    raise ValueError(f"unknown projection mode {mode!r}")


def picks_in_slab(coords_tomo, z0, thickness, nz):
    lo, hi = slab_band(z0, thickness, nz)
    z = np.asarray(coords_tomo, float)[:, 2]
    return (z >= lo) & (z < hi)


def plan_slab_centers(z_coords, n_slabs, nz):
    """``n_slabs`` bin-midpoint centers evenly spanning the picks' z-range (or the
    whole volume when there are no picks)."""
    n = int(n_slabs)
    z = np.asarray(z_coords, float)
    if z.size == 0:
        zmin, zmax = 0.0, float(nz)
    else:
        zmin, zmax = float(z.min()), float(z.max())
    if zmax <= zmin:
        zmax = zmin + 1.0
    span = zmax - zmin
    return [int(round(zmin + (i + 0.5) * span / n)) for i in range(n)]


def select_top_n(scores, n):
    """Indices of the ``n`` highest FINITE scores (descending). ``None`` when
    ``scores is None``; caps at the number of finite scores so NaN/blank scores
    are never ranked as top picks; all finite indices when ``n >= #finite``."""
    if scores is None:
        return None
    scores = np.asarray(scores, float)
    finite = np.isfinite(scores)
    n = min(int(n), int(finite.sum()))
    if n <= 0:
        return np.array([], int)
    ranked = np.where(finite, scores, -np.inf)   # push NaN/blank to the bottom
    return np.argsort(ranked)[::-1][:n]


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _render(img2d, picks_xy, out_path, color, marker_size, title=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ny, nx = img2d.shape
    fig, ax = plt.subplots(figsize=(6.0, 6.0 * ny / max(1, nx)))
    ax.imshow(normalize(img2d), cmap="gray", origin="lower")
    picks_xy = np.asarray(picks_xy, float).reshape(-1, 2)
    if len(picks_xy):
        ax.scatter(picks_xy[:, 0], picks_xy[:, 1], s=marker_size,
                   facecolors="none", edgecolors=color, linewidths=0.8)
    ax.set_xlim(-0.5, nx - 0.5)
    ax.set_ylim(-0.5, ny - 0.5)
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=8)
    fig.savefig(out_path, dpi=120, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return out_path


def overlay_picks(tomogram, picks, out_prefix, coords_angpix=None, tomo=None,
                  n_slabs=DEFAULT_N_SLABS, slab_thickness=DEFAULT_SLAB_THICKNESS,
                  top_n=DEFAULT_TOP_N, project="mean",
                  all_color="cyan", top_color="red", marker_size=40, log=print):
    import mrcfile

    with mrcfile.open(tomogram, permissive=True) as m:
        vol = np.asarray(m.data, dtype=np.float32)
        tomo_angpix = float(m.voxel_size.x)
    if vol.ndim != 3:
        raise ValueError(f"tomogram is not 3D: shape {vol.shape}")
    if not tomo_angpix or tomo_angpix <= 0:
        log(f"WARNING: tomogram voxel_size is {tomo_angpix}; defaulting to 1.0 A/px "
            f"-- coordinate scaling from a .star will be wrong (pass a tomogram with a "
            f"correct header, or picks already on this grid)")
        tomo_angpix = 1.0
    nz = vol.shape[0]

    coords_src, scores, src_angpix = read_picks(
        picks, coords_angpix=coords_angpix, tomo=tomo, warn=log)
    coords = to_tomo_pixels(coords_src, src_angpix, tomo_angpix)
    n_total = len(coords)

    has_scores = scores is not None
    top_idx = select_top_n(scores, top_n)
    is_top = np.zeros(n_total, bool)
    if top_idx is not None and n_total:
        is_top[top_idx] = True

    z_for_plan = coords[:, 2] if n_total else np.array([])
    centers = plan_slab_centers(z_for_plan, n_slabs, nz)

    Path(out_prefix).parent.mkdir(parents=True, exist_ok=True)
    outputs = []
    shown = np.zeros(n_total, bool)
    for z0 in centers:
        img = slab_project(vol, z0, slab_thickness, mode=project)
        in_slab = (picks_in_slab(coords, z0, slab_thickness, nz)
                   if n_total else np.zeros(0, bool))
        if n_total:
            shown |= in_slab
        p_all = f"{out_prefix}_slab{z0}_all.png"
        outputs.append(_render(img, coords[in_slab][:, :2] if n_total else [],
                               p_all, all_color, marker_size,
                               title=f"z{z0} all ({int(in_slab.sum())})"))
        if has_scores:
            sel = in_slab & is_top
            p_top = f"{out_prefix}_slab{z0}_topN.png"
            outputs.append(_render(img, coords[sel][:, :2], p_top,
                                   top_color, marker_size,
                                   title=f"z{z0} top{top_n} ({int(sel.sum())})"))

    n_shown = int(shown.sum())
    pct = (100.0 * n_shown / n_total) if n_total else 0.0
    n_scored = int(np.isfinite(scores).sum()) if has_scores else 0
    log(f"picks: {n_total} total; shown {n_shown} across {len(centers)} slabs "
        f"({pct:.0f}%); scored {n_scored}; top-N={top_n}"
        + ("" if has_scores else "; NO score column -> _topN skipped"))
    return outputs


def main():
    ap = argparse.ArgumentParser(
        description="Template-matching picks QC: tomogram z-slabs with picks overlaid.")
    ap.add_argument("--tomogram", required=True, help="tomogram MRC to project")
    ap.add_argument("--picks", required=True, help="RELION .star or PyTOM particles.xml")
    ap.add_argument("-o", "--out-prefix", required=True, help="output PNG path prefix")
    ap.add_argument("--coords-angpix", type=float, default=None,
                    help="source pixel size of pick coords, in A. Fallback when a .star "
                         "has no rlnPixelSize/rlnDetectorPixelSize column; for PyTOM .xml "
                         "it overrides the default (coords already on the tomogram grid).")
    ap.add_argument("--tomo", default=None,
                    help="filter star rows to this tomogram (rlnMicrographName)")
    ap.add_argument("--n-slabs", type=int, default=DEFAULT_N_SLABS)
    ap.add_argument("--slab-thickness", type=int, default=DEFAULT_SLAB_THICKNESS,
                    help="slab thickness in tomogram px (~1 particle)")
    ap.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    ap.add_argument("--project", choices=["mean", "min", "max"], default="mean")
    args = ap.parse_args()

    outs = overlay_picks(
        args.tomogram, args.picks, args.out_prefix,
        coords_angpix=args.coords_angpix, tomo=args.tomo,
        n_slabs=args.n_slabs, slab_thickness=args.slab_thickness,
        top_n=args.top_n, project=args.project,
        log=lambda msg: print(msg, file=sys.stderr))
    for p in outs:
        print(p)


if __name__ == "__main__":
    main()
