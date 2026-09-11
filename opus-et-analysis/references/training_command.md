# opus-et-analysis: training command

Moved verbatim from SKILL.md so the skill body stays an overview; this is the reference.

## Training Command Generation

Use the provided script to generate training commands:

```bash
python <skill-dir>/scripts/generate_train_cmd.py [options]
```

### Pose PKL Generation

The pose pickle file can be provided directly or **generated automatically** from the star file:
- If `--poses` is provided, it will be used directly
- If `--poses` is omitted, the script will:
  1. Parse the star file to find the first subtomogram
  2. Read the MRC header to detect the actual box size
  3. Generate pose pkl using `dsd parse_pose_star` with the correct dimensions

### Interactive Mode

Run without arguments to be prompted for all parameters:

```bash
python <skill-dir>/scripts/generate_train_cmd.py
```

### Command-Line Mode

**With existing pose pkl:**
```bash
python <skill-dir>/scripts/generate_train_cmd.py \
    --star ../zribo_test/matching80s.star \
    --poses ../zribo_test/matching80s_pose_euler.pkl \
    --datadir <RUN_DIR>/warp_tiltseries/ \
    --mask-mrc ../zribo_test/mask.mrc \
    --mask-params ../mask_params.pkl \
    --split deep.pkl \
    --tilt-range 50 \
    --tilt-step 2 \
    --angpix <OUTPUT_ANGPIX> \
    --output train.sh
```

**Auto-generate pose pkl from star file:**
```bash
python <skill-dir>/scripts/generate_train_cmd.py \
    --star ../zribo_test/matching80s.star \
    --datadir <RUN_DIR>/warp_tiltseries/ \
    --mask-mrc ../zribo_test/mask.mrc \
    --mask-params ../mask_params.pkl \
    --split deep.pkl \
    --tilt-range 50 \
    --tilt-step 2 \
    --angpix <OUTPUT_ANGPIX> \
    --output train.sh
```

