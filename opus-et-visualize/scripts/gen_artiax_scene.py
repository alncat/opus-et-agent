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


def reconcile_coords(df, coords_angpix, tomo_angpix):
    df = df.copy()
    ratio = coords_angpix / tomo_angpix
    for axis in ("X", "Y", "Z"):
        df[f"voxel{axis}"] = df[f"rlnCoordinate{axis}"] * ratio
        df[f"angstrom{axis}"] = df[f"rlnCoordinate{axis}"] * coords_angpix
    return df


def attach_labels(df, labels):
    if len(labels) != len(df):
        raise ValueError(f"labels ({len(labels)}) != particles ({len(df)})")
    df = df.copy()
    df["state"] = [int(x) for x in labels]
    return df


def write_relion_star(df, out_path, state=None):
    subset = df if state is None else df[df["state"] == state]
    keep = [c for c in (["rlnMicrographName"] + COORD_COLS + ANGLE_COLS)
            if c in subset.columns]
    starfile.write(subset[keep].reset_index(drop=True), out_path, overwrite=True)
    return out_path


STATE_PALETTE = ["cornflower blue", "tomato", "gold", "medium sea green",
                 "orchid", "turquoise", "sandy brown", "slate gray"]


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
    r = euler_to_matrix(row["rlnAngleRot"], row["rlnAngleTilt"], row["rlnAnglePsi"])
    t = np.array([row["voxelX"], row["voxelY"], row["voxelZ"]]).reshape(3, 1)
    return np.hstack([r, t])


def emit_cxc(tomogram, map_path, state_stars, out_cxc, state_colors=None,
             coords_angpix=1.0, contour_level=None, contour_sd=None,
             tomo_transfer=None, tomo_color="light gray", bg_color="black",
             tilt_x=-30, movie_out=None, state_maps=None, state_contours=None,
             shadows=False, silhouettes=False, still_out=None,
             movie_rock=None, movie_step=None):
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
    state_maps = state_maps or {}
    state_contours = state_contours or {}
    states = sorted(state_stars)
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
            f"artiax particles {pl} originScaleFactor {coords_angpix}",
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
