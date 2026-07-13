# WARP/OPUS-ET Complete Workflow

## Test on a small subset first (long-running phases only)

For phases that take many hours per submission, validate the configuration on
a 1–3 tilt-series subset before launching the full dataset. The phases worth
subset-testing are:

- **Phase 3 — `warp_export_stacks` + AreTomo alignment** (~5–15 min/TS)
- **Phase 5 — `warp_ts_reconstruct`** (~5–10 min/TS at moderate binning)
- **Phase 6 — template matching** (~5–10 min/TS)

Cheap phases (frame-series import, tilt-series setup, settings updates,
CTF estimation, alignment import, mask generation, STAR conversion) finish in
seconds to minutes — run them on the full set and inspect the output.

To subset-test, set `MAX_TS=N` (e.g. `MAX_TS=2`) in the CONFIGURATION block of
the script you want to validate. The three loops support this cap:

- `warp_aretomo_align_negate.slurm` — limits the alignment loop
- `gen_tm_jobs_aretomo.slurm` — limits the job-XML generation loop
- `run_tm_sequential.slurm` — limits how many TM jobs actually run

After the subset run looks correct, set `MAX_TS=0` to disable the cap and
re-submit for the full set. A common-enough mistake (wrong `BINNING_FACTOR`,
wrong `TOMO_DIMS`, wrong `ALIGN_ANGPIX`, wrong template path) caught on 2 TS
in 10 minutes saves a 24-hour job from finishing with bad parameters.

Note: `warp_ts_reconstruct.slurm` and `warp_ts_ctf.slurm` use a single
`WarpTools` call that processes every tilt series in the settings file, so
they don't have a `MAX_TS` knob. To subset-test those, point them at a
trimmed `tomostar/` (e.g. `tomostar_test/`) and a separate `.settings` file.

## Phase 1: Frame Series Processing

### 1.1 Create Frame Series Settings
```bash
WarpTools create_settings \
    --folder_data ../metadata/movies/ \
    --folder_processing warp_frameseries \
    --extension "*.mrc" \
    --angpix 3.37 \
    --exposure 2.36 \
    --output warp_frameseries.settings
```

### 1.2 Link Tilt Images (Single-Frame)
For single-frame tilt images (skip motion correction):
```bash
mkdir -p warp_frameseries/average
for f in ../metadata/movies/*.mrc; do
    ln -sf $(realpath $f) warp_frameseries/average/$(basename $f)
done
```

### 1.3 Estimate CTF on Frame Series
```bash
WarpTools fs_ctf \
    --settings warp_frameseries.settings \
    --grid 2x2x1 \
    --range_max 7 \
    --defocus_max 8
```

### 1.4 (Alternative) Use SLURM Script for Frame Series
```bash
sbatch scripts/warp_frameseries_import.slurm
```
This script automates steps 1.1-1.3 with configurable parameters.

## Phase 2: Tilt Series Setup

### 2.1 Create Tilt Series Settings
```bash
WarpTools create_settings \
    --folder_data tomostar \
    --folder_processing warp_tiltseries \
    --extension "*.tomostar" \
    --angpix 3.37 \
    --exposure 2.36 \
    --tomo_dimensions 3840x3712x2000 \
    --output warp_tiltseries.settings
```

### 2.2 Import Tilt Series from MDOC
```bash
WarpTools ts_import \
    --mdocs mdoc/ \
    --frameseries warp_frameseries \
    --tilt_exposure 2.36 \
    --min_intensity 0.3 \
    --output tomostar
```

### 2.3 (Alternative) Use SLURM Script for Tilt Series Setup
```bash
sbatch scripts/warp_tiltseries_setup.slurm
```
This script automates steps 2.1-2.2 with configurable parameters.

## Phase 3: Tilt Series Alignment

### 3.1 Export Tilt Stacks for AreTomo
```bash
# 4x binning: 3.37 * 4 = 13.48 Å
WarpTools ts_stack \
    --settings warp_tiltseries.settings \
    --angpix 13.48
```

Output: `warp_tiltseries/tiltstack/TS_XXX/TS_XXX.st` and `TS_XXX.rawtlt`

### 3.2 (Alternative) Use SLURM Script for Export
```bash
sbatch scripts/warp_export_stacks.slurm
```
This script exports tilt stacks with automatic pixel size calculation from settings.

