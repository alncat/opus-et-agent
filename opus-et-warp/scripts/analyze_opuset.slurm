#!/bin/bash
#SBATCH --job-name=analyze_opuset        # Job name
#SBATCH --partition=normal               # GPU partition
#SBATCH --nodes=1                        # Single node
#SBATCH --ntasks=1                       # Single task
#SBATCH --cpus-per-task=8                # CPU cores
#SBATCH --mem=128G                       # Memory
#SBATCH --gres=gpu:1                     # 1 GPU (eval_vol decoder passes)
#SBATCH --time=8:00:00                   # Time limit
#SBATCH --output=logs/analyze_opuset_%j.out   # Standard output
#SBATCH --error=logs/analyze_opuset_%j.err    # Standard error

# =============================================================================
# OPUS-ET Analysis Script  (Gate 3 prep — state selection)
# Latent-space analysis of a trained heterogeneity model, then volume
# reconstruction at k-means centers and along principal components:
#   1) dsdsh analyze  <resdir> <epoch> <numpc> <numk>   -> analyze.<epoch>/ (UMAP/PCA, kmeans<numk>/, pc<i>/)
#   2) dsdsh eval_vol <resdir> <epoch> kmeans <numk> <apix>   -> k-means-center density maps
#   3) dsdsh eval_vol <resdir> <epoch> pc     <npc>  <apix>   -> PC-traversal maps (continuous motion)
#
# NOTE: the installed `dsdsh` CLI uses POSITIONAL args (verified via --help),
# which differs from the older `dsd eval_vol --load --zfile ...` form in some docs.
# =============================================================================

echo "============================================================"
echo "OPUS-ET Analysis Started"
echo "Job ID: $SLURM_JOB_ID"
echo "Node: $SLURMD_NODENAME"
echo "GPUs: $CUDA_VISIBLE_DEVICES"
echo "Start time: $(date)"
echo "============================================================"
echo ""

# -----------------------------------------------------------------------------
# CONFIGURATION — source shared pipeline.conf + species.conf, then override
# -----------------------------------------------------------------------------
SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found. Set SKILL_DIR or copy pipeline.example.conf -> pipeline.conf and edit."; exit 1; }
SPECIES_CONF="${SPECIES_CONF:-${SCRIPT_DIR}/species.conf}"
source "$SPECIES_CONF" 2>/dev/null || { echo "ERROR: $SPECIES_CONF not found. Set SKILL_DIR or copy species.example.conf -> species.conf and edit."; exit 1; }

# Env override (survives species.conf sourcing) — analyze a warm-start / alt dir
# without editing the conf, e.g. OUTPUT_DIR_OVERRIDE=opuset/ribo/z8_expanded.
[ -n "${OUTPUT_DIR_OVERRIDE:-}" ] && OUTPUT_DIR="$OUTPUT_DIR_OVERRIDE"

# Script-specific parameters (override via --export at sbatch time)
ANALYZE_EPOCH="${ANALYZE_EPOCH:-}"       # blank = auto-detect the latest z.N.pkl
ANALYZE_NUMPC="${ANALYZE_NUMPC:-8}"      # PCA components; must be <= zdim
ANALYZE_NUMK="${ANALYZE_NUMK:-20}"       # k-means clusters
ANALYZE_PC_NUM="${ANALYZE_PC_NUM:-3}"    # PCs to reconstruct volumes for (0 = skip PC volumes)
ANALYZE_APIX="${ANALYZE_APIX:-}"         # blank = read original_Apix from config.pkl

