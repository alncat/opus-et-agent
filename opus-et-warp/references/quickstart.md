# opus-et-warp: quickstart

Moved verbatim from SKILL.md so the skill body stays an overview; this is the reference.

## Quick Start

1. **Copy examples:** `cp pipeline.example.conf pipeline.conf && cp species.example.conf species.conf`
2. **Edit `pipeline.conf` and `species.conf`** — fill all variables (paths, pixel sizes, labels, etc.)
3. **Run `bash validate.sh --phase 1`** — checks placeholders, paths, tools, derived sanity
4. **Submit Phase 1:** `sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_frameseries_import.slurm`

### Variables Reference

All variables are defined in `pipeline.example.conf` and `species.example.conf` — those files are the canonical reference. Copy them to `.conf`, edit, then run `validate.sh` to check them. Derived values (`ALIGN_ANGPIX`, directory paths, etc.) are computed automatically when the configs are sourced.

### Configuration (pipeline.conf + species.conf)

**Two config files** — copy from examples, then edit:

```bash
cp pipeline.example.conf pipeline.conf
cp species.example.conf species.conf
vim pipeline.conf    # tilt-series-wide: paths, microscope, detector, cluster, binning
vim species.conf     # per-species: TM_LABEL, DIAMETER, box sizes, training params
```

`pipeline.conf` is shared by all species. `species.conf` is per-species — keep separate copies for different species (e.g. `species_ribo.conf`, `species_26s.conf`). Override via `SPECIES_CONF=species_26s.conf sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/...`. 

**SLURM copies scripts to a temp directory, so `$0` is not the original path.** Always submit with `SKILL_DIR` so scripts can find their config files:

```bash
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_frameseries_import.slurm
```

In scripts, `SCRIPT_DIR` falls back to `$0` if `SKILL_DIR` is unset:

```bash
SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")" && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found..."; exit 1; }
SPECIES_CONF="${SPECIES_CONF:-${SCRIPT_DIR}/species.conf}"
source "$SPECIES_CONF" 2>/dev/null || { echo "ERROR: $SPECIES_CONF not found..."; exit 1; }
```

### Pre-flight Validation (validate.sh)

**Run before submitting any job:**
```bash
bash validate.sh                        # full check
bash validate.sh --phase 1              # Phase 1 only
bash validate.sh --phase 6 --species species_ribo.conf  # Phase 6, specific species
bash validate.sh --phase 7 --dry-run    # resolve paths + print commands
```

All pre-submission checks are handled by `validate.sh`:
- Unresolved placeholders in `pipeline.conf`, `species.conf`, and all SLURM scripts
- Environment paths (`WORK_DIR`, `WarpTools`, `CONDA_LIB`)
- Conda environments (`WARP_ENV`, `OPUSET_ENV`, `PYTOM_ENV`)
- Required tools (AreTomo2, dsdsh, etc.) — phase-aware
- Derived value sanity (CTF range ≥ 2×ANGPIX, box covers particle, mask fits template)
- Per-tomostar completion checks for the current phase
- SLURM script syntax (`bash -n`)
- `--dry-run` prints resolved paths and commands without submitting

### WARP Environment Notes

> ⚠ **`ulimit -v unlimited` is mandatory.** WARP, MTools, MCore, and AreTomo allocate very large virtual address spaces. Without it they fail at startup with cryptic errors. Every shipped SLURM script already includes this line.

**`WARP_FORCE_MRC_FLOAT32=1`** forces 32-bit float MRC outputs (set in every shipped script). Disk-space tradeoff: 32-bit floats ~2× the size of WARP's compact int16 default. Drop the export if downstream tools handle compressed format and disk is tight.