### 3.3 Run AreTomo2 Alignment
```bash
AreTomo2 \
    -InMrc TS_026.st \
    -OutMrc TS_026_ali.mrc \
    -OutBin 1 \
    -AngFile TS_026.rawtlt \
    -OutImod 2 \
    -VolZ 256 \
    -Gpu 0 \
    -AlignZ 100 \
    -FlipVol 1 \
    -FlipInt 1 \
    -DarkTol 0.1 \
    -Wbp 1
```

### 3.4 (Alternative) Use SLURM Script for AreTomo2 + WARP Prep

**Option A: Angle negation method (recommended for consistency)**
```bash
sbatch scripts/warp_aretomo_align_negate.slurm
```
This script:
1. Negates `.rawtlt` → `_neg.rawtlt` (WARP → AreTomo convention)
2. Runs AreTomo2 with negated angles
3. Negates output back (AreTomo → WARP convention)

This ensures consistent angle conversion throughout the workflow.

## Phase 4: Import Alignment Parameters

### 4.1 Import Alignments into WARP

**Manual import:**
```bash
WarpTools ts_import_alignments \
    --settings warp_tiltseries.settings \
    --alignments warp_tiltseries/tiltstack/ \
    --alignment_angpix 13.48
```

**SLURM script:**
```bash
sbatch scripts/warp_import_alignments.slurm
```

**IMPORTANT:** Use the SAME binned pixel size as used in AreTomo2 alignment (e.g., 13.48 for 4x binning). The alignment parameters (.xf, .tlt) are in binned pixel units.

## Phase 5: CTF and Reconstruction

### 5.1 Check Defocus Handedness
```bash
WarpTools ts_defocus_hand \
    --settings warp_tiltseries.settings \
    --check
```

**If average correlation is negative:**
```bash
WarpTools ts_defocus_hand \
    --settings warp_tiltseries.settings \
    --set_flip
```

### 5.2 CTF Estimation on Tilt Series

**Manual:**
```bash
WarpTools ts_ctf \
    --settings warp_tiltseries.settings \
    --window 512 \
    --range_high 7 \
    --defocus_max 8
```

**SLURM script** (includes defocus handedness check):
```bash
sbatch scripts/warp_ts_ctf.slurm
```

### 5.3 Reconstruct Tomograms

**Manual:**
```bash
# Enable 32-bit float output
export WARP_FORCE_MRC_FLOAT32=1

WarpTools ts_reconstruct \
    --settings warp_tiltseries.settings \
    --angpix 13.48
```

**SLURM script:**
```bash
sbatch scripts/warp_ts_reconstruct.slurm
```

## Phase 6: Template Matching 

### 6.1 Generate Sphere Mask
```bash
sbatch scripts/gen_sphere_mask.slurm
```

Creates a spherical mask `ribo_mask.mrc` (`<TM_LABEL>_mask.mrc`) matching the template dimensions.

### 6.2 Generate Template Matching Job XMLs
```bash
sbatch scripts/gen_tm_jobs_aretomo.slurm
```

Generates `template_matching/<TM_LABEL>/jobs/TS_XXX/job.xml` files configured to use:
- AreTomo-reconstructed tomograms (`*_ali.mrc`)
- Template and mask volumes
- Wedge angles calculated from tilt angles
- PyTom angle list (`angles_12.85_7112.em`)

### 6.3 Run Template Matching Jobs
```bash
sbatch scripts/run_tm_sequential.slurm
```

Runs all template matching jobs **sequentially** on a single node:
- Uses 4 GPUs (`--gres=gpu:4`)
- 4 MPI processes (`mpiexec -n 4`)
- One job at a time (avoids GPU memory conflicts)
- Estimated time: ~5-10 minutes per tomogram

### 6.4 Extract Particle Candidates
```bash
sbatch scripts/extract_tm_candidates_parallel.slurm
```

Extracts particle candidates from template matching results:
- Extracts **5000 particles** per tomogram by default
- Uses parallel processing (multiple CPUs)
- Output: `template_matching/<TM_LABEL>/particles/TS_XXX_particles.xml`
- Individual log files for each tomogram

Parameters (edit script):
- `NUM_CANDIDATES=5000` - top-N peaks per tomogram (over-pick, filtered later by score/clustering)
- `EXTRACT_MASK_RADIUS` - non-max-suppression radius in pixels
- `MIN_SCORE=0.0` - minimum correlation score

