#!/usr/bin/env python3
"""Per-particle raw-density gallery: zoomed crops centered on picks, marked.

For a sparse or rare species (e.g. FAS), a whole-slab overlay
(``tm_picks_overlay.py``) shows *where* the picks are but the particles are lost
in the crowd. This tool zooms in: one small crop per pick, taken at the pick's
own Z plane, with a marker drawn so the **raw reconstruction inside the marker
stays visible** — the point is to reveal the density the picks land on, not to
hide it under an idealized surface (that is the ArtiaX finale's job).

Three marker styles trade marker-visibility against density-visibility:
  ``ring``        open circle; interior untouched (best for "reveal the density")
  ``transparent`` tinted disc; density shows through, marker easier to spot
  ``solid``       opaque disc; marker unmissable but hides the particle under it

Coordinates are read and reconciled to the tomogram grid by the same code path
as ``tm_picks_overlay`` (RELION ``.star`` via Angstroms, or PyTOM ``.xml`` already
on the grid). Pure-numpy core; matplotlib and mrcfile are imported lazily so the
selection/crop logic tests without them.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

# reuse the picks reader + coordinate reconciliation (same scripts/ dir)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tm_picks_overlay import read_picks, to_tomo_pixels  # noqa: E402

DEFAULT_HALF = 17          # crop half-window in tomo px (~a particle + margin)
DEFAULT_ZTHICK = 1         # planes averaged around the pick's Z (1 = single slice)
DEFAULT_N = 24
DEFAULT_COLS = 6
MARKER_STYLES = ("ring", "transparent", "solid")


# --------------------------------------------------------------------------- #
# selection + cropping (pure)
# --------------------------------------------------------------------------- #

def in_bounds(coord, half, vol_shape, zthick=1):
    """True when a ``2*half`` XY window and a ``zthick`` Z band around ``coord``
    (x, y, z, in tomogram px) both fit fully inside a ``(nz, ny, nx)`` volume."""
    nz, ny, nx = vol_shape
    x, y, z = float(coord[0]), float(coord[1]), float(coord[2])
    hz = int(zthick) // 2
    zi = int(round(z))
    return (half <= x <= nx - half and half <= y <= ny - half
            and zi - hz >= 0 and zi + (int(zthick) - 1 - hz) <= nz - 1)


def select_gallery_picks(coords_tomo, half, vol_shape, n=DEFAULT_N, zthick=1):
    """Indices of up to ``n`` picks whose full crop is in-bounds, spread evenly
    across the (order-preserving) in-bounds subset so the gallery samples the
    whole list rather than the first ``n`` picks."""
    coords = np.asarray(coords_tomo, float).reshape(-1, 3)
    ok = [i for i, c in enumerate(coords) if in_bounds(c, half, vol_shape, zthick)]
    if not ok:
        return np.array([], int)
    take = min(int(n), len(ok))
    sel = np.linspace(0, len(ok) - 1, take).round().astype(int)
    return np.array([ok[j] for j in dict.fromkeys(sel)], int)   # unique, in order


def crop_at_pick(vol, coord, half, zthick=1):
    """``(2*half, 2*half)`` crop centered on ``coord`` (x, y, z in tomo px) at its Z
    plane; ``zthick>1`` mean-projects that many planes. Caller must ensure in-bounds."""
    vol = np.asarray(vol)
    x, y, z = int(round(coord[0])), int(round(coord[1])), int(round(coord[2]))
    hz = int(zthick) // 2
    sub = vol[z - hz:z - hz + int(zthick), y - half:y + half, x - half:x + half]
    plane = sub.mean(axis=0) if int(zthick) > 1 else vol[z, y - half:y + half, x - half:x + half]
    return np.asarray(plane, dtype=np.float32)


# --------------------------------------------------------------------------- #
# rendering (lazy matplotlib)
# --------------------------------------------------------------------------- #

def write_bild(coords_tomo, tomo_angpix, out_path, radius=70.0, color="gold"):
    """Write ChimeraX BILD marker spheres at ``coords_tomo`` (x, y, z in tomogram px),
    placed in the tomogram's Angstrom frame (px * voxel size). Feeds the plain-ChimeraX
    *scannable* reveal: open the tomogram as an image plane + these markers, then scan
    ``volume planes z`` through Z so each barrel appears under its marker (see SKILL.md).
    ArtiaX's own orthoslice cannot be scanned by command, hence this route. Pure text."""
    coords = np.asarray(coords_tomo, float).reshape(-1, 3) * float(tomo_angpix)
    lines = [f".color {color}"]
    lines += [f".sphere {x:.2f} {y:.2f} {z:.2f} {float(radius):.1f}" for x, y, z in coords]
    Path(out_path).write_text("\n".join(lines) + "\n")
    return out_path


def _marker_kw(style, color):
    if style == "ring":
        return dict(facecolor="none", edgecolor=color, linewidth=2.2)
    if style == "transparent":
        return dict(facecolor=color, edgecolor="none", alpha=0.40)
    if style == "solid":
        return dict(facecolor=color, edgecolor="none", alpha=1.0)
    raise ValueError(f"unknown marker style {style!r}; choose from {MARKER_STYLES}")


