---
name: opus-et-visualize
description: Generate in-cell molecular visualizations for cryo-ET results. Two modes — (1) place a refined/averaged map at every particle pose inside its original tomogram in ChimeraX/ArtiaX, colored by OPUS-ET conformational state (the finale look); (2) REVEAL the raw density instead of replacing it — mark picks on the raw tomogram (per-particle zoomed gallery via particle_gallery.py, or slab overlays via tm_picks_overlay.py) with ring/transparent/solid markers, and scan the slice through Z. Use when the user wants molecules in cellular context, a hero in-cell render, or to show/validate that picks land on real raw density.
---

# OPUS-ET Visualize (in-cell scenes)

Places the refined map at every particle pose inside the tomogram, colored by
conformational state, via ChimeraX + ArtiaX.

## Agent Rules — read before acting
- Read before editing; verify command behavior before asserting.
- Reconcile pixel sizes explicitly — never assume star coords and tomogram share
  a pixel size.
- **Fair coloring.** When showing several candidate volumes/states side by side (e.g. a
  k-means state gallery), color each **distinctly** (or all neutrally) — never highlight a
  subset (e.g. "the selected ones in blue"), which pre-biases the viewer before the evidence
  is in. Use one consistent palette across the gallery and the latent UMAP so a state is the
  same color in both. Distinct 20-colors: golden-angle hue spacing gives good neighbour
  contrast (`h=(i*0.618)%1`, s≈0.68, v≈0.88).
- **ChimeraX `tile` resets lighting** — run `lighting soft` (and any `lighting` command)
  AFTER `tile #* columns N`, or it won't apply.
- **Judge Gate-3 state resolution in 3D at a HIGH percentile contour, not by CC.** Render the
  k-means state gallery with `gen_gallery_cxc.py --percentile 98` (per-map 98th-percentile
  contour). ChimeraX's default/auto contour sits too low and renders high-res detail as
  low-density "speckle" that reads as junk. Two traps invert the ranking: (a) a sharp
  high-res map correlates *less* with the blurry averaged consensus / low-res template, so
  `compare_to_template.py`'s CC ranking pushes the BEST states to the BOTTOM — treat low
  consensus-CC as a POSSIBLE high-res signal, not junk, and cross-check in 3D; (b) a cleanly
  separated, abundant latent-UMAP island is often the best-aligned/sharpest population, not an
  artifact. (Real case: ribo `z8_expanded` k17/18/19 were the high-res ribosomes but ranked
  last by CC and looked grainy at auto-contour.)

## Inputs
- WARP/RELION star: `rlnCoordinateX/Y/Z`, `rlnAngleRot/Tilt/Psi`, `rlnMicrographName`.
- Tomogram MRC (cell context) and its pixel size (`--tomo-angpix` or MRC header).
- Refined/averaged map MRC.
- Optional per-particle `labels.pkl` from `opus-et-analysis` k-means (color by state).
- `--coords-angpix`: pixel size of the star coordinates.

## Convention landmine — DEFUSE FIRST (spec §7.1)
ArtiaX vs RELION angle + pixel-size conventions are the top risk. Before rendering
thousands, validate ONE particle:
1. `particle_transform(row, tomo_angpix)` gives `[R|t]` for particle N.
2. Load the map into ChimeraX and place it with that transform; compare against a
   manual `fitmap` of the map into the tomogram at that particle.
3. Only once one particle lands correctly, render the full scene.
`euler_to_matrix` uses the RELION ZYZ `Euler_angles2matrix` convention.

## ArtiaX 0.7.0 scene recipe (verified locally on the Mac)
`emit_cxc` was rewritten against real ArtiaX 0.7.0 behavior (5 tests cover the
pure-Python `.cxc` generation, and run anywhere via the repo `.venv`). Old
M1-stub commands like `artiax open particles` and `artiax attach ... geomodel`
**do not exist in 0.7.0** — do not use them.

- **Renders run LOCALLY on the Mac, not on the cluster.** ChimeraX 1.10 +
  ArtiaX 0.7.0 at `/Applications/ChimeraX-1.10.app/Contents/bin/ChimeraX`. Pull
  the maps/poses (`.mrc`, `sel_*.star`) down from the cluster first, then render.