The cap is uniform, but tomograms differ a lot in particle density. A tomogram where a high
fraction of its top-N land in the good k-means clusters is dense and the cap is likely
truncating **real** particles — re-extract just those tomograms at a higher cap (reusing the
existing `scores_*.em`, so no re-TM):
```bash
extractCandidates.py --jobFile <jobs>/TS_XXX/job.xml --result <jobs>/TS_XXX/scores_<TM_LABEL>_tm.em \
    --orientation <jobs>/TS_XXX/angles_<TM_LABEL>_tm.em --particleList <particles>/TS_XXX_particles.xml \
    --particlePath <particles> --size <EXTRACT_MASK_RADIUS> --numberCandidates <higher> --minimalScoreValue <MIN_SCORE>
```
then re-convert/-export those tomograms and warm-start (§8.7). Sparse tomograms should stay
at the base cap — raising theirs only adds noise (clustering drops it, but wastes compute).

### 6.5 Convert to STAR Format
```bash
sbatch scripts/convert_to_star.slurm
```

Converts PyTom particle XML files to STAR format:
- Output: `template_matching/<TM_LABEL>/star_files/*.star`

### 6.6 Convert to WARP Format
```bash
sbatch scripts/convert_pytom_to_warp.slurm
```

Converts PyTom STAR files to WARP-compatible format:
- Uses `dsdsh convert_pytom`
- Output: `template_matching/<TM_LABEL>/warp_star/*_warp.star`
- Output files are named `<ts_name>_norm.star` then renamed

## Phase 7: Export for OPUS-ET

### 7.1 Export Subtomograms

**Manual command:**
```bash
WarpTools ts_export_particles \
    --settings warp_tiltseries.settings \
    --input_directory template_matching/<TM_LABEL>/warp_star/ \
    --input_pattern "*_warp.star" \
    --output_star warp_tiltseries/<TM_LABEL>_matching.star \
    --output_angpix <OUTPUT_ANGPIX> \
    --box <SUBTOMO_BOX_SIZE> \
    --diameter <DIAMETER> \
    --relative_output_paths \
    --3d \
    --coords_angpix <ANGPIX> \
    --output_ctf_csv \
    --dont_correct_ctf_3d
```

`--output_angpix` is the **subtomogram** pixel size (`OUTPUT_ANGPIX`, a light bin ≥ raw
`ANGPIX`) and becomes the STAR's `rlnDetectorPixelSize` — this is what OPUS-ET training
must use for `--angpix`. `--coords_angpix` is the raw tilt-series `ANGPIX` (the pick
coordinate pixel size). Keep the two distinct.

**SLURM script:**
```bash
sbatch scripts/warp_export_particles.slurm
```

Required flags for OPUS-ET compatibility:
- `--output_ctf_csv` - Outputs CTF parameters
- `--dont_correct_ctf_3d` - OPUS-ET handles CTF correction

> **⚠ DANGER — the subtomo folder is shared and overwritten in place.**
> `ts_export_particles` writes the per-particle subtomograms to a **fixed** path under the
> tilt-series processing folder — `warp_tiltseries/subtomo/TS_XXX/TS_XXX_<index>_<OUTPUT_ANGPIX>A.mrc`
> (plus a `*_ctf_*.mrc` per particle) — chosen from the `--settings` file, **not** from `--output_star`.
> The filename encodes only the tilt-series name, the per-TS particle index (from 0), and the
> pixel-size suffix; it does **not** encode the species/`TM_LABEL` or the box size. A different
> `--output_star` gives you a separate STAR that still points back into the *same* shared
> `subtomo/` tree — it does **not** give you a separate set of density files. Every re-export
> therefore overwrites what is on disk. Three silent-corruption traps:
>
> 1. **Second species at the same `OUTPUT_ANGPIX`.** Exporting FAS at the ribo pixel size drops
>    `TS_XXX_00000_<angpix>A.mrc …` on top of the ribosome subtomos; the ribo STAR now points at
>    FAS densities. No error is raised.
> 2. **Re-export with a different box, same `OUTPUT_ANGPIX`.** The box size is not in the filename,
>    so the new box silently replaces the old `.mrc` and the old STAR's `rlnImageName` now resolves
>    to a differently-sized box.
> 3. **`warp_m_export.slurm`** re-exports M-refined particles into the same tree, clobbering the
>    Phase-7 export for that species.
>
> **Mitigation — give each species (and each incompatible export) its own subtomo tree.** After an
> export completes, rename `warp_tiltseries/subtomo/` → `warp_tiltseries/subtomo_<label>/`
> (e.g. `subtomo_ribo`, `subtomo_fas`) and update that species' `DATADIR` **and** the STAR's
> `rlnImageName` prefix (`subtomo/…` → `subtomo_<label>/…`) to match, *before* the next export.
> This is exactly why `DATADIR` is per-species (SKILL.md item 22). The clean-but-heavy alternative
> is a separate `--settings`/processing folder per species. Varying only `OUTPUT_ANGPIX` is **not** a
> safe separator on its own — the files coexist under different suffixes, but you double the disk and
> the isolation is accidental. Before any re-export, confirm you actually intend to replace what is on
> disk, or you will silently orphan a STAR.

