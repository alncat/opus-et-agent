# opus-et-warp: settings

Moved verbatim from SKILL.md so the skill body stays an overview; this is the reference.

## IMPORTANT: Gather Experimental Settings First

**Before generating any commands**, collect the parameters below. Do NOT use placeholder values — wait for actual answers. Only ask about phases the user needs.

Try to determine frame type automatically before asking (see Frame Type below).

### Microscope & Detector
1. **Pixel size** (`ANGPIX`) — physical pixel size in Å (e.g., `1.085`, `2.17`, `3.37`)
2. **Exposure per tilt** (`EXPOSURE`) — e⁻/Å² per tilt image (e.g., `2.36`, `3.2`)
3. **Frame format + type** — cannot be derived from filenames alone; determine from file extension first, then frame count.
   - **Extension check (list the data dir):**
     - `*.eer` → Falcon 4 EER movie (always fractionated; needs frame grouping)
     - `*.tif` / `*.tiff` → TIFF movie stack (fractionated)
     - `*.mrc` → could be either — inspect Z dimension (see Header Tools below): Z > 1 = fractionated, Z == 1 = single frame
   - Single-frame MRC → symlink to `average/` + `fs_ctf`
   - Fractionated MRC/TIFF/EER → `fs_motion_and_ctf` (with format-specific flags)
   - File naming and MDOC `NumberOfFrames` are not reliable — don't trust them for detection

   **Header tools — pick whichever is available:**
   ```bash
   # Preferred (PyTOM env is already required by this skill):
   conda activate "$PYTOM_ENV" && headerPyTom <file.mrc>

   # Alternative (only if IMOD is on PATH):
   header <file.mrc>

   # Fallback (works in any conda env with mrcfile active, e.g. WARP_ENV):
   python -c "import mrcfile, sys; m = mrcfile.open(sys.argv[1], permissive=True); print('shape:', m.data.shape); m.close()" <file.mrc>
   ```
   The `header` (IMOD) command is **not** assumed available. Default to `headerPyTom` since `PYTOM_ENV` is already required by the skill; fall back to the mrcfile one-liner only if PyTOM is unavailable.

   **Script-side MRC metadata readers:**
   - `warp_tiltseries_setup.slurm` reads unbinned frame X/Y with Python `mrcfile` after activating `WARP_ENV` (`m.header.nx`, `m.header.ny`). If `mrcfile` is missing and `AUTO_INSTALL_MRCFILE=1`, the script installs it into `WARP_ENV` with `python -m pip install mrcfile` and re-checks the import.
   - `warp_update_tomo_dims.slurm`, `gen_tm_jobs_aretomo.slurm`, and `gen_sphere_mask.slurm` run in `PYTOM_ENV` and read MRC dimensions with `headerPyTom`; do not import OPUS-ET/`cryodrgn` in these scripts.
   - `gen_training_mask.slurm`, `train_opuset.slurm`, and `train_opuset_fixed.slurm` run in `OPUSET_ENV`; OPUS-ET training `ANGPIX` is inferred only from STAR `_rlnDetectorPixelSize`, and mask shape/radius is handled through `dsdsh create_mask` / OPUS-ET readers.
4. **Gain reference** — required for most fractionated movies (EER, TIFF, raw MRC):
   - Path to gain MRC/DM4 (`--gain <file>`); ask user if present
   - Orientation flags (`--gain_flip_x`, `--gain_flip_y`, `--gain_transpose`) — ask only if the user already knows them; otherwise omit and let WARP's default apply
   - Skip entirely for already-gain-corrected single-frame MRC
5. **EER frame grouping** (EER only) — how many raw EER frames per dose fraction, e.g. `--eer_ngroups 10`. Ask user; typical: 10–20 groups per tilt.
6. **Defects map** (optional) — `--defects <file.txt>` for bad-pixel masking; only if user has one.

