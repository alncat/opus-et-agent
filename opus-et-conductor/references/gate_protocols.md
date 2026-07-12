# Checkpoint Gate Protocols

Each gate: what the conductor auto-prepares, what it presents, what the scientist
decides, and what is written back to config + `.opus_run_state.json`.

## Gate 0 — Setup (before Phase 1)
- Prepare: auto-detect frame type (extension + MRC Z-dim), pixel size (mdoc/header),
  tomo dims; fill `pipeline.conf` + `species.conf`.
- Decide: confirm/correct acquisition params.
- Persist: `checkpoints[] {gate:"setup", params_set:{...}}`.

## Gate 1 — Alignment / reconstruction QC (after Phase 5)
- Prepare: for each reconstructed tomogram, render central XY (top-down) + XZ (side)
  preview PNGs with `opus-et-visualize/scripts/slice_preview.py`
  (`preview_mrc(mrc_path, out_prefix)` → `<out_prefix>_xy.png`, `<out_prefix>_xz.png`)
  into `gate1_qc/`. Fan out a parallel QC **Workflow** — one agent per tomogram — each
  judging alignment/reconstruction quality from its slices (missing-wedge smearing,
  streaking, blank/failed volumes, no recognizable cellular density) and returning a
  per-TS verdict (`good` / `suspect`) with a one-line reason.
- Optional handedness check: render matched-Z slices of the WARP vs AreTomo
  reconstruction for one TS and confirm the two agree (no chirality flip) before
  trusting downstream poses. Save as `gate1_qc/handedness_<TS>.png`.
- Present: the per-TS verdicts + reasons, the slice previews, and (if run) the
  handedness montage.
- Decide: which tomograms to keep vs. exclude before template matching.
- Persist: `checkpoints[] {gate:"alignment_qc", kept:[...], excluded:[...]}`. Exclude
  each rejected TS via the keep-list mechanism — relocate `TS.tomostar` →
  `tomostar/excluded/` (the tomostar-globbing case of the Gate 2 exclusion protocol
  below; every phase from 6 onward globs tomostar, so this drops it from all of them).
  `run_state.exclude_tomostar(state, ts)` records it; `run_state.active_tomostars(all,
  state)` is the in-memory view of what remains.

## Gate `tm_params` — TM parameter selection (before Phase 6)
The *before-Phase-6* decision of which matching parameters to run — distinct from Gate 2's
*after-Phase-6* score threshold.
- Prepare (mask — implemented): run `opus-et-warp/scripts/tm_auto_mask.py` on the
  **native-resolution** template → `mask_radius_angstrom` + radial-profile PNG. Set
  `MASK_RADIUS = round(mask_radius_angstrom / ALIGN_ANGPIX)` and `MASK_SIGMA` in
  `species.conf`. Measure at native resolution and convert to tomogram pixels — do NOT
  measure on the `ALIGN_ANGPIX`-resampled template (the coarse box under-sizes the radius
  ~1 px). Optionally render a ChimeraX overlay of the resampled template + sphere mask for
  a visual fit check; the default `--target 0.95` is a snug mask that trims flexible outer
  density (raise it for fuller enclosure).
- Prepare (matching params — Phase 2, pending `tm_auto_tune`): sweep bandpass / angular /
  mask-radius on one reference-rich tomogram, scored vs the reference pick set (e.g.
  `sel30.star`) by best-F1 (recall-first, since the reference is curated/incomplete).
- Set `NUM_CANDIDATES` (top-N peaks/TS to extract, then filter by score) to the target's
  abundance: abundant particles ~5000/TS, sparse species far fewer (FAS ~600/TS — its
  reference has only ~400 picks across all 10 tomograms).
- **Validate before the full run** (single-TS gate): run TM on ONE reference-rich tomogram,
  extract, and score recall vs the reference with `tm_eval_agreement.py`. High recall
  confirms the template scale, `TEMPLATE_INVERT` (contrast sign), coordinate frame, and the
  missing wedge are all right before committing every tomogram (ribo TS_034 → recall 1.000;
  FAS validated on TS_028). Low recall means fix contrast/scale/mask — NOT the threshold.
- Decide: accept or override the mask and (when available) the matching params.
- Persist: `MASK_RADIUS`, `MASK_SIGMA` (+ `ANGLE_LIST` / template low-pass / `MIN_SCORE`
  when tuned) into `species.conf`; `checkpoints[] {gate:"tm_params", params_set:{...}}`.

