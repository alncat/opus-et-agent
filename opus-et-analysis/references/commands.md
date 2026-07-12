# opus-et Commands Reference

## Two CLIs — `dsd` vs `dsdsh`
opusTomo ships two entry points that do overlapping things:

- **`dsdsh <subcommand> ...`** — a shell wrapper with **positional** arguments. This is
  what `analyze_opuset.slurm` and `select_state.slurm` drive, and the forms below marked
  "verified" were run end-to-end on this dataset. Subcommands: `eval_vol analyze parse_pose
  combine_star convert_star convert_warp convert_pytom* convert_artiax create_mask
  extract_tomo_cubes prepare prepare_multi`.
- **`dsd <subcommand> ...`** — the lower-level cryodrgn-style CLI with **flag** arguments
  (`--load`, `--zfile`, `-o`, ...). The training/export scripts use `dsd parse_pose_star
  ... -o` to build the pose pickle.

> **Login-node caveat:** these commands import cryodrgn/torch, which **segfaults on the
> login node** (OpenBLAS `RLIMIT_NPROC` cap). `dsdsh <cmd> --help` works (the wrapper's
> argparse runs before the import), but any real run must go through SLURM on a compute
> node. Use `analyze_opuset.slurm` / `select_state.slurm`, or an `sbatch`/`srun` wrapper.

## Analysis Commands

### dsdsh analyze  (verified)
Run PCA + k-means + UMAP on a trained epoch.

```bash
dsdsh analyze <resdir> <epoch> <numpc> <numk>
```

- `resdir` — training dir with `weights.N.pkl`, `z.N.pkl`, `config.pkl`
- `epoch` — epoch index of the pkl to analyze (e.g. `39` = the 40th, 0-based file index)
- `numpc` — number of PCA components (**must be ≤ `zdim`**)
- `numk` — number of k-means clusters

**Output:** `analyze.<epoch>/` with `kmeans<numk>/` (`labels.pkl`, `centers.txt`,
`pre<k>.star` after `parse_pose`), `pc<i>/` trajectory z-values, and `umap*.png` / `z_pca*.png`.

### dsdsh eval_vol  (verified — POSITIONAL args)
Reconstruct real-space volumes from the analysis. Reads the centers/trajectories that
`dsdsh analyze` wrote — no manual `--zfile` needed.

```bash
dsdsh eval_vol <resdir> <epoch> {kmeans,pc,dpc,joint} <num> <apix>
```

- k-means-center maps: `dsdsh eval_vol . 39 kmeans 20 3.37` → `analyze.39/kmeans20/reference<k>.mrc`
- PC-traversal maps:   `dsdsh eval_vol . 39 pc 3 3.37`      → `analyze.39/pc<i>/`
- `apix` = the **original** pixel size (`model_args.Apix * dataset_args.downfrac`).

Low-level equivalent (builds the z-file paths yourself):
```bash
dsd eval_vol --load weights.<epoch>.pkl -c config.pkl -o <outdir> \
    --zfile analyze.<epoch>/kmeans<numk>/centers.txt --Apix <apix> --prefix kmeans
```

### dsdsh parse_pose  (verified — split a star by cluster)
Split the training star into one star per k-means cluster (for state selection).

```bash
dsdsh parse_pose [--relion31] <starfile> <D> <apix> <resdir> <epoch> <numk>
```

- `D` = `effective_box_size` = `lattice_args['D'] - 1`; `apix` = original pixel size.
- `--relion31` only if the star has a `data_optics` block (RELION 3.1).
- Wraps `cryodrgn.commands.parse_pose_star ... --labels analyze.<epoch>/kmeans<numk>/labels.pkl
  --outdir analyze.<epoch>/kmeans<numk>/`, writing `pre0.star … pre<numk-1>.star`.

### dsd parse_pose_star -o  (pose pickle, Mode 2 — used by the training/export scripts)
Generate a single pose `.pkl` from a star (needed before training / M on a combined star):

```bash
dsd parse_pose_star <starfile> -D <box_size> --Apix <apix> -o <output_pose.pkl>
```

