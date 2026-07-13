# Render Commands — non-terminal captures for the demo

Two shots in the video are **ChimeraX renders, not Claude Code screen-recordings**: the
**3D state-gallery spin** (Scene 4 b-roll) and the **in-cell ArtiaX finale** (Scene 5). Render
each to a still + short movie, then cut them into the edit. Both read outputs from the **58k
expanded run** — regenerate after the run settles.

**Render in GUI mode — it's the only reliable path.** The **state-gallery spin** (§A) and the
plain-ChimeraX **map showcases** (§B2) are plain ChimeraX and render simplest in a local GUI
session. ChimeraX's headless `--offscreen`/`--nogui` **often does not work** (it needs a working
OSMesa / virtual-display GL context, which many machines — including cluster nodes — lack, so
renders come out blank or error); treat it as a last resort, verify the output, and fall back to a
GUI session when it fails. The **ArtiaX scenes** (§B in-cell finale, §B3) are GUI-**only**, locally
on the Mac (ChimeraX 1.10 + ArtiaX 0.7.0 at `/Applications`) — never `--nogui`/`--offscreen`;
ArtiaX's commands don't even register without the GUI up.

---

## A. 3D state-gallery spin  (Scene 4 b-roll)
All k-means state maps, tiled, fair-colored by golden-angle hue, soft-lit, spinning.

```bash
python demo/gen_gallery_cxc.py \
    --maps-dir opuset/ribo/z8_expanded/analyze.35/kmeans20 \
    --pattern '*.mrc' --columns 5 \
    --still ribo_state_gallery_3d.png \
    --movie ribo_state_gallery_spin.mp4 \
    --out gallery.cxc
chimerax gallery.cxc          # GUI mode (reliable); --offscreen headless often fails (OSMesa/GL)
```
- Coloring/tiling/lighting are handled by the generator (golden-angle `h=(i*0.618)%1`;
  `lighting soft` emitted **after** `tile`, since `tile` resets lighting).
- If the auto contour looks uneven across maps, pass a common level, e.g. `--level 1.0`, and
  re-render. Tune once; the maps share a normalization from `eval_vol`.
- The **still** (`ribo_state_gallery_3d.png`) is also the README/Gate-3 gallery asset — this is
  the same render, so generating it here refreshes both the video b-roll and the doc figure.

---

## B. In-cell ArtiaX finale — molecular sociology  (Scene 5)  ✅ rendered
**Both species** — the joint-M-refined ribosome *and* FAS maps — placed at every TS_028 pose
inside the tomogram, colored by species. Rendered **locally on the Mac in ChimeraX 1.10 +
ArtiaX 0.7.0** (`demo/qc/finale/`), *not* on the cluster. `emit_cxc` takes `state_stars` as a
dict `{state: star}` and supports a multi-species scene via `state_maps` / `state_contours`
(each particle list gets its own map + contour).

Sync from the cluster: `m/species/{ribo_11ea1073,fas_89b38e4c}/{ribo,fas}_filtsharp.mrc`
(vox 3.37 Å), `warp_tiltseries/reconstruction/TS_028_bin4.mrc`,
`opuset/ribo/z8_expanded/sel_ribo_TS028.star` (3,387), `opuset/fas/z8/sel_fas30.star`
(filter to TS_028 → 95). Then:

