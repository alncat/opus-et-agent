#!/bin/bash
#SBATCH --job-name=warp_ts_ctf           # Job name
#SBATCH --partition=normal               # GPU partition
#SBATCH --nodes=1                        # Number of nodes
#SBATCH --ntasks=1                       # Number of tasks
#SBATCH --cpus-per-task=8                # CPU cores per task
#SBATCH --mem=256G                       # Memory per node (256GB recommended)
#SBATCH --gres=gpu:4                     # Number of GPUs
#SBATCH --time=12:00:00                  # Time limit (12 hours)
#SBATCH --output=logs/warp_ts_ctf_%j.out      # Standard output log
#SBATCH --error=logs/warp_ts_ctf_%j.err       # Standard error log

# =============================================================================
# WARP Tilt Series Defocus Handedness + CTF Estimation Script
# Requires completed frame-series local CTF (2x2x1 grid).
# Steps: Check Defocus Handedness → Set Handedness → Tilt-Series CTF
# =============================================================================

echo "============================================================"
echo "WARP Tilt Series CTF Estimation Started"
echo "Job ID: $SLURM_JOB_ID"
echo "Node: $SLURMD_NODENAME"
echo "GPUs: $CUDA_VISIBLE_DEVICES"
echo "Start time: $(date)"
echo "============================================================"

# -----------------------------------------------------------------------------
# CONFIGURATION — source shared pipeline.conf, then override per-script values
# -----------------------------------------------------------------------------
SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found. Set SKILL_DIR or copy pipeline.example.conf -> pipeline.conf and edit."; exit 1; }

# Script-specific overrides (uncomment to override pipeline.conf defaults)
PERDEVICE=1                              # Processes per GPU (reduce if OOM)

# -----------------------------------------------------------------------------
# Environment Setup
# -----------------------------------------------------------------------------
echo ""

echo "Setting up environment..."

source ~/.bashrc
conda activate "$WARP_ENV" || exit 1
ulimit -v unlimited

export LD_LIBRARY_PATH="${CONDA_LIB}:${WARP_DIR}:$LD_LIBRARY_PATH"
export PATH="${WARP_DIR}:$PATH"

cd "$WORK_DIR" || exit 1
echo "Working directory: $(pwd)"

# Verify GPU availability
echo ""
echo "Checking GPU availability..."
nvidia-smi

# Verify WARP
echo ""
echo "Verifying WARP installation..."
"${WARP_DIR}/WarpTools"

# Check settings file exists
if [ ! -f "$TILTSERIES_SETTINGS" ]; then
    echo "ERROR: Settings file not found: $TILTSERIES_SETTINGS"
    exit 1
fi

# =============================================================================
# Step 1: Check Defocus Handedness
# =============================================================================
echo ""
echo "============================================================"
echo "Step 1: Check Defocus Handedness"
echo "============================================================"

# Check sampling before modifying metadata or launching GPU fitting.
python "${SCRIPT_DIR}/scripts/warp_settings.py" contract \
    "${FRAMESERIES_DIR}.settings" "$ANGPIX" "$BINNING_FACTOR" "$ALIGN_ANGPIX" \
    --settings "$TILTSERIES_SETTINGS" || exit 1
if ! [[ "${CTF_VOLTAGE:-}" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: Set CTF_VOLTAGE explicitly in pipeline.conf (e.g. 200 or 300 kV)."
    exit 1
fi
# The hand check reads the frame-series CTF grids, so they must have been fitted
# at the microscope voltage too, not WARP's 300 kV default.
python "${SCRIPT_DIR}/scripts/warp_settings.py" check-voltage \
    "${FRAMESERIES_DIR}.settings" "$CTF_VOLTAGE" || {
    echo "ERROR: Rerun frame-series CTF with CTF_VOLTAGE=$CTF_VOLTAGE before choosing a hand."
    exit 1
}

# auto uses local FRAME CTF grids, not the later tilt-series CTF fit.
# keep preserves a deliberately selected geometry. No fallback guesses.
case "${CTF_HAND:-auto}" in
    auto)
        if CHECK_OUTPUT=$("${WARP_DIR}/WarpTools" ts_defocus_hand \
            --settings "$TILTSERIES_SETTINGS" --check 2>&1); then
            echo "$CHECK_OUTPUT"
        else
            CHECK_EXIT_CODE=$?
            echo "$CHECK_OUTPUT"
            echo "ERROR: Handedness check failed (exit $CHECK_EXIT_CODE); no handedness change or CTF fit performed."
            echo "Check frame-series local CTF and invalid tilt-series geometry (e.g. NaN alignments)."
            echo "Deselect the invalid series in a separate processing tree, then rerun; or use CTF_HAND=keep/flip/noflip only after choosing the hand deliberately."
            exit 1
        fi
        if echo "$CHECK_OUTPUT" | grep -Eqi '(^|[^[:alnum:]_])(NaN|Infinity|Exception)([^[:alnum:]_]|$)|[1-9][0-9]* failed'; then
            echo "ERROR: Handedness output reports invalid data or failures; stopping."
            echo "Inspect the failed series (tomo44 has one tilt here). Deselect it, then rerun auto; or set CTF_HAND deliberately."
            exit 1
        fi
        if echo "$CHECK_OUTPUT" | grep -q "average correlation is negative" &&
           ! echo "$CHECK_OUTPUT" | grep -q "average correlation is positive"; then
            FLIP_NEEDED=1
        elif echo "$CHECK_OUTPUT" | grep -q "average correlation is positive" &&
             ! echo "$CHECK_OUTPUT" | grep -q "average correlation is negative"; then
            FLIP_NEEDED=0
        else
            echo "ERROR: Handedness check is inconclusive; no automatic flip will be applied."
            exit 1
        fi
        ;;
    flip) FLIP_NEEDED=1 ;;
    noflip) FLIP_NEEDED=0 ;;
    keep) FLIP_NEEDED=keep ;;
    *) echo "ERROR: CTF_HAND must be auto, keep, flip, or noflip"; exit 1 ;;
