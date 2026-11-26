---
name: opus-et-analysis
description: "Use when the user has OPUS-ET training output and wants to interpret it: PCA or k-means over the latent codes of an epoch, volumes for cluster centres or principal components, per-state STAR files, pose parsing, the original pixel size, a training command for a new run, or a molecule mask and gold-standard FSC between half maps after Gate 3 or 4. Also use for state-vs-template and per-tomogram state-consistency checks."
---

# opus-et Analysis

Process and analyze cryo-ET training results from opus-et.

> Shared agent rules live in [AGENTS.md](../opus-et-conductor/references/AGENTS.md).


## Training Command Generation
Run `python ./scripts/generate_train_cmd.py --help` ([generate_train_cmd.py](./scripts/generate_train_cmd.py)) for the required arguments, then invoke it to generate a training script. Prefer the generator to hand-editing; the reference explains its options. Full text: [training_command.md](./references/training_command.md).

## Helper Scripts

All helper scripts ship under `./scripts/` inside this skill directory (when invoking from a project working directory, pass the resolved skill path):

### [generate_train_cmd.py](./scripts/generate_train_cmd.py)
Generates training commands with automatic pose pkl generation.
```bash
python ./scripts/generate_train_cmd.py [options]
```

### [extract_config.py](./scripts/extract_config.py)
Extracts configuration parameters from `config.pkl`.
```bash
python ./scripts/extract_config.py config.pkl
```

### [exclude_stars.py](./scripts/exclude_stars.py)
Checks overlap between two star files based on 3D coordinates.
```bash
python ./scripts/exclude_stars.py <reference.star> <query.star>
```

**Pixel size:** Supply `--angpix`, or load `Apix * downfrac` from `--config` (default: `config.pkl` in the current directory). Missing or invalid sampling stops the command; it never guesses a pixel size.

**Distance threshold:** `--distance` sets the physical distance in Å (default: **136 Å**), converted to voxels using the pixel size.

## Determining the Original Pixel Size

The original data pixel size (needed for `parse_pose_star`, `exclude_stars.py`, etc.) can be found two ways:

**From config.pkl** (recommended):
```python
import pickle
config = pickle.load(open('config.pkl', 'rb'))
original_Apix = config['model_args']['Apix'] * config['dataset_args']['downfrac']
```

**From the star file** (alternative):
Check `_rlnDetectorPixelSize` (look up the named column) in the star file header — this is the exported subtomogram pixel size.

The two values should agree closely. The star file value is the authoritative original; the config.pkl derivation is the training pipeline's record of it.

Use [extract_config.py](./scripts/extract_config.py) to extract config values including `original_Apix`.

## Quick Reference

Key parameters from `config.pkl`:
- `Apix = config['model_args']['Apix']` (training-effective pixel size)
- `downfrac = config['dataset_args']['downfrac']` (downsampling fraction)
- `original_Apix = Apix * downfrac` (original data pixel size — use for `parse_pose_star` and other original-data operations)
- `D = config['lattice_args']['D'] - 1` (effective box size is lattice D minus 1)
- `particles = config['dataset_args']['particles']` (original star file)

**Important:** The `Apix` stored in `config.pkl` is the training-effective pixel size. For operations on the original star file (e.g., `dsd parse_pose_star`), multiply by `downfrac` to get the original data pixel size:
```python
import pickle
config = pickle.load(open('config.pkl', 'rb'))
apix = config['model_args']['Apix'] * config['dataset_args']['downfrac']
```

Use [extract_config.py](./scripts/extract_config.py) to extract these values.

## Multi-Body Training

To enable multi-body deformation modeling, add `--masks <path_to_mask_params.pkl>` to the training command. The `mask_params.pkl` file contains rigid body definitions:

| Key | Description |
|-----|-------------|
| `com_bodies` | Centers of mass for each rigid body (shape: `[num_bodies, 3]`) |
| `principal_axes` | Principal axes defining body orientations |
| `orient_bodies` | Body orientation matrices |
| `rotate_directions` | Allowed rotation directions for each body |
| `in_relatives` | Rotation reference body index - body *i* rotates relative to body `in_relatives[i]` |
| `radii_bodies` | Radii for each body |

### Parameter Clarification

- **`ZDIM`**: Composition latent space dimension - captures structural/compositional heterogeneity
- **`--zaffinedim`**: Conformation latent space dimension - captures continuous conformational changes (independent of deformation modeling)
- **`--masks`**: Enables rigid body deformation modeling using the body definitions in `mask_params.pkl`

These three mechanisms operate independently:
- **Composition** (`ZDIM`): Discrete structural states
- **Conformation** (`--zaffinedim`): Continuous flexible motions  
- **Deformation** (`--masks`): Rigid body motions between defined bodies

## Common Workflows
Analyse an epoch (PCA + k-means), volumes for k-means centres and principal components, per-cluster STAR files -- the `dsdsh` commands with their positional arguments. Full text: [workflows.md](./references/workflows.md).

## Complete Workflow Example
A full Gate-3 pass end to end. Full text: [workflows.md](./references/workflows.md).

## Directory Structure Convention
`analyze.<epoch>/` layout: `kmeans<K>/`, `pc<i>/`, and where each command writes. Full text: [workflows.md](./references/workflows.md).

## Complete Deformation Workflow Example
The multi-body (`defanalyze`) equivalent of the Gate-3 pass. Full text: [workflows.md](./references/workflows.md).

## Key Differences: analyze vs defanalyze Outputs
What differs between the two output trees. Full text: [workflows.md](./references/workflows.md).

## Files in this skill
```
scripts/                     # all CLIs unless noted
  compare_to_template.py     # masked CC of each state map vs a reference/template (Gate-3 signal)
  state_consistency.py       # template-free N×N map-to-map consistency heatmap (Gate-3 signal)
  state_tomo_stats.py        # per-tomogram particle counts for the selected clusters (Gate-3)
  compute_fsc.py             # gold-standard FSC + phase-randomization correction (Gate-4)
  gen_mask_from_map.py       # molecule mask from a density map (Gate-4 half-map mask)
  tm_eval_agreement.py       # numeric pick-agreement metric (Gate-2)
  generate_train_cmd.py      # build the OPUS-ET training command from config
  extract_config.py          # extract config parameters
  exclude_stars.py           # CLI — overlap of two stars by 3D coords (136 Å); writes <query>_exclude.star
references/
  commands.md                # detailed dsdsh command reference
  training_command.md        # the training-script template generate_train_cmd.py emits, parameter by parameter
  workflows.md               # Gate-3 workflows, analyze.<epoch>/ layout, multi-body (defanalyze) variant
tests/                       # pytest — one per numerical script: test_{compare_to_template,
                             #   compute_fsc, gen_mask_from_map, state_consistency, state_tomo_stats, tm_eval_agreement, exclude_stars}
```

## See Also

- [commands.md](./references/commands.md) - Command reference and QC helper discovery (`--help` lists current options)
- [extract_config.py](./scripts/extract_config.py) - Extract config parameters
- Deformation workflows are documented in [workflows.md](./references/workflows.md)
