#!/usr/bin/env python3
"""Generate a ChimeraX/ArtiaX scene placing a refined map at every particle pose
inside its tomogram, colored by OPUS-ET conformational state (spec §7).

Scope: STAR parsing, pixel-size reconciliation, per-state RELION star output,
the RELION ZYZ pose->matrix used for single-particle validation, and .cxc text
emission. The emitted ArtiaX commands are verified against ArtiaX 0.7.0 /
ChimeraX 1.10 (see emit_cxc's docstring); renders run locally on the Mac, not on
the cluster (spec §7.1, §11).
"""
from pathlib import Path
import argparse
import colorsys
import pickle

import numpy as np
import pandas as pd
import starfile

COORD_COLS = ["rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ"]
ANGLE_COLS = ["rlnAngleRot", "rlnAngleTilt", "rlnAnglePsi"]


def read_particles(star_path):
    data = starfile.read(star_path)
    if isinstance(data, pd.DataFrame):
        return data
    for block in data.values():                 # dict of blocks
        if "rlnCoordinateX" in getattr(block, "columns", []):
            return block
    raise ValueError(f"no particle block with rlnCoordinateX in {star_path}")


def reconcile_coords(df, coords_angpix, tomo_angpix, origin_angpix=None):
    """Physical refined centres; RELION origins are subtracted from coordinates.

    Pixel-valued origins use origin_angpix (defaults to coords_angpix). Origins
    already in Angstroms must not be multiplied by a pixel size again.
    """
    df = df.copy()
    origin_angpix = coords_angpix if origin_angpix is None else origin_angpix
    for value in (coords_angpix, tomo_angpix, origin_angpix):
        if not np.isfinite(value) or value <= 0:
            raise ValueError('pixel sizes must be positive and finite')
    for axis in ("X", "Y", "Z"):
        pixel_key, angstrom_key = f'rlnOrigin{axis}', f'rlnOrigin{axis}Angst'
        shift = (df[pixel_key] * origin_angpix if pixel_key in df else
                 df[angstrom_key] if angstrom_key in df else 0.0)
        df[f"angstrom{axis}"] = df[f"rlnCoordinate{axis}"] * coords_angpix - shift
        df[f"voxel{axis}"] = df[f"angstrom{axis}"] / tomo_angpix
    return df


def attach_labels(df, labels):
    if len(labels) != len(df):
        raise ValueError(f"labels ({len(labels)}) != particles ({len(df)})")
    df = df.copy()
    df["state"] = [int(x) for x in labels]
    return df


def write_relion_star(df, out_path, state=None):
    subset = df if state is None else df[df["state"] == state]
    origins = [f'rlnOrigin{axis}{unit}' for unit in ('', 'Angst') for axis in 'XYZ']
    keep = [c for c in (["rlnMicrographName"] + COORD_COLS + ANGLE_COLS + origins)
            if c in subset.columns]
    starfile.write(subset[keep].reset_index(drop=True), out_path, overwrite=True)
    return out_path


STATE_PALETTE = ['#' + ''.join(f'{round(c * 255):02x}' for c in
                 colorsys.hsv_to_rgb((i * 0.61803398875) % 1.0, 0.65, 0.90))
                 for i in range(20)]


def euler_to_matrix(rot, tilt, psi):
    """RELION Euler_angles2matrix (intrinsic ZYZ), angles in degrees."""
    ca, sa = np.cos(np.radians(rot)), np.sin(np.radians(rot))
    cb, sb = np.cos(np.radians(tilt)), np.sin(np.radians(tilt))
    cg, sg = np.cos(np.radians(psi)), np.sin(np.radians(psi))
    return np.array([
        [ cg * cb * ca - sg * sa,  cg * cb * sa + sg * ca, -cg * sb],
        [-sg * cb * ca - cg * sa, -sg * cb * sa + cg * ca,  sg * sb],
        [ sb * ca,                 sb * sa,                 cb     ],
    ])


def particle_transform(row, tomo_angpix):
    """ChimeraX transform in Angstroms, from a reconcile_coords output row."""
    r = euler_to_matrix(row["rlnAngleRot"], row["rlnAngleTilt"], row["rlnAnglePsi"])
    t = np.array([row["voxelX"], row["voxelY"], row["voxelZ"]]).reshape(3, 1) * tomo_angpix
    return np.hstack([r, t])