### Tilt Series
7. **Approximate tomo dimensions** (`TOMO_DIMS`) — ask the user for `cols×rows×Z` in **unbinned** pixels before creating tilt-series settings (e.g. `3840x3712x2000`).
   - `cols × rows` = detector size (fixed, from camera). If the user does not know it, derive X/Y from one unbinned frame or averaged tilt image: set `SAMPLE_FRAME` in `warp_tiltseries_setup.slurm`, or leave `TOMO_DIM_X/Y` blank so the script reads the first MRC under `warp_frameseries/average`. The script reads `m.header.nx` / `m.header.ny` with Python `mrcfile` in `WARP_ENV`; if `mrcfile` is missing, the script can auto-install it into `WARP_ENV`.
   - `Z` = an **upper bound on sample thickness** in unbinned pixels — only used to size AreTomo's reconstruction slab. Phase 3.5 rewrites it from the actual `_ali.mrc` output, so a rough estimate is fine. Defaults: `2000` for thin lamellae / thin cryo-ET, `3000–4000` for cells / thick specimens.
8. **Binning factor** (`BINNING_FACTOR`) — for `ts_stack` export and AreTomo (e.g. `4`, `8`). AreTomo runs on the **binned** stack, so it receives binned dims:
   - `VOL_Z = TOMO_DIM_Z / BINNING_FACTOR` → AreTomo `-VolZ`; `500` binned voxels is a practical starting value for many datasets, then adjust if the tomogram needs more or less Z margin
   - `ALIGN_Z` → AreTomo `-AlignZ`; set this close to the measured sample thickness in **binned voxels**, and always keep `ALIGN_Z < VOL_Z`
   - `ALIGN_ANGPIX = ANGPIX × BINNING_FACTOR` (stack export pixel size)
   The unbinned `TOMO_DIMS` stays in `warp_tiltseries.settings`; only AreTomo's CLI sees the binned values (computed inside the script).
   For `ALIGN_Z`, do not rely on a fixed fraction of `VOL_Z` when accuracy matters. A practical approach is to run global-only alignment / a quick 3D reconstruction at large binning, inspect an XZ slice, measure the specimen thickness, then set `ALIGN_Z` near that thickness. `VOL_Z` should remain larger to provide final-reconstruction Z margin.

### Particle / Template Matching
9. **Particle diameter** (`DIAMETER`) — in Å (e.g., `320`)
10. **Box sizes** — there are **two distinct boxes**, sized at different pixel sizes:
    - **TM template / mask box** (used in Phase 6 by `gen_sphere_mask.slurm` and consumed by `gen_tm_jobs_aretomo.slurm`): sized at `ALIGN_ANGPIX`, since TM runs on the binned reconstructions. Rule: `box ≈ round_to_even(DIAMETER / 0.75 / ALIGN_ANGPIX)` (particle occupies ~75% of box). Example: 320 Å at `ALIGN_ANGPIX=13.48 Å` → 32 px.
    - **Subtomo export box** (`SUBTOMO_BOX_SIZE` in `warp_export_particles.slurm`, `warp_m_export.slurm`, OPUS-ET training): sized at `OUTPUT_ANGPIX`. Rule: `SUBTOMO_BOX_SIZE ≈ round_to_even(DIAMETER / 0.75 / OUTPUT_ANGPIX)` (particle occupies ~75% of box), with `DIAMETER / 0.5 / OUTPUT_ANGPIX` for high-res / strong CTF cases. Example: 320 Å at `OUTPUT_ANGPIX=2.5 Å` → 171 px → round to 176.

    Both should be a multiple of 8 (preferably 16) for FFT efficiency. Box too small → CTF aliasing; too large → wasted memory and slower training.