**`--angpix` here must be the subtomogram pixel size (`OUTPUT_ANGPIX` = the
export STAR's `rlnDetectorPixelSize`), NOT the raw tilt-series pixel size
(e.g. 3.37) — OPUS-ET computes the CTF from `--angpix`, and this value also
feeds the pose-pkl `--Apix` below. See memory `opus-et-angpix-ctf`.

The generated script will auto-detect the subtomogram box size and include a section to create the pose pkl:
```bash
# ==============================================================================
# GENERATE POSE PKL FROM STAR FILE
# ==============================================================================

echo "Generating pose pickle from star file..."
dsd parse_pose_star ${STAR_FILE} \
    -D 176 \
    --Apix ${ANGPIX} \
    -o ${POSE_PKL}
```

**Note:** The box size (176 in this example) is automatically detected by reading the first subtomogram's MRC header — this is the **subtomogram box** (`SUBTOMO_BOX_SIZE`), which OPUS-ET's `PoseTracker` uses to scale translations. It is unrelated to `TEMPLATERES` (the decoder output size, set separately below) even though both can coincidentally be powers of two — do not substitute one for the other. You can override the detected box with `--box-size` if needed. `ANGPIX` here must be the subtomogram pixel size (`OUTPUT_ANGPIX`), not the raw tilt-series pixel size (see memory `opus-et-angpix-ctf`).

### Training Command Template

If writing manually, use this template. Note: You can generate `POSE_PKL` from `STAR_FILE` using `dsd parse_pose_star`.

```bash
#!/bin/bash

# ==============================================================================
# EXPERIMENTAL PARAMETERS
# ==============================================================================

STAR_FILE=<path_to_star>           # Particle star file
POSE_PKL=<path_to_pose_pkl>        # Pose pickle file (or generate from star)
DATADIR=<path_to_tilt_series>      # Path to tilt series directory
MASK_MRC=<path_to_mask_mrc>        # Mask mrc file
TILT_RANGE=<tilt_range>            # Maximum tilt angle (degrees)
TILT_STEP=<tilt_step>              # Tilt increment (degrees)
ANGPIX=<OUTPUT_ANGPIX>              # Subtomogram pixel size (OUTPUT_ANGPIX = export STAR's
                                    # rlnDetectorPixelSize); drives the CTF. NOT the raw
                                    # tilt-series ANGPIX (see memory opus-et-angpix-ctf)

# Multi-body deformation (optional)
MASK_PARAMS=<path_to_mask_params>  # Path to mask_params.pkl for multi-body
SPLIT_PKL=<path_to_split>          # Train/val split pickle

# ==============================================================================
# OPTIONAL/TUNABLE PARAMETERS
# ==============================================================================

ZDIM=8                              # Composition latent space dimension (default)
ZAFFINEDIM=4                       # Conformation latent space dimension
# ENCODERRES=13                    # Optional: encoder resolution
NUM_EPOCHS=40
BATCH_SIZE=10
LEARNING_RATE=3.0e-5
BETA_CONTROL=0.5                   # KL divergence weight
LAMB=0.5                           # Structural disentanglement weight
BFACTOR=3.0                        # B-factor sharpening
NUM_GPUS=4
OUTPUT_DIR=.
VAL_FRAC=0.05
TEMPLATERES=128

# ==============================================================================
# TRAINING COMMAND
# ==============================================================================

torchrun --nproc_per_node=${NUM_GPUS} -m cryodrgn.commands.train_tomo_dist \
    ${STAR_FILE} \
    --poses ${POSE_PKL} \
    -n ${NUM_EPOCHS} \
    -b ${BATCH_SIZE} \
    --zdim ${ZDIM} \
    --zaffinedim ${ZAFFINEDIM} \
    --lr ${LEARNING_RATE} \
    --num-gpus ${NUM_GPUS} \
    --multigpu \
    --beta-control ${BETA_CONTROL} \
    -o ${OUTPUT_DIR} \
    -r ${MASK_MRC} \
    --masks ${MASK_PARAMS} \
    --split ${SPLIT_PKL} \
    --lamb ${LAMB} \
    --bfactor ${BFACTOR} \
    --valfrac ${VAL_FRAC} \
    --templateres ${TEMPLATERES} \
    --tmp-prefix tmp \
    --datadir ${DATADIR} \
    --angpix ${ANGPIX} \
    --downfrac 1. \
    --warp \
    --tilt-range ${TILT_RANGE} \
    --tilt-step ${TILT_STEP} \
    --ctfalpha 0. \
    --ctfbeta 1. \
    --estpose
```

**Example values:**
| Parameter | Example Value |
|-----------|---------------|
| `STAR_FILE` | `../zribo_test/matching80s.star` |
| `POSE_PKL` | `../zribo_test/matching80s_pose_euler.pkl` |
| `DATADIR` | `<RUN_DIR>/warp_tiltseries/` |
| `MASK_MRC` | `../zribo_test/mask.mrc` |
| `MASK_PARAMS` | `../mask_params.pkl` |
| `SPLIT_PKL` | `deep.pkl` |
| `TILT_RANGE` | `50` |
| `TILT_STEP` | `2` |
| `ANGPIX` | `<OUTPUT_ANGPIX>` (subtomogram pixel size) |

### Quick Parameter Reference

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| `DATADIR` | Required | Tilt series directory path | `/path/to/tilt_series/` |
| `ANGPIX` | Required | Subtomogram pixel size (Å) — `OUTPUT_ANGPIX`, drives the CTF; NOT the raw tilt-series ANGPIX | `<OUTPUT_ANGPIX>` |
| `TILT_STEP` | Required | Tilt increment (°) | `2` |
| `TILT_RANGE` | Required | Max tilt angle (°) | `50` |
| `--box-size` | Optional | Subtomogram box size (`SUBTOMO_BOX_SIZE`, auto-detected from MRC) | `176` |
| `ZDIM` | Tunable | Composition latent dim | `8` (default) |
| `--zaffinedim` | Tunable | Conformation latent dim | `4` (continuous conformational changes) |
| `BETA_CONTROL` | Tunable | Reconstruction vs KL balance | `0.5-1.0` |
| `LAMB` | Tunable | Disentanglement strength | `0.5-1.0` |
| `BFACTOR` | Tunable | Map sharpening factor | `3.0` |
| `TEMPLATERES` | Tunable | Output box size | `128` |
| `--warp` | Flag | Enable I/O for WarpTools subtomograms | Add when using WarpTools |
| `--masks` | Optional | Path to mask_params.pkl for multi-body deformation | `../mask_params.pkl` |