def emit_cxc(tomogram, map_path, state_stars, out_cxc, state_colors=None,
             coords_angpix=None, contour_level=None, contour_sd=None,
             tomo_transfer=None, tomo_color="light gray", bg_color="black",
             tilt_x=-30, movie_out=None, state_maps=None, state_contours=None,
             shadows=False, silhouettes=False, still_out=None,
             movie_rock=None, movie_step=None, origin_angpix=None):
    """Emit a ChimeraX/ArtiaX scene placing `map_path` at every particle pose in
    each per-state RELION star, inside `tomogram`. Colored by conformational state.

    Syntax verified against **ArtiaX 0.7.0 / ChimeraX 1.10** (the M1 stub was wrong):
      * particle lists load via ``open <star> format relion`` — there is no
        ``artiax open particles`` command (it is commented out in 0.7.0; the generic
        ``artiax open`` errors telling you to use the plain ``open``).
      * ArtiaX places ``rlnCoordinate*`` at **1.0 A/px** by default, so
        ``artiax particles <pl> originScaleFactor <coords_angpix>`` is REQUIRED to
        rescale coords to physical A and register the particles with the tomogram
        (the map itself is placed at true size from its own MRC ``voxel_size`` — no
        scaling needed there).
      * RELION origins are retained. ``transScaleFactor`` is origin_angpix
        for pixel-valued shifts, or 1.0 for Angstrom-valued shifts. Specify
        origin_angpix when it differs from coords_angpix.
      * attach a map as each particle's surface with
        ``artiax attach #<map> toParticleList #<pl>`` (map must be a Volume).
      * style instances with ``color #<pl> <c>`` / ``artiax particles``.

    Model IDs are deterministic for this emission order:
      ``artiax start`` -> #1 (#1.1 Tomograms, #1.2 Particle Lists, #1.3 Geometric);
      ``artiax open tomo`` -> #1.1.1; one map copy per state -> #2, #3, ...;
      particle lists -> #1.2.1, #1.2.2, ...

    contour_level (absolute) or contour_sd (sigma) sets the map surface. Pass a
    finer/tighter cropped map for lighter memory when instancing thousands of copies.
    tomo_transfer is an optional list of (value, alpha) pairs -> a translucent
    image-style tomogram slab (a second, plain copy of the tomo). movie_out appends
    a 360 turntable record/encode to that path.

    state_maps / state_contours are optional {state: value} overrides for a
    MULTI-SPECIES scene: each particle list then gets its OWN reference map and
    contour (e.g. {0: ribo.mrc, 1: fas.mrc}) instead of the shared map_path /
    contour_level. When a state is absent from the dict it falls back to the
    shared map_path / contour_level / contour_sd.

    HERO 'molecular sociology' aesthetic (opt-in, backward-compatible defaults):
      * silhouettes=True (with tilt_x ~ -55 so the slab is edge-on) gives the 3D-cloud
        read at ANY instance count — this is what the thousands-of-instances in-cell
        finale uses. Cheap; no per-instance cost.
      * shadows=True adds ground-shadow pop but ONLY for small scenes — single-map
        spins / M-showcases or <=~2,000 instances (e.g. TS_029). Do NOT pass
        shadows=True for the thousands-of-instances in-cell finale: the shadow map
        covers every placed copy and even a single still hangs (3,387 ribosomes never
        finished). See SKILL.md's "Do NOT pass shadows=True" note.
      * still_out=<png> saves the hero still before the movie.
      * movie_rock=<deg> rocks +-deg instead of a full 360 turntable (a thin
        particle slab goes edge-on and hides in-plane features on a full turn).
      * movie_step=<n> coarsens each map for a tractable movie and drops shadows
        (per-frame shadow maps are the cost); shadows, if used at all (small scenes
        only), belong on the still not the movie.
    Defaults (all off / None) reproduce the original plain 360 turntable.
    """
    if coords_angpix is None or not np.isfinite(coords_angpix) or coords_angpix <= 0:
        raise ValueError('coords_angpix must explicitly specify the coordinate pixel size')
    origin_angpix = coords_angpix if origin_angpix is None else origin_angpix
    if not np.isfinite(origin_angpix) or origin_angpix <= 0:
        raise ValueError('origin_angpix must be positive and finite')
    state_maps = state_maps or {}
    state_contours = state_contours or {}
    states = sorted(state_stars)
    translation_scales = {}
    tomogram_names = set()
    for state in states:
        if not state_maps.get(state, map_path):
            raise ValueError(f'no map supplied for state {state}')
        particles = read_particles(state_stars[state])
        if 'rlnMicrographName' in particles:
            tomogram_names.update(particles['rlnMicrographName'].dropna().unique())
        translation_scales[state] = (origin_angpix if 'rlnOriginZ' in particles else
                                     1.0 if 'rlnOriginZAngst' in particles else origin_angpix)
    if len(tomogram_names) > 1:
        raise ValueError('filter state STARs to one tomogram before emitting a scene')
    lines = ["# Auto-generated by gen_artiax_scene.py (opus-et-visualize) — ArtiaX 0.7.0",
             "artiax start"]
    if tomogram:
        lines.append(f"artiax open tomo {tomogram}")
    for state in states:                                # one map copy per state -> #2, #3, ...
        lines.append(f"open {state_maps.get(state, map_path)}")
    for i, state in enumerate(states):
        mid = i + 2
        level = state_contours.get(state, contour_level)
        if level is not None:
            lines.append(f"volume #{mid} level {level}")
        elif contour_sd is not None:
            lines.append(f"volume #{mid} sdLevel {contour_sd}")
    for state in states:                                # particle lists -> #1.2.1, #1.2.2, ...
        lines.append(f"open {state_stars[state]} format relion")
    for i, state in enumerate(states):
        mid, pl = i + 2, f"#1.2.{i + 1}"
        color = (state_colors or {}).get(state, STATE_PALETTE[state % len(STATE_PALETTE)])
        lines += [
            f"# --- conformational state {state} ---",
            f"artiax particles {pl} originScaleFactor {coords_angpix} transScaleFactor {translation_scales[state]}",
            f"artiax attach #{mid} toParticleList {pl}",
            f"artiax show {pl} surface",
            f"hide {pl}.3 models",                       # hide the auto marker set
            f"color {pl} {color}",
        ]
    if tomogram and tomo_transfer:                       # translucent tomo slab (plain copy)
        tmid = len(states) + 2
        transfer = " ".join(f"level {v},{a}" for v, a in tomo_transfer)
        lines += [
            f"open {tomogram}",
            "hide #1.1.1 models",                        # hide the ArtiaX orthoslice
            f"volume #{tmid} style image",
            f"volume #{tmid} color {tomo_color}",
            f"volume #{tmid} {transfer}",
        ]
    lines += [
        "lighting soft",
        "lighting depthCue true depthCueStart 0.5 depthCueEnd 1.0",
    ]
    if shadows:                              # ground the molecules -> 3D pop (still)
        lines.append("lighting shadows true intensity 0.7")
    if silhouettes:                          # crisp outlines; clean once the slab is tilted
        lines.append("graphics silhouettes true width 1.2")
    lines += [
        f"set bgColor {bg_color}",
        "view",
        f"turn x {tilt_x}",
    ]
    if still_out:                            # hero still (with shadows) before the movie
        lines.append(f"save {still_out} width 1800 height 1300 supersample 3")
    if movie_out:
        if movie_step:                       # coarsen surfaces + drop per-frame shadow maps
            for i in range(len(states)):
                lines.append(f"volume #{i + 2} step {movie_step}")
            if shadows:
                lines.append("lighting shadows false")
        if movie_rock:                       # rock +-N deg (a thin slab goes edge-on on a 360)
            d = int(movie_rock)
            lines += [
                "movie record size 1300,1000 supersample 1",
                f"turn y 1.0 {d}", f"wait {d}",
                f"turn y -1.0 {2 * d}", f"wait {2 * d}",
                f"turn y 1.0 {d}", f"wait {d}",
                f"movie encode {movie_out} framerate 24 quality medium",
            ]
        else:                                # default: 360 turntable (unchanged)
            lines += [
                "movie record size 1500,1000 supersample 1",
                "turn y 2 180",
                "wait 180",
                f"movie encode {movie_out} framerate 30 quality high",
            ]
    text = "\n".join(lines) + "\n"
    Path(out_cxc).write_text(text)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Emit a ChimeraX/ArtiaX .cxc scene placing a refined map at every particle pose."
    )
    ap.add_argument("--star", required=True, help="Input RELION particle STAR file")
    ap.add_argument("--tomogram", required=True, help="Tomogram MRC path")
    ap.add_argument("--map", dest="map_path", required=True, help="Refined reference map MRC path")
    ap.add_argument("--coords-angpix", type=float, required=True,
                    help="Pixel size (A/px) of STAR rlnCoordinateX/Y/Z")
    ap.add_argument("--tomo-angpix", type=float, default=None,
                    help="Pixel size (A/px) of tomogram (defaults to --coords-angpix)")
    ap.add_argument("--origin-angpix", type=float, default=None,
                    help="Pixel size (A/px) of pixel-valued RELION origin shifts (defaults to --coords-angpix)")
    ap.add_argument("--out-cxc", required=True, help="Output ChimeraX .cxc script path")
    ap.add_argument("--stars-dir", default=None,
                    help="Directory for per-state RELION STAR files (defaults to <scene>_stars beside the .cxc)")
    ap.add_argument("--labels", default=None,
                    help="Optional OPUS-ET k-means labels.pkl (attached before tomogram filtering)")
    ap.add_argument("--tomo-name", default=None,
                    help="Filter rlnMicrographName to one tomogram (applied after --labels)")
    ap.add_argument("--contour-level", type=float, default=None, help="Absolute map surface level")
    ap.add_argument("--contour-sd", type=float, default=None, help="Map surface level in SD units")
    ap.add_argument("--bg-color", default="black", help="Background color (default: black)")
    ap.add_argument("--tilt-x", type=float, default=-30.0, help="Initial X tilt in degrees (default: -30)")
    ap.add_argument("--silhouettes", action="store_true", help="Enable crisp silhouettes (recommended for hero scenes)")
    ap.add_argument("--shadows", action="store_true", help="Enable ground shadows (small scenes only)")
    ap.add_argument("--still-out", default=None, help="Optional PNG path to save a still render")
    ap.add_argument("--movie-out", default=None, help="Optional MP4 path to record a movie")
    ap.add_argument("--movie-rock", type=int, default=None, help="Rock +-N degrees instead of 360 turntable")
    ap.add_argument("--movie-step", type=int, default=None, help="Coarsen surface step during movie recording")
    args = ap.parse_args(argv)

    tomo_angpix = args.coords_angpix if args.tomo_angpix is None else args.tomo_angpix
    df = reconcile_coords(
        read_particles(args.star),
        coords_angpix=args.coords_angpix,
        tomo_angpix=tomo_angpix,
        origin_angpix=args.origin_angpix,
    )
    if args.labels:
        with open(args.labels, "rb") as fh:
            labels = pickle.load(fh)
        df = attach_labels(df, labels)
    else:
        df["state"] = 0

    if args.tomo_name:
        if "rlnMicrographName" not in df.columns:
            raise SystemExit("--tomo-name requested, but STAR has no rlnMicrographName column")
        df = df[df["rlnMicrographName"] == args.tomo_name]
        if df.empty:
            raise SystemExit(f"no particles found for tomogram {args.tomo_name!r}")

    out_cxc = Path(args.out_cxc)
    stars_dir = Path(args.stars_dir) if args.stars_dir else out_cxc.with_name(out_cxc.stem + "_stars")
    out_cxc.parent.mkdir(parents=True, exist_ok=True)
    stars_dir = stars_dir.resolve()
    stars_dir.mkdir(parents=True, exist_ok=True)
    state_stars = {
        s: str(write_relion_star(df, stars_dir / f"state{s}.star", state=s))
        for s in sorted(set(df["state"]))
    }
    emit_cxc(
        tomogram=args.tomogram,
        map_path=args.map_path,
        state_stars=state_stars,
        out_cxc=out_cxc,
        coords_angpix=args.coords_angpix,
        origin_angpix=args.origin_angpix,
        contour_level=args.contour_level,
        contour_sd=args.contour_sd,
        bg_color=args.bg_color,
        tilt_x=args.tilt_x,
        silhouettes=args.silhouettes,
        shadows=args.shadows,
        still_out=args.still_out,
        movie_out=args.movie_out,
        movie_rock=args.movie_rock,
        movie_step=args.movie_step,
    )
    print(f"wrote {out_cxc} ({len(df)} particles, {len(state_stars)} state(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