### dsd parse_pose_star --poses  (Mode 3 — inject OPUS-ET-refined poses into a star)
OPUS-ET refines poses during training (`estpose`), saved as `pose.<epoch>.pkl` in the result
dir. Write those refined eulers + translations back into a star so downstream (fixed-mode, M)
uses the refined poses instead of the original template-matching poses:

```bash
dsd parse_pose_star <starfile> -D <SUBTOMO_BOX_SIZE> --Apix <subtomo_apix> \
    --poses <resdir>/pose.<epoch>.pkl --out-star <refined.star>
```

`-D` must be the **subtomogram box size** (`SUBTOMO_BOX_SIZE` = lattice `D`−1) and `--Apix` the
**subtomogram pixel size** (`OUTPUT_ANGPIX`). The pkl stores refined translations as fractions
of the subtomo box, scaled to pixels by `-D` (OPUS-ET's `PoseTracker` uses box = `D`−1). Row
order is preserved, so k-means `labels.pkl` still aligns → split the refined star by cluster and
the per-cluster stars inherit the refined poses. `select_state.slurm` does this automatically
(Step 0) when `pose.<epoch>.pkl` exists (`USE_REFINED_POSES=1`, default).

> The training scripts (`generate_train_cmd.py`, `train_opuset*.slurm`) generate the *initial*
> pose pkl with `-D SUBTOMO_BOX_SIZE` (the lattice box D−1, e.g. 176), **not** `TEMPLATERES` (the
> decoder output size, 128). Template matching gives **zero** initial translations, so the `-D`
> value would be immaterial for the *initial* pkl anyway; but the *refined* translations are
> non-zero, so the injection `-D` MUST be the subtomo box — never `TEMPLATERES`.

### dsdsh combine_star  (verified)
Combine two star files. Chain for more than two.

```bash
dsdsh combine_star <starfile1> <starfile2> <output.star>
# chain: a+b -> tmp, tmp+c -> out
```

## Config values (`config.pkl`)
Read with `python scripts/extract_config.py config.pkl`:

- `model_args.Apix` — training-effective pixel size (NOT the original)
- `dataset_args.downfrac` — downsampling fraction
- **`original_Apix` = `Apix * downfrac`** — use for `eval_vol` / `parse_pose` / all original-data ops
- `lattice_args.D` — lattice size; **`effective_box_size` = `D - 1`** (use for `-D`)
- `dataset_args.particles` — original star path; `dataset_args.poses` — pose pkl path
- `model_args.zdim` — latent dimension (caps `numpc`)

`original_Apix` can also be read from the star's `_rlnDetectorPixelSize` column.

## Common Analysis Workflow (Gate 3 — state selection)

1. **Extract config:** `python scripts/extract_config.py config.pkl`
   → note `original_Apix` and `effective_box_size`.
2. **Analyze + reconstruct (one job):** `sbatch opus-et-warp/scripts/analyze_opuset.slurm`
   — runs `dsdsh analyze . 39 <numpc> <numk>` then `dsdsh eval_vol` for k-means (and PC) maps.
3. **Identify real states:** `python scripts/compare_to_template.py --maps
   'analyze.39/kmeans20/reference*.mrc' --template <ref.mrc> --labels
   analyze.39/kmeans20/labels.pkl -o state_vs_template` — masked CC to template + consensus.
   Combine with a 3D view of the maps (ChimeraX) — template-CC rejects junk, but map
   **resolution/detail** distinguishes the *best* state (a low-res template rewards smooth blobs).
4. **Select clusters → sel.star (one job):** `sbatch --export=ALL,SELECT_CLUSTERS="8 9 10 11"
   opus-et-warp/scripts/select_state.slurm` — runs `dsdsh parse_pose` then chains
   `dsdsh combine_star` over the chosen `pre<k>.star`.
5. **Pose pickle for the selection (before M / fixed-mode):**
   `dsd parse_pose_star sel.star -D <effective_box_size> --Apix <original_apix> -o sel_pose.pkl`