### 7.2 Prepare OPUS-ET Input
The training scripts auto-generate the pose pickle from the export STAR. Manual form:
```bash
dsd parse_pose_star warp_tiltseries/<TM_LABEL>_matching.star \
    -D <SUBTOMO_BOX_SIZE> --Apix <OUTPUT_ANGPIX> -o particles_pose.pkl
```

**CRITICAL pixel-size / box conventions** (both silently corrupt the map if wrong):
- Training `--angpix` and the pose-pkl `--Apix` = the **subtomogram** pixel size
  (`OUTPUT_ANGPIX`, = the export STAR's `rlnDetectorPixelSize`), **NOT** the raw
  tilt-series `ANGPIX`. OPUS-ET computes the **CTF** from `--angpix`; a wrong value
  mis-scales every CTF zero. (See memory `opus-et-angpix-ctf`.)
- The pose-pkl `-D` = the **subtomogram box** (`SUBTOMO_BOX_SIZE` = lattice D−1, what
  OPUS-ET's `PoseTracker` scales translations by), **NOT** `TEMPLATERES` (the decoder
  output size). Harmless while translations are zero (template matching), but wrong once
  poses carry non-zero shifts (fixed-mode / refined-pose input).

## Phase 8: OPUS-ET Training & State Selection (Gate 3)

### 8.1 Generate Training Mask (Optional)
```bash
sbatch scripts/gen_training_mask.slurm
```
Density-shaped mask; skip it and `train_opuset.slurm` auto-creates a default sphere.

### 8.2 Train the heterogeneity model (grad mode)
```bash
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp scripts/train_opuset.slurm
```
- 4 GPUs, `NUM_EPOCHS` (default 40, ~10 min/epoch), `ZDIM=8`, `ZAFFINEDIM=6`.
- Uses the subtomogram `OUTPUT_ANGPIX` for `--angpix` (correct CTF).
- Output: `opuset/<TM_LABEL>/z<ZDIM>/` with `weights.<e>.pkl` / `z.<e>.pkl` /
  `pose.<e>.pkl` (refined poses) / `config.pkl` per epoch.

### 8.3 Analyze the latent space (Gate-3 prep)
```bash
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,ANALYZE_NUMK=20,ANALYZE_PC_NUM=0 \
       scripts/analyze_opuset.slurm
```
Runs `dsdsh analyze` (PCA + k-means + UMAP) then `dsdsh eval_vol kmeans` (cluster-center
maps) → `analyze.<epoch>/kmeans<numk>/` (`labels.pkl`, `reference<k>.mrc`, UMAP/PCA plots).
The installed `dsdsh eval_vol` is **positional**: `dsdsh eval_vol <resdir> <epoch>
{kmeans,pc} <num> <apix>` — not the older `dsd eval_vol --load --zfile`.

`analyze_opuset.slurm` and `select_state.slurm` honor `OUTPUT_DIR_OVERRIDE` (mirroring
`train_opuset.slurm`), so a warm-start / alternate result dir can be analyzed / selected
**without editing `species.conf`** (which pins `OUTPUT_DIR` and would clobber a plain
`--export`). The epoch auto-detects from the latest `z.<N>.pkl` in that dir:
```bash
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,OUTPUT_DIR_OVERRIDE=opuset/ribo/z8_expanded,\
       ANALYZE_NUMK=20 scripts/analyze_opuset.slurm
```

### 8.4 Identify real states (Gate 3 — multi-signal)
Which clusters are genuine particles? Use converging signals, not one score:
- `opus-et-analysis/scripts/compare_to_template.py` — masked CC of each map to a reference
  template + the internal consensus (good at junk rejection).
- `opus-et-analysis/scripts/state_consistency.py` — template-free N×N map-to-map consistency; the
  tight high-resolution core appears as a contiguous block.
- Latent **UMAP** (particle-space islands) + a **3D ChimeraX gallery** of the cluster maps
  (resolution/detail — color each state distinctly, never highlight a subset).

Template-CC alone is biased toward smooth blobs (it rewards agreement with a low-res
reference); the **template-free** signals + 3D resolution are the reliable discriminators.

Once the good clusters are chosen, `opus-et-analysis/scripts/state_tomo_stats.py`
(`--star <particles.star> --labels <labels.pkl> --select "<clusters>"`) reports the
**per-tomogram** selected-particle count + fraction. A high selected-fraction flags a
particle-dense tomogram whose fixed pick cap is likely truncating real particles — a
candidate for the cap-raising re-pick (§6.4) + warm-start (§8.7).

### 8.5 Build the selection star (with refined poses)
```bash
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,SELECT_CLUSTERS="8 9 10" \
       scripts/select_state.slurm
```
Injects the OPUS-ET-**refined** poses (`pose.<epoch>.pkl`, `-D SUBTOMO_BOX_SIZE`,
`--Apix OUTPUT_ANGPIX`) into the particles star, splits by cluster, and combines the chosen
clusters → `sel_<TM_LABEL>.star`. Fixed-mode + M then start from the refined poses.

### 8.6 Fixed-mode half-maps (gold-standard)
Split `sel_<TM_LABEL>.star` into two gold-standard halves at
`warp_tiltseries/<TM_LABEL>_matching_subset{1,2}.star` — a **random ~50/50** partition
that preserves the star header. For a single-`data_`-block star: keep the header lines up
to the first data row, random-permute the data rows, cut in half, and write each half under
that same header:
```bash
python - <<'PY'
import random
src = "sel_<TM_LABEL>.star"
lines = open(src).read().splitlines()
# header = everything up to the first particle data row (last '_rln...' col def + loop_)
first = next(i for i,l in enumerate(lines)
             if l.strip() and not l.startswith(("data_","loop_","_","#")) and i>3)
header, rows = lines[:first], [l for l in lines[first:] if l.strip()]
random.seed(0); random.shuffle(rows)
half = len(rows)//2
for n,chunk in ((1,rows[:half]),(2,rows[half:])):
    open(f"warp_tiltseries/<TM_LABEL>_matching_subset{n}.star","w").write(
        "\n".join(header+chunk)+"\n")
PY
```
(An `rlnRandomSubset` column is optional here since the two halves are separate files;
`train_opuset_fixed` picks its half via `FIXED_SUBSET_LABEL`.) Then run each:
```bash
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,FIXED_SUBSET_LABEL=1 scripts/train_opuset_fixed.slurm
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,FIXED_SUBSET_LABEL=2 scripts/train_opuset_fixed.slurm
```
Each averages its half → `opuset/<TM_LABEL>/fixed_subset<N>/`. `FIXED_SUBSET_LABEL` is
env-overridable (must not be pinned in `species.conf`, or both jobs collide on subset 1).
The half-map is the **mean of each subset's per-rank `tmp*.mrc`**.

### 8.6b Resolution QC (molecule mask + gold-standard FSC)
Derive a mask that follows the density (a **sphere induces a spurious high-frequency FSC
rise**), then measure resolution:
```bash
# molecule mask from the reconstruction (threshold + largest-CC + soft edge) — also the M_MASK
# --qc also writes a mask–density overlay PNG (3 orthogonal slices, mask envelope outlined) so
# the checkpoint *shows* the mask wraps the molecule without clipping (Gate-4 / pre-M QC)
python opus-et-analysis/scripts/gen_mask_from_map.py \
    --half1 opuset/<TM_LABEL>/fixed_subset1/tmp0.mrc \
    --half2 opuset/<TM_LABEL>/fixed_subset2/tmp0.mrc -o mask.mrc \
    --qc <TM_LABEL>_mask_overlay.png
# masked gold-standard FSC — report the FIRST 0.143 crossing (a rise after it is a mask artifact)
python opus-et-analysis/scripts/compute_fsc.py \
    --half1 opuset/<TM_LABEL>/fixed_subset1/tmp0.mrc \
    --half2 opuset/<TM_LABEL>/fixed_subset2/tmp0.mrc \
    --apix <OUTPUT_ANGPIX> --mask mask.mrc -o fsc
```
By default `compute_fsc.py` also applies the **phase-randomization correction** (RELION
high-resolution noise substitution): it randomizes phases beyond the shell where the masked
FSC drops below 0.8, re-applies the same mask, and subtracts the resulting mask-only
correlation — `corrected = (FSC_masked − FSC_random)/(1 − FSC_random)`. The printed "corrected"
0.143 resolution is the honest number; the plot overlays masked/randomized/corrected curves.
Pass `--no-phase-randomize` for the raw masked curve, or `--rand-res <Å>` to set the cutoff.
The exact high-frequency behaviour is not critical here — only a **low-resolution** map is
imported into **M**, which refines from there.

`mask.mrc` + the two half-maps feed **M refinement** (`warp_m_*`, advanced.md), which refines
tilt-series alignment + poses to push resolution toward Nyquist.

### 8.7 Warm-start / add more particles (optional, no full retrain)
To fold in more picks (e.g. raise the per-tomogram candidate cap on ribosome-dense
tomograms), re-extract + re-export the extra particles, build an expanded star, and
continue from a checkpoint:
```bash
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,\
  LOAD_CHECKPOINT=<resdir>/weights.<N>.pkl,\
  EXPORT_STAR_OVERRIDE=<expanded.star>,\
  OUTPUT_DIR_OVERRIDE=<new_dir>,\
  NUM_EPOCHS_OVERRIDE=<N+1+warm> scripts/train_opuset.slurm
```
`--load` restores encoder + decoder only (poses come from `--poses`, so all can start from
TM), and it sets `start_epoch = N+1` — so `NUM_EPOCHS = N+1+<warm epochs>` (e.g. load
`weights.25` + 10 warm epochs → `NUM_EPOCHS_OVERRIDE=36`).

### 8.8 Symmetric particles (point-group symmetry)
Validated this session on FAS (D3 barrel). Before OPUS-ET training, symmetry-expand the
particle star so the network sees every symmetry-related view of each particle:
```bash
module load relion/3.0.8_cuda10.1
relion_particle_symmetry_expand --i <TM_LABEL>_matching.star \
    --o <TM_LABEL>_matching_symD3.star --sym D3
```
Order(D3) = 6 → 6x rows; all columns preserved (`rlnImageName`, CTF, angles). Keep
`SYM=C1` in `species.conf` — symmetry is baked into the particle set, so the network
reconstructs in C1. Point training at the expanded star via `EXPORT_STAR_OVERRIDE` (§8.7).

After Gate-3 (§8.4–8.5), the selection star built from this expanded run holds the
symmetry-expanded copies too — the 6 D3 copies of a particle **share one `rlnImageName`**
and differ only in Euler angles.

**Fixed-mode half-maps + M (§8.6) require de-expanding first**, or the two halves each
hold D3 copies of the same particle and the halves become correlated (inflated FSC):
```bash
dsdsh convert_star sel_<TM_LABEL>.star <OUTPUT_ANGPIX> --remove-symexp
```
This keeps one row per `rlnImageName` (the identity/original pose). Then split with
`--subset-label 1` / `--subset-label 2`, which write a deterministic disjoint 50/50
`<basename>_subset{1,2}.star` (in place of the random-shuffle split in §8.6). Verify the
two halves are disjoint (0 shared `rlnImageName`) before running `train_opuset_fixed`.

**Alternative — impose symmetry IN the fixed-mode reconstruction itself:** D3-expand
each *already-disjoint* half-set star (`relion_particle_symmetry_expand` on subset1 and
subset2 separately, not on the pre-split star), then run fixed-mode on the expanded
halves. The decoder sees all 6 symmetry views per particle → a symmetrized map with
~6x the asymmetric-unit count, while FSC stays honest because the two halves still
share no physical particle. `FIXED_SUBSET_LABEL` is a free string, so route to a
distinct output dir with e.g. `FIXED_SUBSET_LABEL=1_symD3` →
`STAR_FILE=<TM_LABEL>_matching_subset1_symD3.star`,
`OUTPUT_DIR=opuset/<TM_LABEL>/fixed_subset1_symD3/`.

## Quick SLURM Submission

For automated processing, use:
```bash
# Phase 1: Frame series import + CTF
sbatch scripts/warp_frameseries_import.slurm

# Phase 2: Tilt series setup (settings + MDOC import)
sbatch scripts/warp_tiltseries_setup.slurm

# Phase 3: Export tilt stacks for alignment
sbatch scripts/warp_export_stacks.slurm

# Phase 3: Run AreTomo2 alignment + prepare for WARP
sbatch scripts/warp_aretomo_align_negate.slurm

# Phase 4: Import alignments into WARP
sbatch scripts/warp_import_alignments.slurm

# Phase 5: CTF estimation (includes defocus handedness check)
sbatch scripts/warp_ts_ctf.slurm

# Phase 5: Reconstruction
sbatch scripts/warp_ts_reconstruct.slurm

# Phase 6: Template matching (optional)
sbatch scripts/gen_sphere_mask.slurm               # Generate mask
sbatch scripts/gen_tm_jobs_aretomo.slurm           # Generate job XMLs
sbatch scripts/run_tm_sequential.slurm             # Run TM jobs
sbatch scripts/extract_tm_candidates_parallel.slurm # Extract particles
sbatch scripts/convert_to_star.slurm               # Convert to STAR
sbatch scripts/convert_pytom_to_warp.slurm         # Convert to WARP format

# Phase 7: Export for OPUS-ET
sbatch scripts/warp_export_particles.slurm         # Export subtomograms

# Phase 8: OPUS-ET Training + Gate-3 state selection (all take SKILL_DIR=$PWD/opus-et-warp)
sbatch scripts/gen_training_mask.slurm             # (optional) density training mask
sbatch scripts/train_opuset.slurm                  # 8.2 heterogeneity training (grad mode)
sbatch scripts/analyze_opuset.slurm                # 8.3 dsdsh analyze + eval_vol (kmeans maps)
#    -> 8.4 Gate 3: compare_to_template.py + state_consistency.py + UMAP + ChimeraX gallery
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,SELECT_CLUSTERS="..." scripts/select_state.slurm  # 8.5 sel star (refined poses)
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,FIXED_SUBSET_LABEL=1 scripts/train_opuset_fixed.slurm  # 8.6 half1
sbatch --export=ALL,SKILL_DIR=$PWD/opus-et-warp,FIXED_SUBSET_LABEL=2 scripts/train_opuset_fixed.slurm  # 8.6 half2
```

## File Organization

Names like `opuset/ribo/z8/`, `ribo_training_mask.mrc`, and `<species>` below are
illustrative — substitute with the user's chosen `OUTPUT_DIR` / template name
/ `SPECIES_BASE`. Epoch numbers shown as `<N-1>` resolve to `NUM_EPOCHS - 1`
(e.g. 39 for the default 40 epochs).

```
project/                              # = $WORK_DIR
├── *_<jobid>.out / *_<jobid>.err    # SLURM logs land HERE if sbatch is run
│                                       from $WORK_DIR (recommended)
├── mdoc/                             # raw MDOC files
├── tomostar/                         # canonical tilt-series list (*.tomostar)
├── tomostar_test/                    # OPTIONAL: 1–3 .tomostar copies for
│                                       subset testing (see top of this file)
│
├── warp_frameseries/                 # Phase 1: Frame series
│   ├── average/                     # linked / motion-corrected tilt images
│   ├── powerspectrum/               # CTF power spectra
│   └── TS_XXX_XX.xml                # per-image frame metadata
│
├── warp_tiltseries/                  # Phases 2–7
│   ├── tiltstack/TS_XXX/            # Phase 3: stacks + AreTomo output
│   │   ├── TS_XXX.st                # exported tilt stack
│   │   ├── TS_XXX.rawtlt            # raw angles
│   │   ├── TS_XXX_neg.rawtlt        # negated angles (AreTomo input)
│   │   ├── TS_XXX_ali.mrc           # AreTomo aligned tomogram (binned, at ALIGN_ANGPIX)
│   │   ├── TS_XXX.xf                # transforms (negated → WARP convention)
│   │   ├── TS_XXX.tlt               # angles (negated → WARP convention)
│   │   └── TS_XXX_Imod/             # AreTomo2 IMOD output
│   │       ├── TS_XXX_st.tlt        # original refined angles
│   │       └── TS_XXX_st.xf         # original transforms
│   ├── reconstruction/              # Phase 5: WARP tomograms (TS_XXX_<apix>Apx.mrc, at ALIGN_ANGPIX)
│   ├── subtomo/                     # Phase 7: ts_export_particles output
│   │   ├── TS_XXX/*.mrc             #   per-tilt-series subtomograms (at OUTPUT_ANGPIX)
│   │   └── *.csv                    #   per-particle CTF metadata (--output_ctf_csv)
│   ├── <TM_LABEL>_matching.star     # Phase 7: per-template export STAR (one per TM_LABEL)
│   ├── logs/                        # WARP processing logs
│   └── TS_XXX.xml                   # per-tilt-series metadata
│
├── templates/                        # Template generation output
│   ├── <TM_LABEL>_tm.mrc            # template at ALIGN_ANGPIX (gen_template_from_mrc.slurm)
│   └── <TM_LABEL>_mask.mrc          # sphere mask paired with the template (gen_sphere_mask.slurm)
│
├── template_matching/
│   └── <TM_LABEL>/                   # Phase 6: one namespace per species/template
│       ├── jobs/TS_XXX/             # PyTOM TM job dirs
│       │   ├── job.xml               # PyTom job config
│       │   ├── scores_<TM_LABEL>.em  # correlation scores
│       │   └── angles_<TM_LABEL>.em  # orientation angles
│       ├── particles/                # extracted candidates
│       │   ├── TS_XXX_particles.xml  # per-TS particle list (PyTom XML)
│       │   └── TS_XXX_extraction.log
│       ├── star_files/               # PyTom XML → per-TS STAR
│       │   └── TS_XXX.star
│       └── warp_star/                # WARP-compatible STAR
│           └── TS_XXX_warp.star
│
├── <output_dir>/                     # Phase 8: OPUS-ET training output (e.g. opuset/ribo/z8/)
│   ├── weights.<N-1>.pkl            # final model weights (NUM_EPOCHS-1)
│   ├── z.<N-1>.pkl                  # final latent embeddings (NUM_EPOCHS-1)
│   ├── config.pkl                   # model configuration
│   ├── run.log                      # training log
│   └── analyze.<N-1>/               # analysis output
├── <output_dir>_subset1/             # Phase 8 fixed-mode on rlnRandomSubset=1 → half1
├── <output_dir>_subset2/             # Phase 8 fixed-mode on rlnRandomSubset=2 → half2
├── <template>_training_mask.mrc      # Phase 8: training-loss sphere mask
│
└── m/                                # M refinement (advanced.md)
    ├── <population_name>.population  # MTools create_population output
    └── species/
        └── <species>_<hash>/         # WARP appends an 8-char hash
            ├── <species>.species     # MTools species metadata
            ├── half1.mrc             # input half-map (from fixed-mode subset1)
            ├── half2.mrc             # input half-map (from fixed-mode subset2)
            ├── mask.mrc              # thresholded molecule mask (NOT a sphere)
            ├── <species>_filt.mrc    # filtered consensus map (MCore output)
            ├── <species>_particles.star # refined per-particle poses (MCore output, WARP STAR)
            └── <species>_relion.star    # RELION-style copy made by `dsdsh convert_warp`
                                         #   (required input to ts_export_particles)
```

## Troubleshooting

### OutOfMemoryException
- Use 256GB+ RAM
- All SLURM scripts include `ulimit -v unlimited` to remove virtual memory limits

### CTF estimation fails
- Ensure frame series CTF is done first
- Check images are linked in `warp_frameseries/average/`
- Verify 256GB+ memory available

### Job timeout / killed by SLURM
- **Symptom**: Job ends with `TIMEOUT` status, output stops mid-process, or partially completed files
- **Solution**: Increase time limit in SLURM script:
  ```bash
  #SBATCH --time=48:00:00  # Increase from default 12-24 hours
  ```
- For many tilt series or large datasets:
  - Use 48 hours or more for full pipeline
  - Process in smaller batches
  - Use separate scripts for each phase (export, align, CTF, reconstruct)