The canonical driver is **[`demo/finale/build_insitu_scene.py`](finale/build_insitu_scene.py)** —
it crops the current refined maps and calls `emit_cxc` with the proven hero-aesthetic opt-ins
(gentle rock, silhouettes, coarsened surfaces, **no shadows**):
```python
import sys; sys.path.insert(0, "opus-et-visualize/scripts")
from gen_artiax_scene import emit_cxc
emit_cxc(
    tomogram="TS_028_bin4.mrc", map_path=None,
    state_stars={0: "ribo_TS028.star", 1: "fas_TS028.star"},
    state_maps={0: "ribo_refined.mrc", 1: "fas_refined.mrc"},   # crop to ~128^3 first (lighter instancing)
    state_contours={0: 0.010, 1: 0.0072},                       # ~mean+4·sd per CURRENT map
    state_colors={0: "cornflower blue", 1: "gold"},
    coords_angpix=4.2,          # sel-star coords are at the 4.2 Å subtomo pixel; maps place at their own 3.37 Å
    tomo_transfer=None,         # ArtiaX's OWN orthoslice gives the tomo/cell context — do NOT add a slab
    tilt_x=-55, bg_color="black",
    silhouettes=True, still_out="finale_insitu_still.png",      # hero still (silhouettes, no shadows)
    movie_rock=30, movie_step=3,                                # rock ±30°, coarsened — NOT a 360 turntable
    out_cxc="finale.cxc", movie_out="finale_insitu.mp4")
```
Render locally (ArtiaX needs **GUI mode**, so no `--nogui` / `--offscreen`):
```bash
CX=/Applications/ChimeraX-1.10.app/Contents/bin/ChimeraX
"$CX" finale.cxc            # renders the hero still + gentle-rock movie, then exits
```
**Gotchas learned the hard way:**
- The translucent-slab block (`tomo_transfer`) **breaks** the scene locally — drop it; the ArtiaX
  orthoslice already shows the tomogram. (`artiax start` needs GUI: it fails under `--nogui` with
  `'Session' object has no attribute 'ArtiaX'` — that's expected, use GUI mode.)
- Errors in a `.cxc` go to the GUI **Log**, not stdout; add `log save log.html` to capture them.
- The **movie** with 3,387 instances is the cost, and **shadows are what hangs it** — per-frame
  shadow maps over thousands of instances stall even a single still. So: `movie_step=3` (coarser
  surfaces), `silhouettes=True` (crisp outlines, cheap), **`shadows` OFF**, and **rock** (`movie_rock=30`)
  not a full 360 (a thin particle slab goes edge-on mid-spin and hides the scene). Keep shadows for
  the single-map spins (§B2), where there's only one instance.

## B2. M-refined high-res map showcase
The two refined maps side by side, **at the same scale** (so their relative sizes are accurate),
**straight-on** (no `turn`), **on the same horizon**. First **pad both maps to the same box**
(e.g. 200³, centered) — different box sizes (190³ vs 160³) place their centers at different
heights (~14% offset). Plain ChimeraX (no ArtiaX): `open` BOTH padded maps in ONE scene → `volume
level ~mean+4·sd` → `surface dust #1 size 120` (clean speckle) → `color` → move them apart
(`move x -190 models #1` / `move x 190 models #2`) → one `view` (single camera = same scale) →
`lighting soft` + `shadows` + `silhouettes` on white → `save … transparentBackground true`.
Then `demo/finale/compose_maps.py` adds a Title-Case heading + per-map labels (auto-aligned to
the blue/gold centroids) → `demo/qc/finale/m_refined_maps.png` (+ the strip's `panel_maps.png`).
Do NOT render each map separately with its own `view` — that auto-fits each and destroys the
relative-size comparison.

**Rotating showcases (one 360 turntable per map).** The moving companion to the same-scale still —
`demo/finale/build_map_spins.py` → `map_spins.cxc` → `m_refined_ribo_spin.mp4` +
`m_refined_fas_spin.mp4`. Plain ChimeraX per map: `volume level ~mean+4sd` → `surface dust #1
size 120` → `color` → `lighting shadows/silhouettes` on white → `turn y 3 120` (360° / 120 frames).
A single map is cheap, so keep shadows on for the whole turn (unlike the thousands-of-instances
in-cell movie).

**M-refined FSC curve (the resolution proof).** `demo/finale/build_m_fsc.py` → `m_refined_fsc.png`
+ `m_refined_fsc.tsv`. No ChimeraX — pure matplotlib (repo `.venv`: `starfile` + `matplotlib`).
Sync the two WARP/M FSC stars from the cluster — `m/species/{ribo_<hash>,fas_<hash>}/<species>_fsc.star`
— into the run dir as `ribo_fsc.star` / `fas_fsc.star`, then `../.venv/bin/python
../demo/finale/build_m_fsc.py`. It plots the `_wrpFSCCorrected` column (phase-randomization-corrected,
the same honest basis as Gate 4) for both species and annotates the interpolated **0.143 crossing**
— which reproduces the headline **7.76 Å** (ribosome) / **13.88 Å** (FAS) straight from the curve.

> **§B footnote — the in-cell finale movie is now `emit_cxc`, not a hand-built `.cxc`.** The hero
> aesthetic is opt-in on the skill call: `emit_cxc(..., tilt_x=-55, silhouettes=True,
> still_out=…, movie_out=…, movie_rock=30, movie_step=3)`. Rock (±30), not a 360 turntable — a thin
> particle slab goes edge-on mid-spin and hides the scene; silhouettes need the tilt (face-on, the
> opaque slice occludes far particles so only a near-plane subset gets outlined). **Do NOT set
> `shadows=True` here** — shadows over thousands of instances hang even a single still (use them
> only for the single-map spins). Use the current `ribo_filtsharp.mrc @ 0.010`, NOT an older/cropped
> copy (a stale map reads as skeletal at the same contour).

## B3. Cellular-context render — TS_029 (organelle-rich companion)  ✅ rendered
The TS_028 finale is ribosome-dense but its cell is a near-uniform cytoplasm. **TS_029** was
picked for *cellular context*: it carries a large membrane-bound organelle (a clean round arc)
that the **1,825 ribosomes visibly exclude** — molecular sociology with real architecture — plus
38 FAS. Chosen by cross-referencing organelle content (clean `*_13.48Apx.png` central slices)
against per-tomogram particle counts (`grep -oE "TS_0[0-9]+" sel_ribo.star | sort | uniq -c`,
÷5 for the star's 5 mentions/row: TS_028 3387, **TS_029 1825**, TS_030 2006, TS_034 2794 …).

Sync + build (cluster; only the small binned tomogram travels, not the 1.78 GB full recon):
```bash
# bin the full reconstruction 4x -> 53.92 A/px (bin_tomo.py: block-mean + set voxel), on opuset_env python
python bin_tomo.py warp_tiltseries/reconstruction/TS_029_13.48Apx.mrc TS_029_bin4.mrc 4 13.48
# filter the selected poses to this tomogram (awk keeps header + TS_029 rows)
awk '/^(data_|loop_|_rln)/{print;next} /TS_029\.tomostar/{print}' opuset/ribo/z8_expanded/sel_ribo.star > ts029_ribo.star
awk '/^(data_|loop_|_rln)/{print;next} /TS_029\.tomostar/{print}' opuset/fas/z8/sel_fas30.star   > ts029_fas.star
cp m/species/ribo_11ea1073/ribo_filtsharp.mrc ribo_refined.mrc
cp m/species/fas_89b38e4c/fas_filtsharp.mrc   fas_refined.mrc
```
Render locally (GUI mode); the driver writes the absolute-path `.cxc`:
```bash
python demo/finale/build_ts029_cell_scene.py <input_dir>   # -> ts029_cell.cxc
CX=/Applications/ChimeraX-1.10.app/Contents/bin/ChimeraX
"$CX" ts029_cell.cxc        # -> ts029_hero.png (still) + ts029_insitu.mp4 (gentle rock), then exits
```
→ `demo/qc/finale/insitu_TS029_cell.png` + `insitu_TS029.mp4`. Recipe notes (same hero aesthetic
as §B — that's what makes it *look* good, and it survives the organelle):
- **Perspective `turn x -55` + `lighting shadows true` + `graphics silhouettes true width 1.2`** —
  the molecules read as a 3D cloud spilling past a floating slab (not a flat decorated micrograph),
  and at −55° the slab is edge-on enough that the organelle arc still reads on the right. A *face-on*
  view flattens it to a 2D slice and (worse) the opaque orthoslice occludes particles behind it, so
  silhouettes outline only an uneven near-plane subset — that is the messy look to avoid.
- **`view` does not reset orientation** — it re-fits zoom but keeps the current rotation. To render a
  second angle, re-issue the full setup or `view orient` first; chaining `view; turn x -38` after a
  prior `turn x -55` silently stacks to −93° (edge-on).
- **Movie = rock ±30° at the −55° tilt**, silhouettes on, `lighting shadows false` + `volume … step 3`
  (per-frame shadow maps are the cost — keep shadows for the still only, as in §B).

---

## C. Pipeline strip — "raw movies → molecular sociology"  (overview figure)
A single at-a-glance figure: five stage panels (raw frame series → tomogram → picks →
OPUS-ET 3D states → two-species sociology) tiled with arrows. **No ChimeraX** — pure
matplotlib. Panels 1/2/5 render from MRCs on the cluster; 3/4 reuse existing demo figures.

```bash
# 1) on the cluster (conda: opuset_env), render the MRC-derived panels:
#    raw = warp_frameseries/average/TS_028_*.mrc (a mid-series tilt, central crop)
#    tomo = warp_tiltseries/reconstruction/TS_028_bin4.mrc (central slab)
#    sociology = ribo_matching.star + fas_matching.star (TS_028) scattered on the tomo
#                Z-projection, colored by species (imshow extent = coord frame — no scale factor)
python make_panels.py                       # writes finale/strip/panel_{raw,tomo,sociology}.png
# 2) scp those three into demo/qc/pipeline_panels/, then compose locally:
python demo/gen_process_strip.py            # -> demo/qc/pipeline_strip.png
```
- The `make_panels.py` recipe lives in the session scratchpad; the sociology panel uses the
  **template-match** stars (all picks, one row per site) so positions sit in the TM-tomo frame.
- Swap panel ⑤ for the 3D in-cell ArtiaX finale (§B) once it's rendered, for the video capstone.

---

## Capture settings (both shots)
- **Resolution:** render ≥1080p (`size 1920,1080`), `supersample 3` for clean isosurfaces.
- **Background:** white for the gallery (set by the generator); the finale inherits the
  tomogram context — a dark background often reads better in-cell (adjust `set bgColor`).
- **Length:** a single 360° turn at 30 fps ≈ 12 s — trim to the ~6–10 s the scene needs.
- **Format:** MP4 (`movie encode … quality high`); grab a still with `save <png>` at the best angle.