11. **Subtomogram export pixel sizes** — `OUTPUT_ANGPIX` in `warp_export_particles.slurm` must be chosen by the user/agent for OPUS-ET training and is often **binned** after template matching. Do not derive it from `warp_tiltseries.settings`. `COORDS_ANGPIX` is the pixel size of coordinates in the input STAR; after this skill's PyTom→WARP conversion it is usually the unbinned `ANGPIX`. **⚠ Export is destructive:** subtomos always land in the shared `warp_tiltseries/subtomo/TS_XXX/TS_XXX_<index>_<OUTPUT_ANGPIX>A.mrc` (fixed by `--settings`, **not** `--output_star`), so a second species/box/re-export at the same `OUTPUT_ANGPIX` silently overwrites the previous particles. Isolate each species in its own `subtomo_<label>/` before re-exporting — see [references/workflow.md](references/workflow.md) §7.1 and item 22 (`DATADIR`).
12. **Template generation / TM template** — **always ask the user** for the source template map (`INPUT_MRC`) and its pixel size (`MAP_ANGPIX`); never guess or default to `ribo.mrc`. The default workflow is to run `gen_template_from_mrc.slurm` before template matching, using `ANGPIX`, `BINNING_FACTOR`, and `TM_BOX_SIZE`, then set `TEMPLATE_MRC` to the generated output (usually under `templates/`). Only skip template generation if the user confirms they already have a PyTom-ready template at exactly `ALIGN_ANGPIX = ANGPIX × BINNING_FACTOR` and the intended TM box size.
13. **TM sphere mask** (`TM_MASK_MRC`) — absolute path to the mask paired with the template; produced by `gen_sphere_mask.slurm` and consumed by `gen_tm_jobs_aretomo.slurm`. Default: `$WORK_DIR/templates/${TM_LABEL}_mask.mrc`. The mask box is read from the template MRC dimensions, and the default sphere radius is derived in pixels as `round(DIAMETER / 2 / ALIGN_ANGPIX)`. Keep `MASK_SIGMA=1` unless the user asks for a softer edge; the safety condition is `MASK_RADIUS + MASK_SIGMA < TEMPLATE_DIM / 2`, so the soft edge fits inside the template box. **Both scripts must reference the same mask path** — propagate one variable into both.
14. **Extraction non-max suppression radius** (`EXTRACT_MASK_RADIUS`, pixels) — in `extract_tm_candidates_parallel.slurm` (default 14, passed as `--mask-radius`). Intentionally **larger** than the sphere-mask `MASK_RADIUS`; do NOT derive it from `round(DIAMETER / 2 / ALIGN_ANGPIX)` (that formula yields the sphere radius ~11, not the NMS radius). Two peaks closer than this distance are merged. Wrong value → either duplicate hits (too small) or missed adjacent particles (too large).
15. **Candidates per tomogram** (`NUM_CANDIDATES`) — top-N peaks extracted per TS in `extract_tm_candidates_parallel.slurm`. Over-pick (5000–10000 for crowded specimens, lower for sparse) and filter by score in the resulting STAR.
16. **Extraction reference/template name** (`TEMPLATE`) — with this skill's default job layout, `gen_tm_jobs_aretomo.slurm` creates one job directory per tilt series/tomogram, and `run_tm_sequential.slurm` runs `localization.py` inside that directory. PyTom therefore writes `scores_<template>.em` and `angles_<template>.em` directly in each job directory (e.g. `scores_ribo.em`, `angles_reference19.em`), not under `job_dir/<template>/`. **Always ask the user which template/reference suffix they want to extract**, since users commonly run multiple TMs against the same tomograms. Set `TEMPLATE` to that suffix (e.g. `TEMPLATE="ribo"`, `TEMPLATE="26s"`, or `TEMPLATE="reference19"`). Only leave empty if the user confirms a single reference per job; the helper will otherwise auto-pick the first matched pair and warn if ambiguous. The extractor can also read per-reference subdirs for externally organized PyTom jobs, but that is not produced by `gen_tm_jobs_aretomo.slurm`.
17. **TM label** (`TM_LABEL`) — one species/template namespace, e.g. `ribosome`, `26s`, `hsp`. For multiple species, run Phase 6 once per `TM_LABEL` and keep all intermediate files under `template_matching/$TM_LABEL/`. Do not share `particles/`, `star_files/`, or `warp_star/` across species; it becomes impossible to know which picks belong to which template. Prefer keeping `TM_LABEL`, the generated template basename, and the PyTom extraction `TEMPLATE` suffix consistent unless the user has an existing naming convention.