def render_gallery(crops, out_path, half, marker_radius, style="ring",
                   color="gold", cols=DEFAULT_COLS, title=None, subtitle=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    n = len(crops)
    cols = max(1, min(int(cols), n)) if n else 1
    rows = int(np.ceil(n / cols)) if n else 1
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 1.9, rows * 1.9))
    axes = np.atleast_1d(np.asarray(axes)).reshape(-1)
    for k, ax in enumerate(axes):
        ax.set_xticks([]); ax.set_yticks([])
        if k >= n:
            ax.axis("off"); continue
        c = np.asarray(crops[k], float)
        lo, hi = np.percentile(c, [2, 98])
        if hi <= lo:
            hi = lo + 1.0
        ax.imshow(c, cmap="gray", vmin=lo, vmax=hi, origin="lower", interpolation="bicubic")
        ax.add_patch(Circle((half, half), marker_radius, **_marker_kw(style, color)))
        for s in ax.spines.values():
            s.set_edgecolor("#999"); s.set_linewidth(0.6)
    if title:
        fig.suptitle(title, fontsize=13, fontweight="bold", y=0.995)
    if subtitle:
        fig.text(0.5, 0.008, subtitle, ha="center", fontsize=9.5, color="#555")
    fig.tight_layout(rect=[0, 0.02 if subtitle else 0, 1, 0.97 if title else 1])
    fig.savefig(out_path, dpi=150, facecolor="white")
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #

def particle_gallery(tomogram, picks, out_path, coords_angpix=None, tomo=None,
                     half=DEFAULT_HALF, zthick=DEFAULT_ZTHICK, n=DEFAULT_N,
                     cols=DEFAULT_COLS, style="ring", color="gold",
                     marker_radius=None, species="particle", bild_out=None,
                     bild_radius=70.0, log=print):
    import mrcfile

    with mrcfile.open(tomogram, permissive=True) as m:
        vol = np.asarray(m.data, dtype=np.float32)
        tomo_angpix = float(m.voxel_size.x)
    if vol.ndim != 3:
        raise ValueError(f"tomogram is not 3D: shape {vol.shape}")
    if not tomo_angpix or tomo_angpix <= 0:
        log(f"WARNING: tomogram voxel_size is {tomo_angpix}; defaulting to 1.0 A/px "
            f"-- .star coordinate scaling will be wrong")
        tomo_angpix = 1.0

    coords_src, _scores, src_angpix = read_picks(
        picks, coords_angpix=coords_angpix, tomo=tomo, warn=log)
    coords = to_tomo_pixels(coords_src, src_angpix, tomo_angpix)
    n_total = len(coords)

    if bild_out is not None:   # all picks, for the 3D / scannable ChimeraX reveal
        write_bild(coords, tomo_angpix, bild_out, radius=bild_radius, color=color)
        log(f"bild: {n_total} markers ({bild_radius:.0f} A) -> {bild_out}")

    idx = select_gallery_picks(coords, half, vol.shape, n=n, zthick=zthick)
    if len(idx) == 0:
        raise ValueError(f"no picks have a full {2 * half}px crop in-bounds "
                         f"(tomogram {vol.shape}, {n_total} picks)")
    crops = [crop_at_pick(vol, coords[i], half, zthick) for i in idx]

    if marker_radius is None:
        marker_radius = round(half * 0.38, 1)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    title = (f"Raw {species} density in the tomogram — {len(idx)} of {n_total} picks, "
             f"each {style}-marked at its own Z-plane")
    sub = (f"{tomo_angpix:.2f} A/px · the marker is the pipeline's pick; the interior is "
           f"untouched raw reconstruction — the {species} each pick lands on")
    render_gallery(crops, out_path, half, marker_radius, style=style, color=color,
                   cols=cols, title=title, subtitle=sub)
    log(f"gallery: {len(idx)}/{n_total} picks, {2*half}px crops, style={style} -> {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Per-particle raw-density gallery: zoomed crops centered on picks.")
    ap.add_argument("--tomogram", required=True, help="tomogram MRC (raw reconstruction)")
    ap.add_argument("--picks", required=True, help="RELION .star or PyTOM particles.xml")
    ap.add_argument("-o", "--out", required=True, help="output PNG path")
    ap.add_argument("--coords-angpix", type=float, default=None,
                    help="source pixel size of pick coords (A); fallback when the .star "
                         "has no pixel-size column")
    ap.add_argument("--tomo", default=None, help="filter star rows to this tomogram")
    ap.add_argument("--half", type=int, default=DEFAULT_HALF,
                    help="crop half-window in tomo px")
    ap.add_argument("--zthick", type=int, default=DEFAULT_ZTHICK,
                    help="planes averaged around each pick's Z (1 = single slice)")
    ap.add_argument("-n", "--num", type=int, default=DEFAULT_N, help="max crops to show")
    ap.add_argument("--cols", type=int, default=DEFAULT_COLS)
    ap.add_argument("--style", choices=MARKER_STYLES, default="ring")
    ap.add_argument("--color", default="gold")
    ap.add_argument("--marker-radius", type=float, default=None,
                    help="marker radius in tomo px (default ~0.38*half)")
    ap.add_argument("--species", default="particle", help="label used in the title")
    ap.add_argument("--bild", default=None,
                    help="also write a ChimeraX BILD of ALL picks (3D markers for the "
                         "scannable in-cell reveal; see SKILL.md)")
    ap.add_argument("--bild-radius", type=float, default=70.0,
                    help="BILD marker sphere radius in Angstroms")
    args = ap.parse_args()

    out = particle_gallery(
        args.tomogram, args.picks, args.out, coords_angpix=args.coords_angpix,
        tomo=args.tomo, half=args.half, zthick=args.zthick, n=args.num, cols=args.cols,
        style=args.style, color=args.color, marker_radius=args.marker_radius,
        species=args.species, bild_out=args.bild, bild_radius=args.bild_radius,
        log=lambda msg: print(msg, file=sys.stderr))
    print(out)


if __name__ == "__main__":
    main()