### Full Pipeline (SLURM)
```bash
# All sbatch commands must pass SKILL_DIR so scripts find pipeline.conf:
#   sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/...

# Phase 1: Frame series
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_frameseries_import.slurm

# Phase 2: Tilt series setup
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_tiltseries_setup.slurm

# Phase 3: Export stacks + AreTomo2 alignment
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_export_stacks.slurm
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_aretomo_align_negate.slurm

# Phase 3.5: REQUIRED — update TOMO_DIMS from AreTomo output
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_update_tomo_dims.slurm

# Phase 4: Import alignments
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_import_alignments.slurm

# Phase 5: CTF + reconstruction
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_ts_ctf.slurm
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_ts_reconstruct.slurm

# 💡 Verify Phase 3–5 alignment chain: AreTomo vs WARP reconstruction dimensions
#    Both are at ALIGN_ANGPIX, so nx × ny × nz must match exactly:
#
#      TS=TS_026
#      headerPyTom warp_tiltseries/tiltstack/$TS/${TS}_ali.mrc | grep 'Number of columns'
#      headerPyTom warp_tiltseries/reconstruction/${TS}_*Apx.mrc | grep 'Number of columns'
#
#    Mismatch → Phase 3.5 skipped, or --alignment_angpix wrong in Phase 4,
#    or TOMO_DIMS X/Y incorrect. Fix the source, re-run from Phase 3.5/4.
#    Details: references/phases.md § sanity check.

# Phase 6: Template matching
# All shared variables are already in pipeline.conf + species.conf.
# Scripts source both automatically — no per-script CONFIG editing needed.
# Agent MUST fill these in species.conf before submitting:
#   TM_LABEL, DIAMETER, TM_BOX_SIZE, SUBTOMO_BOX_SIZE
#   INPUT_MRC, MAP_ANGPIX (template source)
#   NUM_CANDIDATES, EXTRACT_MASK_RADIUS, TEMPLATE (extraction)
# Agent MUST fill these in pipeline.conf:
#   ANGPIX, BINNING_FACTOR (→ ALIGN_ANGPIX derived automatically)
# Script-specific overrides (edit only if defaults don't fit):
#   gen_tm_jobs_aretomo.slurm: SEARCH_REGION=""  (full X/Y, z_start=20)
#   extract_tm_candidates_parallel.slurm: EXTRACT_MASK_RADIUS=14  (non-max suppression)
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/gen_template_from_mrc.slurm
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/gen_sphere_mask.slurm
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/gen_tm_jobs_aretomo.slurm
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/run_tm_sequential.slurm
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/extract_tm_candidates_parallel.slurm
# PyTom XML → per-tilt-series STAR. Canonical path: two SLURM steps below.
# Do not use a combined particles.star here.
# convert_to_star.slurm and convert_pytom_to_warp.slurm must use the same
# TM_LABEL, so they read/write template_matching/$TM_LABEL/star_files and warp_star.
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/convert_to_star.slurm
# convert_to_star.slurm sources pipeline.conf for ANGPIX (unbinned) and BINNING_FACTOR.
# These map to convert.py's --pixelSize and --binPyTom. Verify both are set in pipeline.conf.
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/convert_pytom_to_warp.slurm

# Phase 7: Export subtomograms
# All variables are in pipeline.conf + species.conf — no per-script editing needed.
# Verify in species.conf: TM_LABEL, SUBTOMO_BOX_SIZE, DIAMETER, OUTPUT_ANGPIX
# Verify in pipeline.conf: COORDS_ANGPIX (= ANGPIX)
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_export_particles.slurm

# Phase 8: OPUS-ET training (sub-phases: 8a mask → 8b grad → 8c fixed)
# All variables are in pipeline.conf + species.conf — no per-script editing needed.
# Verify in species.conf: TM_LABEL, ZDIM, NUM_EPOCHS, BATCH_SIZE,
#   SUBTOMO_BOX_SIZE, TEMPLATERES, MASK_SOFT_EDGE, MASK_RADIUS_FRACTION
# Verify in pipeline.conf: NUM_GPUS
# For fixed-mode (Phase 8c): run dsdsh convert_star --subset-label 1 on the Phase 7 STAR first.
# Phase 8b auto-creates a fallback sphere mask if TRAINING_MASK_MRC is missing.
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/gen_training_mask.slurm  # Phase 8a — optional explicit mask
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/train_opuset.slurm        # Phase 8b — heterogeneity
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/train_opuset_fixed.slurm   # Phase 8c — fixed half-maps
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/prepare_m_halfmaps.slurm    # Phase 8d — link half-maps + mask
```

**For detailed per-phase commands:** read `references/phases.md`

---