### OPUS-ET Training
18. **Latent dimension** (`ZDIM`) — default `8`; increase for more expressivity and more heterogeneous datasets
19. **Number of epochs** (`NUM_EPOCHS`) — default `40`
20. **Number of GPUs** — for SLURM (`--nproc_per_node`)
21. **Training decoder size** (`TEMPLATERES`) — OPUS-ET `--templateres`; the decoder output size should be ~ `SUBTOMO_BOX_SIZE × downfrac / 0.75` so the particle occupies ~75% of the decoder output. This is typically smaller than `SUBTOMO_BOX_SIZE` because the encoder downsamples.
22. **Training subtomogram directory** (`DATADIR`) — the subtomogram export directory (e.g. `$WORK_DIR/warp_tiltseries/subtomo`, or per-species like `subtomo_ribo`). The training scripts pass `--datadir $(dirname "$DATADIR")` to OPUS-ET because the STAR file's `_rlnImageName` entries already include the subdirectory prefix. For mask creation, `$DATADIR` is used directly to find a sample subtomogram.
23. **Training mask** (`TRAINING_MASK_MRC`) — required by OPUS-ET training. Default: `$WORK_DIR/templates/${TM_LABEL}_training_mask.mrc`. For template-matching filtering, a broad centered sphere mask is acceptable: run `gen_training_mask.slurm` with `MODE="sphere"` (the default, soft edge 2 voxels; the dilate/erode morphology applies only in `MODE="density"`), which samples an exported subtomogram from `DATADIR` for shape/header (so the output mask matches `SUBTOMO_BOX_SIZE` at `OUTPUT_ANGPIX`). The default sphere diameter is 85% of the box (`MASK_RADIUS_FRACTION=0.85`). Use `MODE="density"` only when a clean consensus map should define a tighter molecular mask. If the mask is missing at training time and `AUTO_CREATE_TRAINING_MASK=1`, `train_opuset.slurm` and `train_opuset_fixed.slurm` can still create a fallback spherical mask from an exported subtomogram using `dsdsh create_mask --sphere-radius`.
24. **Training geometry overrides** (`ANGPIX`, `TILT_RANGE`, `TILT_STEP`) — in `train_opuset.slurm` and `train_opuset_fixed.slurm`, leave `ANGPIX` blank to auto-detect only from `_rlnDetectorPixelSize` in the input particle STAR file. Do not infer training `ANGPIX` from `OUTPUT_ANGPIX` in `warp_export_particles.slurm` or from `warp_tiltseries.settings`. Tilt range/step are read from the first `.tlt` file under `TILTSTACK_DIR`, normally `warp_tiltseries/tiltstack`. Set them manually only when the auto-detected values are wrong or the dataset uses non-standard metadata.
25. **Fixed-mode subset split** — Phase 8c needs two independent particle halves for half-map reconstruction. The input is a **selected** subset of particles (e.g., from OPUS-ET analysis picking a specific conformational state), not a blind split of the full matching STAR. Once the user has a selected STAR (`sel.star`), split it with:
    ```bash
    # signature: dsdsh convert_star <starfile> <angpix> [--subset-label N] [--remove-symexp] [--rescale-angpix T]
    # angpix is POSITIONAL (the star's pixel size — OUTPUT_ANGPIX for an OPUS-ET sel.star);
    # there is NO --angpix and NO -o flag. Output is auto-named <starbasename>_subsetN.star.
    dsdsh convert_star sel.star <OUTPUT_ANGPIX> --subset-label 1   # → sel_subset1.star
    dsdsh convert_star sel.star <OUTPUT_ANGPIX> --subset-label 2   # → sel_subset2.star
    ```
    Run 8c twice, once per subset, with `FIXED_SUBSET_LABEL=1` and `FIXED_SUBSET_LABEL=2`.
    If the particles were symmetry-expanded (e.g. D3 FAS), de-expand **first** with
    `dsdsh convert_star sel.star <OUTPUT_ANGPIX> --remove-symexp` (one row per `rlnImageName`),
    or the two halves each carry symmetry copies of the same particle and the FSC is inflated.
    You **can** then re-symmetry-expand each *already-disjoint* subset for fixed-mode training
    (`relion_particle_symmetry_expand` on `sel_subset1.star` and `sel_subset2.star` **separately**,
    never the pre-split star) → a symmetrized half-map while the two halves stay independent
    (see [references/workflow.md](references/workflow.md) §8.6).