esac

# =============================================================================
# Step 2: Set Correct Defocus Handedness
# =============================================================================
echo ""
echo "============================================================"
echo "Step 2: Set Defocus Handedness"
echo "============================================================"

if [ "$FLIP_NEEDED" = keep ]; then
    echo "Preserving existing defocus handedness (CTF_HAND=keep)"
elif [ "$FLIP_NEEDED" = 1 ]; then
    echo "Setting defocus handedness to: FLIP"
    "${WARP_DIR}/WarpTools" ts_defocus_hand \
    --settings "$TILTSERIES_SETTINGS" \
    --set_flip
else
    echo "Setting defocus handedness to: NO FLIP"
    "${WARP_DIR}/WarpTools" ts_defocus_hand \
    --settings "$TILTSERIES_SETTINGS" \
    --set_noflip
fi

if [ $? -ne 0 ]; then
    echo "ERROR: Failed to set defocus handedness"
    exit 1
fi

# Verify what WARP actually wrote; keep still requires one hand for every series.
case "$FLIP_NEEDED" in
    keep) HAND_EXPECTED=uniform ;;
    1) HAND_EXPECTED=flip ;;
    *) HAND_EXPECTED=noflip ;;
esac
HAND_RECORDED=$(python "${SCRIPT_DIR}/scripts/warp_settings.py" check-hand \
    "$TILTSERIES_SETTINGS" "$HAND_EXPECTED") || exit 1
echo "Recorded AreAnglesInverted=$HAND_RECORDED for every selected tilt series"

# =============================================================================
# Step 3: CTF Estimation on Tilt Series
# =============================================================================
echo ""
echo "============================================================"
echo "Step 3: CTF Estimation on Tilt Series"
echo "============================================================"

echo ""
echo "CTF parameters:"
echo "  - Voltage: $CTF_VOLTAGE kV; Cs: ${CTF_CS:-2.7} mm"
echo "  - Window size: $CTF_WINDOW"
echo "  - Max resolution: $CTF_RANGE_MAX A"
echo "  - Max defocus: $CTF_DEFOCUS_MAX um"
echo "  - Processes per GPU: $PERDEVICE"

"${WARP_DIR}/WarpTools" ts_ctf \
--settings "$TILTSERIES_SETTINGS" \
--voltage "$CTF_VOLTAGE" \
--cs "${CTF_CS:-2.7}" \
--window "$CTF_WINDOW" \
--range_high "$CTF_RANGE_MAX" \
--defocus_max "$CTF_DEFOCUS_MAX" \
--perdevice "$PERDEVICE"

CTF_EXIT_CODE=$?

if [ $CTF_EXIT_CODE -ne 0 ]; then
    echo ""
    echo "ERROR: CTF estimation failed (Exit code: $CTF_EXIT_CODE)"
    echo ""
    echo "Common issues:"
    echo "  - Frame series CTF not completed first"
    echo "  - Images not linked in warp_frameseries/average/"
    echo "  - Out of memory (try reducing --perdevice)"
    exit 1
fi

python "${SCRIPT_DIR}/scripts/warp_settings.py" check-voltage \
    "$TILTSERIES_SETTINGS" "$CTF_VOLTAGE" || exit 1

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "============================================================"
echo "Tilt Series CTF Estimation Complete"
echo "============================================================"
echo "End time: $(date)"
echo ""
echo "Settings applied:"
if [ "$FLIP_NEEDED" = keep ]; then
    echo "Preserving existing defocus handedness (CTF_HAND=keep)"
elif [ "$FLIP_NEEDED" = 1 ]; then
    echo "  - Defocus handedness: FLIP"
else
    echo "  - Defocus handedness: NO FLIP"
fi
echo ""
echo "CTF parameters:"
echo "  - Voltage: $CTF_VOLTAGE kV; Cs: ${CTF_CS:-2.7} mm"
echo "  - Window: $CTF_WINDOW"
echo "  - Max resolution: $CTF_RANGE_MAX A"
echo "  - Max defocus: $CTF_DEFOCUS_MAX um"
echo ""
echo "Output locations:"
echo "  - CTF XML files: warp_tiltseries/*.xml"
echo "  - Power spectra: warp_tiltseries/powerspectrum/"
echo ""
echo "Next step:"
echo "  Reconstruct tomograms:"
echo "  sbatch scripts/warp_ts_reconstruct.slurm"
echo "============================================================"
