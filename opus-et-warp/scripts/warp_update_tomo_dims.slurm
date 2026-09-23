#!/bin/bash
#SBATCH --job-name=warp_update_dims        # Job name
#SBATCH --partition=normal                # CPU partition
#SBATCH --nodes=1                         # Number of nodes
#SBATCH --ntasks=1                        # Number of tasks
#SBATCH --cpus-per-task=2                 # CPU cores per task
#SBATCH --mem=16G                         # Memory per node
#SBATCH --time=00:30:00                   # Time limit (30 minutes)
#SBATCH --output=logs/warp_update_dims_%j.out  # Standard output log
#SBATCH --error=logs/warp_update_dims_%j.err   # Standard error log

# =============================================================================
# Update warp_tiltseries.settings with UNBINNED tomo dimensions
# derived from AreTomo's _ali.mrc output.
#
# MUST run AFTER Phase 3 (AreTomo alignment) and BEFORE Phase 4
# (ts_import_alignments). If TOMO_DIMS in settings is wrong, Phase 4
# imports correctly but later reconstruction / particle export use wrong
# coordinates.
# =============================================================================

# -----------------------------------------------------------------------------
# CONFIGURATION — source shared pipeline.conf, then override per-script values
# -----------------------------------------------------------------------------
SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found. Set SKILL_DIR or copy pipeline.example.conf -> pipeline.conf and edit."; exit 1; }

# Script-specific overrides (uncomment to override pipeline.conf defaults)

# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------
source ~/.bashrc
cd "$WORK_DIR" || exit 1

# -----------------------------------------------------------------------------
# Read every AreTomo volume header. The _ali.mrc may be further binned
# relative to the tilt stack (this project has 9.476 A _ali files from a
# 4.738 A alignment stack; -OutBin 1 itself means no extra binning).
# Validate the actual voxel size before changing any settings.
# -----------------------------------------------------------------------------
conda activate "$PYTOM_ENV" || exit 1
ali_files=()
while IFS= read -r -d '' ali_file; do
    ali_files+=("$ali_file")
done < <(find "$TILTSTACK_DIR" -name "*_ali.mrc" -type f -print0)
if [ "${#ali_files[@]}" -eq 0 ]; then
    echo "ERROR: No *_ali.mrc under $TILTSTACK_DIR; run AreTomo first."
    exit 1
fi
python "${SCRIPT_DIR}/scripts/warp_settings.py" contract \
    "${FRAMESERIES_DIR}.settings" "$ANGPIX" "$BINNING_FACTOR" "$ALIGN_ANGPIX" \
    --settings "$TILTSERIES_SETTINGS" || exit 1
python "${SCRIPT_DIR}/scripts/warp_settings.py" update-from-mrc \
    "$TILTSERIES_SETTINGS" "${FRAMESERIES_DIR}.settings" \
    "$ALIGN_ANGPIX" "${ali_files[@]}" || exit 1

echo "You may now run Phase 4: sbatch scripts/warp_import_alignments.slurm"