### Environment Paths
**These are required for ALL phases — collect them first before anything else.**
25. **Project directory** (`WORK_DIR`) — absolute path where data and scripts will run
26. **WARP install path** (`WARP_DIR`) — absolute path to `publish/` dir, e.g. `/home/user/warp/Release/linux-x64/publish`. **Must be the alncat fork on the `alncat` branch** (<https://github.com/alncat/warp>), not upstream <https://github.com/warpem/warp>: this skill's `ts_export_particles` calls rely on `--dont_correct_ctf_3d` and `--output_ctf_csv`, both of which exist only in the fork (OPUS-ET ingests the per-particle CTF CSV and applies CTF correction internally). Verify with `WarpTools ts_export_particles --help | grep dont_correct_ctf_3d` — empty output means stock WARP. To install the fork:
    ```bash
    git clone https://github.com/alncat/warp.git
    cd warp
    git checkout alncat
    # Then follow the upstream WARP build instructions to produce
    # Release/linux-x64/publish/. Point WARP_DIR at that publish/ directory.
    ```
27. **WARP conda env** (`WARP_ENV`) — e.g. `warp_build`
28. **OPUS-ET conda env** (`OPUSET_ENV`) — e.g. `opuset_env`
29. **PyTOM conda env** (`PYTOM_ENV`) — e.g. `pytom_env` (template matching phases)
30. **conda lib path** (`CONDA_LIB`) — `lib/` dir of WARP conda env; derive with:
    ```bash
    conda activate <WARP_ENV> && echo $CONDA_PREFIX/lib
    ```
    e.g. `/home/user/.conda/envs/warp_build/lib`
31. **AreTomo cluster modules** (`MODULE_CUDA`, `MODULE_COMPILER`) — only `warp_aretomo_align_negate.slurm` uses `module load`; set these only if AreTomo2 requires cluster modules on the user's system, otherwise set them to `""`.
32. **SLURM partitions and GPU count** — ask once, propagate into every script:
    - `CPU_PARTITION` — partition name for CPU-only jobs (scripts shipped as `--partition=normal`). Examples: `normal`, `compute`, `cpu`.
    - `GPU_PARTITION` — partition name for GPU jobs (scripts shipped as `--partition=gpu` or `--partition=normal`). Examples: `gpu`, `gpu-a100`, `volta`.
    - `GRES_GPU` — full `--gres` value, e.g. `gpu:4`, `gpu:a100:4`, `gpu:1`. Some sites require the GPU type (`gpu:a100:4`); check `sinfo -o "%G"`.
    - `NUM_GPUS` — integer, must equal the count in `GRES_GPU`. Used by the training scripts (`--nproc_per_node`, `--num-gpus`).
    These default values in the scripts assume a generic SLURM setup — **do not submit without confirming** the partition names exist on the user's cluster (`sinfo -s` lists available partitions).

### Optional
33. **Output directory prefix** (`OUTPUT_DIR`) — e.g., `opuset/${TM_LABEL}/z${ZDIM}`

### M Refinement
34. **Half-maps** (`M_HALF1`, `M_HALF2`) — after Phase 8d, these default to `opuset/<TM_LABEL>/half1.mrc` and `half2.mrc`. User can also provide externally generated half-maps if skipping Phase 8c/8d.
35. **Resample pixel size** (`ANGPIX_RESAMPLE`) — MCore working pixel size for species creation. Defaults to `$ANGPIX` (unbinned detector pixel size). Set to a coarser value for faster refinement with lower memory usage.
36. **Half-map mask** (`M_MASK`) — defaults to `opuset/<TM_LABEL>/halfmap_mask.mrc` from Phase 8d. User can provide a custom mask.
37. **Half-map mask threshold** (`M_HALFMAP_THRESHOLD`) — density threshold for creating the half-map mask in Phase 8d. Only positive density above this value is kept. User must provide this.
38. **RELION particles STAR** (`M_PARTICLES_STAR`) — RELION-format particle STAR file for MTools create_species. User must provide this.
39. **M mask threshold** (`M_MASK_THRESHOLD`) — density threshold for MTools update_mask between MCore passes (optional, only needed if running Md).

---

