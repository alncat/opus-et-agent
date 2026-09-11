---
name: opus-et-analysis
description: Use when the user has OPUS-ET training output and wants to interpret it: PCA or k-means over the latent codes of an epoch, volumes for cluster centres or principal components, per-state STAR files, pose parsing, the original pixel size, a training command for a new run, or a molecule mask and gold-standard FSC between half maps after Gate 3 or 4. Also use for state-vs-template and per-tomogram state-consistency checks.
---

# opus-et Analysis

Process and analyze cryo-ET training results from opus-et.

## Agent Rules — read before acting

- **Do only what the user asks.** Don't anticipate, extend, or fix unreported issues — even obvious ones. One change at a time.
- **Read before editing.** Always read the current file/script before describing or modifying it. Don't rely on remembered content — the codebase changes.
- **Verify before asserting.** If uncertain how a tool, flag, or script behaves, check (`--help`, API docs, actual output) rather than inferring from naming. Wrong documentation is worse than no documentation.
- **Enumerate scope before acting.** For multi-file changes, use `grep` to find all affected files and confirm scope with the user before making any edits.
- **Show before and after for every edit.** Before modifying a script or config, quote the relevant current lines. After editing, summarize exactly what changed — not just "done."
- **Don't chain changes.** Renaming a variable in one place does NOT mean you should rename it everywhere — confirm scope first.
- **Don't redesign.** Apparent inconsistencies may be intentional. Respect the existing pattern unless the user asks to change it.
- **When in doubt, show the current state and ask.** "Here's what I see. Do you want me to change X, Y, or both?"


## Training Command Generation
`scripts/generate_train_cmd.py --help` generates the training script -- the template it emits is reproduced verbatim in the reference, with every parameter explained; prefer the script to hand-editing. Full text: `references/training_command.md`.

## Helper Scripts

The following helper scripts ship with this skill and are located in the skill's `scripts/` directory (not the project directory):

```
<skill-dir>/scripts/
```

`<skill-dir>` is the directory where this skill is installed. When invoking the scripts from a project working directory, replace `<skill-dir>` with the actual skill path, or copy/symlink the scripts into your project. Examples below use `<skill-dir>` as a placeholder.

### `generate_train_cmd.py`
Generates training commands with automatic pose pkl generation.
```bash
python <skill-dir>/scripts/generate_train_cmd.py [options]
```

### `extract_config.py`
Extracts configuration parameters from config.pkl.
```bash
python <skill-dir>/scripts/extract_config.py config.pkl
```

### `exclude_stars.py`
Checks overlap between two star files based on 3D coordinates.
```bash
python <skill-dir>/scripts/exclude_stars.py <reference.star> <query.star>
```

**Pixel size:** Auto-detected from `config.pkl` in the current directory (`Apix * downfrac`). Falls back to 3.37 Å if no config.pkl is found.

**Distance threshold:** `136/angpix` voxels = **136 Å** (constant physical distance regardless of pixel size).

## Determining the Original Pixel Size

The original data pixel size (needed for `parse_pose_star`, `exclude_stars.py`, etc.) can be found two ways:

**From config.pkl** (recommended):
```python
import pickle
config = pickle.load(open('config.pkl', 'rb'))
original_Apix = config['model_args']['Apix'] * config['dataset_args']['downfrac']
```

**From the star file** (alternative):
Check `_rlnDetectorPixelSize` (column 9) in the star file header — this is the original pixel size of the raw data.

The two values should agree closely. The star file value is the authoritative original; the config.pkl derivation is the training pipeline's record of it.

Use `<skill-dir>/scripts/extract_config.py` to extract config values including `original_Apix`.

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

Use `<skill-dir>/scripts/extract_config.py` to extract these values.

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
Analyse an epoch (PCA + k-means), volumes for k-means centres and principal components, per-cluster STAR files -- the `dsdsh` commands with their positional arguments. Full text: `references/workflows.md`.

## Complete Workflow Example
A full Gate-3 pass end to end. Full text: `references/workflows.md`.

## Directory Structure Convention
`analyze.<epoch>/` layout: `kmeans<K>/`, `pc<i>/`, and where each command writes. Full text: `references/workflows.md`.

## Complete Deformation Workflow Example
The multi-body (`defanalyze`) equivalent of the Gate-3 pass. Full text: `references/workflows.md`.

## Key Differences: analyze vs defanalyze Outputs
What differs between the two output trees. Full text: `references/workflows.md`.

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
                             #   compute_fsc, gen_mask_from_map, state_consistency, state_tomo_stats, tm_eval_agreement}
```

## See Also

- `references/commands.md` - Detailed command reference with all options
- `<skill-dir>/scripts/extract_config.py` - Extract config parameters
- Deformation workflows are documented inline above (see "Complete Deformation Workflow Example" and "Key Differences: analyze vs defanalyze Outputs")