- **GUI required.** ArtiaX's commands only register once its GUI is up. Run
  scenes as `ChimeraX --exit scene.cxc` — **not** `--nogui`/`--offscreen`.
- **Deterministic model IDs** after `artiax start` -> `#1` (`#1.1` Tomograms,
  `#1.2` Particle Lists, `#1.3` Geometric Models):
  `artiax open tomo <mrc>` -> `#1.1.1`; each plain `open <map.mrc>` -> `#2`,
  `#3`, ...; each `open <star> format relion` -> `#1.2.1`, `#1.2.2`, ....
- **Particle lists load via plain `open <star> format relion`** — there is no
  `artiax open particles` in 0.7.0 (generic `artiax open` errors telling you to
  use plain `open`). Two star readers are registered ('RELION STAR file',
  nickname `relion`; 'RELION5 STAR file', nickname `relion5`) — you must pass
  `format relion` for old RELION-3.x single-`data_`-block stars or it errors
  "Multiple formats ... support .star suffix".
- **Coordinate scale (critical).** ArtiaX places `rlnCoordinate*` at 1.0 A/px
  by default. Rescale with
  `artiax particles #<pl> originScaleFactor <coords_angpix>` (e.g. `4.2`) so
  particles register with the tomogram. Symptom if omitted: the particle cloud
  is ~`coords_angpix`x too small vs the tomogram. The **map** needs no
  scaling — its size comes from its MRC header voxel size.
- **Attach + style:** `artiax attach #<map> toParticleList #<pl>` (map model
  must be a Volume); `artiax show #<pl> surface`; `hide #<pl>.3 models` (hide
  auto markers); `color #<pl> <color>`.
- **Contour:** set the map surface to an **absolute** level (mean + N·sigma)
  *before* attach so the contour is identical across all instances. Crop the
  map to a tight box (strip solvent padding) to cut memory when instancing
  thousands of copies. `emit_cxc` exposes this via `contour_level`/`contour_sd`.
- **Optional translucent tomogram slab** for cell context: open a plain second
  copy of the tomogram, `volume #N style image`, a faint *symmetric* transfer
  function (cryo-ET density is ~0-mean, e.g.
  `volume #N level -0.008,0.7 level -0.002,0.1 level 0.002,0.1 level 0.008,0.7`),
  `color light gray`, and hide the ArtiaX orthoslice (`hide #1.1.1 models`).
  `emit_cxc`'s `tomo_transfer` param controls the level string.
- `emit_cxc` params: `coords_angpix`, `contour_level`/`contour_sd`,
  `tomo_transfer`, `movie_out`.
- **Judgment note (cross-ref Gate-3 above):** the sharpest/high-res state map
  reads as fragmented "speckle" at a tight contour while blurry low-res maps
  look deceptively clean — don't let that bias which state you pick.

## Usage
**M1: `scripts/gen_artiax_scene.py` is a library, not a CLI (no `__main__`).**
The conductor (or you) imports it and calls its functions directly — there is
no `python scripts/gen_artiax_scene.py ...` invocation yet.

```python
import pickle
import gen_artiax_scene as gs

labels = pickle.load(open("analyze.39/kmeans12/labels.pkl", "rb"))
df = gs.attach_labels(
    gs.reconcile_coords(gs.read_particles("picks.star"),
                         coords_angpix=A, tomo_angpix=A),
    labels,
)
stars = {s: gs.write_relion_star(df, f"state{s}.star", state=s)
         for s in sorted(set(df["state"]))}
gs.emit_cxc(tomogram="TS_026.mrc", map_path="ref.mrc",
            state_stars=stars, out_cxc="scene.cxc")
```
Produces one RELION star per state plus `scene.cxc`.