# Resolve the training/result directory (same rule as train_opuset.slurm)
if [[ "${OUTPUT_DIR}" = /* ]]; then
    RESDIR="${OUTPUT_DIR}"
else
    RESDIR="${WORK_DIR}/${OUTPUT_DIR}"
fi

# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------
echo "Setting up environment..."
source ~/.bashrc
conda activate "$OPUSET_ENV"
ulimit -v unlimited

cd "$WORK_DIR" || exit 1
echo "Working directory: $(pwd)"
echo "Result directory:  $RESDIR"
echo ""

echo "Checking GPU..."
nvidia-smi
echo ""

# -----------------------------------------------------------------------------
# Validate inputs
# -----------------------------------------------------------------------------
if [ ! -d "$RESDIR" ]; then
    echo "ERROR: result directory not found: $RESDIR"
    exit 1
fi
if [ ! -f "$RESDIR/config.pkl" ]; then
    echo "ERROR: config.pkl not found in $RESDIR (was training completed?)"
    exit 1
fi

# Auto-detect the latest epoch from z.<N>.pkl if not given
if [ -z "$ANALYZE_EPOCH" ]; then
    ANALYZE_EPOCH=$(ls "$RESDIR"/z.*.pkl 2>/dev/null \
        | sed -E 's#.*/z\.([0-9]+)\.pkl#\1#' | sort -n | tail -1)
    if [ -z "$ANALYZE_EPOCH" ]; then
        echo "ERROR: no z.<N>.pkl files in $RESDIR; cannot pick an epoch."
        exit 1
    fi
    echo "Auto-detected latest epoch: $ANALYZE_EPOCH"
fi
for req in "z.${ANALYZE_EPOCH}.pkl" "weights.${ANALYZE_EPOCH}.pkl"; do
    if [ ! -f "$RESDIR/$req" ]; then
        echo "ERROR: required file missing for epoch ${ANALYZE_EPOCH}: $RESDIR/$req"
        exit 1
    fi
done

# Original pixel size from config.pkl (training_Apix * downfrac) unless overridden
if [ -z "$ANALYZE_APIX" ]; then
    ANALYZE_APIX=$(python - "$RESDIR/config.pkl" <<'PY'
import pickle, sys
with open(sys.argv[1], "rb") as f:
    c = pickle.load(f)
apix = c["model_args"]["Apix"] * c["dataset_args"].get("downfrac", 1.0)
print(f"{apix:.6f}")
PY
    )
    if [ -z "$ANALYZE_APIX" ]; then
        echo "ERROR: could not read original_Apix from config.pkl; set ANALYZE_APIX manually."
        exit 1
    fi
    echo "Output pixel size (from config.pkl): $ANALYZE_APIX A"
fi

echo ""
echo "Parameters:"
echo "  - Result dir:   $RESDIR"
echo "  - Epoch:        $ANALYZE_EPOCH"
echo "  - PCA comps:    $ANALYZE_NUMPC"
echo "  - k-means:      $ANALYZE_NUMK"
echo "  - PC volumes:   $ANALYZE_PC_NUM"
echo "  - Output apix:  $ANALYZE_APIX"

# =============================================================================
# Step 1: Latent-space analysis (PCA + k-means + UMAP)
# =============================================================================
echo ""
echo "============================================================"
echo "Step 1: dsdsh analyze"
echo "============================================================"
dsdsh analyze "$RESDIR" "$ANALYZE_EPOCH" "$ANALYZE_NUMPC" "$ANALYZE_NUMK"
if [ $? -ne 0 ]; then
    echo "ERROR: dsdsh analyze failed"
    exit 1
fi

ANALYZE_DIR="$RESDIR/analyze.${ANALYZE_EPOCH}"
echo "Analysis output: $ANALYZE_DIR"

# =============================================================================
# Step 2: Reconstruct k-means-center volumes
# =============================================================================
echo ""
echo "============================================================"
echo "Step 2: dsdsh eval_vol kmeans ($ANALYZE_NUMK centers)"
echo "============================================================"
dsdsh eval_vol "$RESDIR" "$ANALYZE_EPOCH" kmeans "$ANALYZE_NUMK" "$ANALYZE_APIX"
if [ $? -ne 0 ]; then
    echo "WARNING: dsdsh eval_vol kmeans returned non-zero (exit $?)"
fi

# =============================================================================
# Step 3: Reconstruct PC-traversal volumes (continuous motion)
# =============================================================================
if [ "$ANALYZE_PC_NUM" -gt 0 ]; then
    echo ""
    echo "============================================================"
    echo "Step 3: dsdsh eval_vol pc (top $ANALYZE_PC_NUM PCs)"
    echo "============================================================"
    dsdsh eval_vol "$RESDIR" "$ANALYZE_EPOCH" pc "$ANALYZE_PC_NUM" "$ANALYZE_APIX"
    if [ $? -ne 0 ]; then
        echo "WARNING: dsdsh eval_vol pc returned non-zero (exit $?)"
    fi
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "============================================================"
echo "OPUS-ET Analysis Complete"
echo "============================================================"
echo "End time: $(date)"
echo ""
echo "Outputs under: $ANALYZE_DIR"
echo "  - umap.png / umap_hex.png       latent landscape (UMAP)"
echo "  - kmeans${ANALYZE_NUMK}/         labels.pkl, centers.txt, per-cluster maps"
echo "  - pc<i>/                         PC-traversal maps"
echo ""
echo "Next (Gate 3 — state selection):"
echo "  1. Inspect UMAP + k-means-center maps; choose conformational cluster(s)."
echo "  2. Split particles by cluster:"
echo "     dsdsh parse_pose <particles.star> <D> <apix> $RESDIR $ANALYZE_EPOCH $ANALYZE_NUMK"
echo "  3. Combine chosen clusters (dsdsh combine_star) -> sel.star -> fixed-mode + M."
echo "============================================================"
