---
name: opus-et-visualize
description: "Use when the user wants molecules shown in their cellular context: a refined map placed at every particle pose inside the original tomogram in ChimeraX/ArtiaX, coloured by conformational state or species, a hero in-cell render or movie, or picks marked on the raw tomogram density to check they land on real particles rather than ice or carbon. ChimeraX/ArtiaX runs on the local desktop, not the cluster."
---

# OPUS-ET Visualize (in-cell scenes)

Places the refined map at every particle pose inside the tomogram, colored by
conformational state, via ChimeraX + ArtiaX.

## Visualization Rules
> Shared agent rules live in [AGENTS.md](../opus-et-conductor/references/AGENTS.md).
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
  k-means state gallery with [gen_gallery_cxc.py](./scripts/gen_gallery_cxc.py) (`python ./scripts/gen_gallery_cxc.py --maps-dir <maps> --out gallery.cxc --percentile 98`, per-map 98th-percentile
  contour). A low contour can include solvent speckle; an excessively high contour can fragment real density. Inspect several matched contours before judging a state. Two traps invert the ranking: (a) a sharp
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
1. `particle_transform(row, tomo_angpix)` gives `[R|t]` in Å for particle N after `reconcile_coords` applies RELION origin shifts.
2. Load the map into ChimeraX and place it with that transform; compare against a
   manual `fitmap` of the map into the tomogram at that particle.
3. Only once one particle lands correctly, render the full scene.
`euler_to_matrix` uses the RELION ZYZ `Euler_angles2matrix` convention.

## ArtiaX 0.7.0 scene recipe (verified locally on the Mac)
The scene recipe implemented by `emit_cxc()`: open tomogram, import particle list, attach map, colour by state; verified against ArtiaX 0.7.0 / ChimeraX 1.10. Full text: [artiax.md](./references/artiax.md).

## Usage
Run [gen_artiax_scene.py](./scripts/gen_artiax_scene.py) directly as a CLI (or import its functions for custom multi-species scenes):

```bash
python ./scripts/gen_artiax_scene.py \
    --star picks.star --tomogram TS_026.mrc --map ref.mrc \
    --coords-angpix <A> --tomo-angpix <A> \
    --labels analyze.39/kmeans12/labels.pkl \
    --tomo-name TS_026.tomostar --out-cxc scene.cxc
```
Produces one RELION star per state (`scene_stars/state<N>.star` for this example) plus `scene.cxc` (attaching `--labels` to the full STAR before filtering `--tomo-name`).

Open the resulting scene in ChimeraX:
`ChimeraX --exit scene.cxc` (GUI required — ArtiaX commands don't register
under `--nogui`/`--offscreen`; see the ArtiaX 0.7.0 recipe above).

## Raw-density marker reveals — show the density, don't replace it
Mark picks on the raw tomogram instead of replacing it: per-particle zoomed galleries ([particle_gallery.py](./scripts/particle_gallery.py)), slab overlays ([tm_picks_overlay.py](./scripts/tm_picks_overlay.py)), ring/transparent/solid markers, Z-scan. Full text: [artiax.md](./references/artiax.md).

## Molecular-sociology / cellular-context render (hero aesthetic) — in `emit_cxc`
The hero in-cell look: lighting, silhouettes, per-species colour, camera. Full text: [artiax.md](./references/artiax.md).

## ChimeraX/ArtiaX gotchas (learned the hard way)
Command-syntax and coordinate traps that cost real time; read before debugging a scene that renders wrong. Full text: [artiax.md](./references/artiax.md).

## Files in this skill
```
scripts/
  gen_artiax_scene.py    # CLI + library — in-cell finale placing a refined map at every pose
  gen_gallery_cxc.py     # CLI + library — fair-colored 3D k-means state gallery .cxc (Gate-3)
  particle_gallery.py    # CLI — per-particle zoomed raw-density gallery + BILD 3D markers (reveal mode)
  slice_picks.py         # library — write_slice_picks_star() exports native XY slice picks to a full-volume STAR
  tm_picks_overlay.py    # CLI — pick markers on mean-projected raw slabs (whole-slab context)
  slice_preview.py       # CLI — central-slice tomogram previews (Gate-1 alignment QC)
tests/                   # pytest — test_scene_pose + test_scene_coords (gen_artiax_scene),
                         #   test_gen_gallery_cxc, test_particle_gallery, test_slice_picks,
                         #   test_tm_picks_overlay, test_slice_preview
references/
  artiax.md              # scene recipe, raw-density reveals, hero render, ChimeraX/ArtiaX gotchas
```