## Gate 2 — Picks QC (after Phase 6)
- Prepare: per-TS template-match score counts; a suggested `MIN_SCORE`.
  - Numeric: `opus-et-analysis/scripts/tm_eval_agreement.py` scores picks vs a
    reference (precision/recall/F1, best-F1 threshold per TS).
  - Visual: `opus-et-visualize/scripts/tm_picks_overlay.py` renders tomogram
    z-slabs (~1-particle thick) with picks overlaid — two PNG sets per slab,
    all picks and the top-N by score — into `gate2_qc/`. Confirms picks land on
    real density and lets you eyeball where the score threshold should fall.
    Fan out one agent per TS (parallel to Gate 1's `slice_preview`):
    `python .../tm_picks_overlay.py --tomogram <recon.mrc> --picks <TS_XXX_particles.xml|*_warp.star> -o gate2_qc/TS_XXX`
- Decide: score threshold + suspicious-TS exclusions.
- Persist: `MIN_SCORE`; record exclusions with `run_state.exclude_tomostar(state, ts)`.
- Honor the keep-list without editing any SLURM script, by relocating each excluded
  TS's INPUT ARTIFACT FOR THE NEXT PHASE out of that phase's input directory (a
  sibling `excluded/` subdir the glob skips):
  - For tomostar-globbing phases (setup / alignment / recon / TM job-gen, which
    iterate `$TOMOSTAR_DIR/*.tomostar`): relocate `TS.tomostar` →
    `tomostar/excluded/`.
  - For Phase 7 export (`warp_export_particles.slurm`, which globs
    `$TM_WARP_DIR/*_warp.star`, NOT tomostar files): relocate that TS's
    `*_warp.star` out of `$TM_WARP_DIR` instead — moving the `.tomostar` alone
    would NOT exclude it from export.
  - `run_state.active_tomostars(all, state)` remains the in-memory view of what
    remains.
  - Caveat: whether WarpTools actually skips an already-produced artifact this
    way is NOT verified from this repo and must be confirmed by a live
    integration check (M5).

## Gate 3 — State selection (after Phase 8b + analysis)
- Prepare: `analyze_opuset.slurm` (`dsdsh analyze` + `dsdsh eval_vol kmeans` → cluster-center
  maps + UMAP/PCA). **Pick k per species, not one k for the whole run:** use a HIGHER k for
  sparse/rigid species than for abundant/flexible ones (e.g. FAS 30 vs the ribo's 20) so a
  well-resolved minority population separates cleanly from the low-res majority blob instead of
  being absorbed into it. Real case: FAS only resolved into one distinct, well-separated latent
  island (k16) at k=30; at the ribo's k=20 it did not separate.
- Identify real states with **converging signals** (workflow.md §8.4), not one score:
  `compare_to_template.py` (masked CC to a template + the internal consensus — junk rejection),
  `state_consistency.py` (template-free N×N map-to-map consistency — the high-res core is a
  block), the latent **UMAP**, and a **3D ChimeraX gallery** (color each state distinctly,
  never highlight a subset). Template-CC alone is biased toward smooth blobs; the template-free
  signals + 3D resolution are the reliable discriminators. Render the UMAP **colored by
  k-means label** (not by particle index) — a cleanly separated island in that coloring is a
  genuine distinct population, not noise (FAS k16 sat alone at UMAP~10, far from the main
  blob).
- **Judge state RESOLUTION in 3D at a high percentile contour (~0.98), never by CC-to-consensus/
  template.** A sharp high-res map correlates LESS with the blurry averaged consensus / low-res
  template, so `compare_to_template.py`'s CC ranking pushes the BEST states to the BOTTOM; treat a
  low consensus-CC as a POSSIBLE high-res signal, not junk, and cross-check in 3D. ChimeraX's
  default/auto contour also sits too low and renders high-res detail as low-density "speckle" that
  reads as grainy junk — render with `demo/gen_gallery_cxc.py --percentile 98` (per-map 98th-percentile
  contour). A cleanly separated, abundant latent-UMAP island is often the sharpest/best-aligned
  population, not an artifact. (Real case: ribo z8_expanded k17/18/19 were the high-res ribosomes
  yet ranked last by CC and looked grainy at auto-contour.) This cuts both ways at the k-means
  step too: the sharpest/highest-res state map reads as fragmented "speckle" at a tight contour
  and ranks LAST by `cc_template`, while a smooth low-res map looks deceptively clean — don't
  let apparent tidiness at Gate 3 override the 3D-resolution read.
- **`eval_vol` cluster-center maps are DECODER outputs, not independent reconstructions —
  their crispness is not evidence of resolution.** The decoder can hallucinate learned
  high-frequency detail onto a sparse/low-SNR cluster, so an `eval_vol` map can look sharper
  than the underlying data supports. NEVER judge final resolution from how crisp an `eval_vol`
  map looks; use it only to pick candidate clusters. The only trustworthy resolution number is
  the independent gold-standard half-map FSC (Gate 4, fixed-mode subset1/2 → `compute_fsc.py`).
  Real case: FAS k16's `eval_vol` map looked crisp, but its half-map FSC was only ~31 Å
  (sparse selection: 180 particles/half) — confirm resolution at Gate 4 before trusting the
  Gate 3 map's appearance.
- Decide: which k-means cluster(s) to push to high-res.
- Build `sel.star`: `select_state.slurm` with `SELECT_CLUSTERS="..."` — injects the OPUS-ET-
  **refined** poses (`pose.<e>.pkl`, `-D SUBTOMO_BOX_SIZE --Apix OUTPUT_ANGPIX`), splits by
  cluster, and combines the chosen clusters → `sel_<label>.star`.
- QC: `state_tomo_stats.py` (per-tomogram selected count/fraction — a high fraction flags a
  dense, cap-limited tomogram → candidate for the pick-more re-pick, §6.4 + §8.7).
- Persist: `artifacts.selected_states`, `sel.star` path.

## Gate 4 — Half-map resolution + refine sign-off (after fixed-mode)
- **Fixed-mode half-maps:** split `sel_<label>.star` into two gold-standard halves →
  `warp_tiltseries/<label>_matching_subset{1,2}.star`. The split is a RANDOM ~50/50 partition
  that preserves the star header: keep the header lines up to the first data row, random-permute
  the data rows, split in half, and write each half back under the same header (single `data_`
  block; `rlnRandomSubset` labelling optional since the two files are separate). Then
  `train_opuset_fixed.slurm` with `FIXED_SUBSET_LABEL=1` and `=2` picks its half
  (env-overridable — must NOT be pinned in species.conf, or both jobs collide on subset 1).
  Each → `fixed_subset<N>/`; the half-map is the mean of the per-rank `tmp*.mrc`.
- **Caveat — a changed selection MUST regenerate the fixed-mode pose pkl / deep_split.** Re-running
  fixed-mode for a NEW `sel_<label>.star` at the same TM_LABEL path would otherwise silently reuse
  the previous selection's pose pkl (different particle count) → `PoseTracker.load` asserts a shape
  mismatch and all torchrun ranks exit 1 with the traceback only in the `.err` HEAD. The script now
  regenerates the pose pkl and drops `deep_split.pkl` whenever the star is newer than them.
- **Derive a molecule mask from the reconstruction** — NOT a sphere (a sphere induces a
  spurious high-frequency FSC rise): `gen_mask_from_map.py --half1 half1.mrc --half2 half2.mrc
  -o mask.mrc` (threshold + largest connected component + soft cosine edge). This mask feeds
  both the FSC and M refinement (`M_MASK`).
- **Gold-standard FSC:** `compute_fsc.py --half1 half1.mrc --half2 half2.mrc
  --apix <OUTPUT_ANGPIX> --mask mask.mrc -o fsc` → resolution at FSC=0.143 (report the FIRST
  crossing) + the FSC curve. By default it also applies the **phase-randomization correction**
  (RELION high-res noise substitution: randomize phases beyond the masked-FSC-0.8 shell,
  re-mask, subtract the mask-only correlation) and reports the *corrected* 0.143 resolution —
  the honest number once the residual mask artifact is removed (`--no-phase-randomize` for the
  raw masked curve; `--rand-res <Å>` to set the cutoff). Exact high-freq behaviour is not
  critical: only a low-resolution map goes into M, which refines from there.
- Present the resolution + FSC curve + a consensus isosurface. Decide: accept, or iterate
  (more particles via pick-more §8.7 if particle-limited, tighter selection, or proceed to M).
- **M refinement** (`warp_m_*`, advanced.md) takes half1/half2 + mask + `sel.star` → refined
  tilt-series alignment + per-particle poses → higher-res map + `<species>_matching_refined.star`
  (input to the in-cell ArtiaX finale).
- Persist: `artifacts.resolution_A`, `mask.mrc`, half-map paths.