**M2 target — not yet wired.** The plan calls for a real `main()`/argparse CLI
wrapping the same calls (see the M1 plan's "Notes for M2–M5"):
```bash
python scripts/gen_artiax_scene.py \
    --star picks.star --tomogram TS_026.mrc --map ref.mrc \
    --labels analyze.39/kmeans12/labels.pkl \
    --coords-angpix <A> --tomo-angpix <A> \
    --out-cxc scene.cxc --out-star-prefix state
```

Open the resulting scene in ChimeraX:
`ChimeraX --exit scene.cxc` (GUI required — ArtiaX commands don't register
under `--nogui`/`--offscreen`; see the ArtiaX 0.7.0 recipe above).

## Raw-density marker reveals — show the density, don't replace it
The finale (above) places the *refined* map at each pose — idealized, and it
hides the raw tomogram. To instead **reveal the raw reconstruction** with the
picks only *marked* (validation, or a rare-species "here it really is" shot):

- **Per-particle zoomed gallery — `scripts/particle_gallery.py`** (20 tests).
  One small crop per pick, taken at the pick's own Z-plane, marker drawn so the
  interior stays visible. Reuses `tm_picks_overlay`'s pick reader + Å coordinate
  reconciliation. Best for a sparse species where a whole-slab overlay loses it.
  ```bash
  python scripts/particle_gallery.py --tomogram TS_028_bin2.mrc \
      --picks sel_fas30.star --tomo TS_028 --coords-angpix 4.2 \
      --style ring --num 24 --half 17 --species FAS -o fas_raw_gallery.png \
      --bild fas_markers.bild        # optional: 3D markers for the scan below
  ```
- **Whole-slab context** is already `tm_picks_overlay.py` (mean-projected slabs,
  ring markers, all/top-N). Use it for "where in the cell", the gallery for "what
  the density looks like".
- **Marker style is the reveal/spot trade-off** (`--style`): `ring` (open circle,
  interior untouched — best for revealing density), `transparent` (tinted disc,
  density shows through), `solid` (opaque — unmissable but hides the particle).
  For a moving Z-scan, `solid` reads best (a thin ring flickers against the grain).
- **Scannable Z-scan reveal (plain ChimeraX, not ArtiaX).** ArtiaX's orthoslice
  can't be driven by command, so scan a plain `volume` image-plane with the picks
  as fixed BILD markers (`--bild` above); each barrel appears under its marker as
  the plane crosses its Z:
  ```
  open TS_028_bin2.mrc ; open fas_markers.bild
  volume #1 style image ; volume #1 color white
  volume #1 level -0.016,0 level 0.016,1 ; volume #1 planes z,62
  set bgColor black ; camera ortho ; view orient
  movie record ; perframe "volume #1 planes z,$1" range 62,176 frames 90 ; wait 90
  perframe stop ; movie encode fas_scan.mp4 framerate 24 quality medium
  ```

## Molecular-sociology / cellular-context render (hero aesthetic) — in `emit_cxc`
This IS `gen_artiax_scene.emit_cxc` — placing each species' refined map at every
pose in the tomogram. The **hero look** (molecules as a 3D cloud spilling past a
floating slab, not a flat decorated micrograph) is opt-in on the same call:
```python
emit_cxc(tomogram="TS_028_bin4.mrc", map_path=None,
         state_stars={0: "ribo_TS028.star", 1: "fas_TS028.star"},
         state_maps={0: "ribo.mrc", 1: "fas.mrc"},
         state_contours={0: 0.010, 1: 0.0072},
         state_colors={0: "cornflower blue", 1: "gold"},
         coords_angpix=4.2, tilt_x=-55,          # tilt so the slab is edge-on
         silhouettes=True,                        # 3D pop; safe at any instance count
         still_out="finale_still.png",
         movie_out="finale.mp4", movie_rock=30,   # rock +-30, NOT a 360 (thin slab)
         movie_step=3, out_cxc="finale.cxc")
```
`silhouettes` needs the tilt (a face-on view flattens it and silhouettes outline
only near-plane particles). **Do NOT pass `shadows=True` for a thousands-of-
instances scene** — the shadow map covers every placed copy and even a single
still hangs (learned the hard way at 3,387 ribosomes). Shadows are fine for the
single-map showcases (M-spins) and small scenes (≲2,000 instances, e.g. TS_029);
the big in-cell finale uses silhouettes + depth-cue only. `movie_step` coarsens
surfaces for a tractable movie (use 3, not 2, for thousands of instances);
`movie_rock` avoids the edge-on midpoint of a 360.
Defaults (all off) reproduce the plain turntable. Pick the tomogram by
cross-referencing organelle content (clean `*_13.48Apx.png` central slices)
against per-tomogram particle counts (`grep -oE "TS_0[0-9]+" sel.star | sort |
uniq -c`, ÷5 for the star's 5 mentions/row). Rotating single-map showcases (the M
result) are a plain-ChimeraX `open map ; volume level <mean+4sd> ; surface dust
#1 size 120 ; lighting shadows/silhouettes ; turn y 3 120` turntable. See
`demo/render_commands.md` §B/§B3, `demo/finale/build_ts029_cell_scene.py`.

## ChimeraX/ArtiaX gotchas (learned the hard way)
- **Headless `--offscreen`/`--nogui` rendering often fails.** ChimeraX offscreen
  rendering needs a working OSMesa / virtual-display GL context that many machines
  (especially cluster nodes) lack, so the render comes back blank or errors. GUI mode
  is the reliable path for **all** renders, not just ArtiaX — if a headless render
  fails, run it in a local GUI session. (ArtiaX is GUI-only regardless; its commands
  don't even register without the GUI.)
- **`view` does NOT reset orientation** — it re-fits zoom but keeps the current
  rotation. `view; turn x -38` after a prior `turn x -55` silently stacks to −93°
  (edge-on). Re-issue the full setup, or `view orient`, between angles.
- **Shadows scale badly with ArtiaX instance count.** `lighting shadows` on
  thousands of placed copies makes even a single still hang (the shadow map
  covers every instance) — a 3,387-ribosome still never finished. Use silhouettes
  only for the big in-cell scene; keep shadows for single-map showcases and
  scenes ≲2,000 instances. For a thousands-of-instances movie also use `volume …
  step 3` (not 2) so it stays ~2 min, not ~6.
- **`volume … planes z,N` breaks the ArtiaX orthoslice** (goes black) — it only
  works on a *plain* (non-ArtiaX) volume. That's why the Z-scan uses plain
  ChimeraX + BILD markers rather than ArtiaX.
- **`turn y` 360° on a thin slab goes edge-on mid-spin** and hides in-plane
  features (an organelle arc). For a slab, rock ±~30° instead of a full turntable.
- **Silhouettes at a face-on view only outline near-plane particles** (the opaque
  slice occludes the rest) → an uneven, messy subset. They render cleanly once the
  slab is tilted edge-on so molecules float clear of it.
- **Python split on the Mac render box:** the ChimeraX-bundled python has
  matplotlib but NOT mrcfile; the repo `.venv` (3.14) has both. Extract crops with
  a python that has mrcfile, render figures with either. Verify an mp4 without
  ffmpeg via `qlmanage -t -s 1100 -o <dir> movie.mp4` (QuickLook thumbnail).

## Status
M1: scene generation + single-particle validation. ArtiaX command syntax is
verified against ArtiaX 0.7.0 / ChimeraX 1.10 locally on the Mac (see recipe
above); the cluster is only the source of the .mrc/.star inputs, not the
render/verification host. Added this
session: `particle_gallery.py` (raw-density marker galleries + BILD markers, 20
tests), the cellular-context render aesthetic, and the scannable Z-scan reveal.

## Files in this skill
```
scripts/
  gen_artiax_scene.py    # in-cell finale — emit_cxc() places a refined map at every pose
                         #   (library; driven by demo/finale/build_insitu_scene.py)
  particle_gallery.py    # CLI — per-particle zoomed raw-density gallery + BILD 3D markers (reveal mode)
  tm_picks_overlay.py    # CLI — pick markers on mean-projected raw slabs (whole-slab context)
  slice_preview.py       # CLI — central-slice tomogram previews (Gate-1 alignment QC)
tests/                   # pytest — test_scene_pose + test_scene_coords (gen_artiax_scene),
                         #   test_particle_gallery, test_tm_picks_overlay, test_slice_preview
```
No `references/` — the ArtiaX recipe + gotchas are inline above.
