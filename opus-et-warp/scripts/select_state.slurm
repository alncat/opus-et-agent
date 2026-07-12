#!/bin/bash
#SBATCH --job-name=select_state          # Job name
#SBATCH --partition=normal               # CPU partition
#SBATCH --nodes=1                        # Single node
#SBATCH --ntasks=1                       # Single task
#SBATCH --cpus-per-task=4                # CPU cores
#SBATCH --mem=32G                        # Memory
#SBATCH --time=0:30:00                   # Time limit
#SBATCH --output=logs/select_state_%j.out    # Standard output
#SBATCH --error=logs/select_state_%j.err     # Standard error

# =============================================================================
# OPUS-ET State Selection  (Gate 3 output)
# Split the training star into per-k-means-cluster stars and combine the human-
# chosen clusters into a single selection star for downstream fixed-mode / M:
#   1) dsdsh parse_pose <star> <D> <apix> <resdir> <epoch> <numk>  -> pre<k>.star
#   2) chain dsdsh combine_star over the chosen pre<k>.star        -> <OUT_STAR>
#
# The chosen clusters come from Gate 3 (inspect analyze.<epoch>/kmeans<numk>/ maps +
# compare_to_template.py). Runs on a compute node: dsdsh imports torch/cryodrgn, which
# segfaults under the login node's OpenBLAS RLIMIT_NPROC cap.
#
# Required:  SELECT_CLUSTERS="8 9 10 11"   (space-separated k-means cluster indices)
# Optional:  ANALYZE_EPOCH (default: latest z.N.pkl), ANALYZE_NUMK (default 20),
#            PARTICLES_STAR (default: dataset_args.particles from config.pkl),
#            OUT_STAR (default: sel_<TM_LABEL>.star in the result dir)
# =============================================================================

echo "============================================================"
echo "OPUS-ET State Selection Started"
echo "Job ID: $SLURM_JOB_ID  Node: $SLURMD_NODENAME  Start: $(date)"
echo "============================================================"

SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found. Set SKILL_DIR or copy pipeline.example.conf -> pipeline.conf."; exit 1; }
SPECIES_CONF="${SPECIES_CONF:-${SCRIPT_DIR}/species.conf}"
source "$SPECIES_CONF" 2>/dev/null || { echo "ERROR: $SPECIES_CONF not found."; exit 1; }

# Env override (survives species.conf sourcing) — select from a warm-start / alt
# result dir without editing the conf, e.g. OUTPUT_DIR_OVERRIDE=opuset/ribo/z8_expanded.
[ -n "${OUTPUT_DIR_OVERRIDE:-}" ] && OUTPUT_DIR="$OUTPUT_DIR_OVERRIDE"

ANALYZE_EPOCH="${ANALYZE_EPOCH:-}"
ANALYZE_NUMK="${ANALYZE_NUMK:-20}"
SELECT_CLUSTERS="${SELECT_CLUSTERS:-}"
PARTICLES_STAR="${PARTICLES_STAR:-}"
OUT_STAR="${OUT_STAR:-}"

if [ -z "$SELECT_CLUSTERS" ]; then
    echo "ERROR: SELECT_CLUSTERS is empty. Pass e.g. --export=ALL,SKILL_DIR=...,SELECT_CLUSTERS=\"8 9 10 11\""
    exit 2
fi

if [[ "${OUTPUT_DIR}" = /* ]]; then RESDIR="${OUTPUT_DIR}"; else RESDIR="${WORK_DIR}/${OUTPUT_DIR}"; fi

echo "Setting up environment..."
source ~/.bashrc
conda activate "$OPUSET_ENV"
ulimit -v unlimited
cd "$WORK_DIR" || exit 1

if [ ! -f "$RESDIR/config.pkl" ]; then
    echo "ERROR: config.pkl not found in $RESDIR"
    exit 1
fi

# Latest epoch if unset
if [ -z "$ANALYZE_EPOCH" ]; then
    ANALYZE_EPOCH=$(ls "$RESDIR"/z.*.pkl 2>/dev/null | sed -E 's#.*/z\.([0-9]+)\.pkl#\1#' | sort -n | tail -1)
    [ -z "$ANALYZE_EPOCH" ] && { echo "ERROR: no z.<N>.pkl in $RESDIR"; exit 1; }
fi

# D (effective box), apix (original), and particles star from config.pkl
CFG_LINE=$(python - "$RESDIR/config.pkl" <<'PY'
import pickle, sys
with open(sys.argv[1], "rb") as f:
    c = pickle.load(f)
D = c["lattice_args"]["D"] - 1
apix = c["model_args"]["Apix"] * c["dataset_args"].get("downfrac", 1.0)
print(D, f"{apix:.6f}", c["dataset_args"]["particles"])
PY
)
CFG_D=$(echo "$CFG_LINE" | awk '{print $1}')
CFG_APIX=$(echo "$CFG_LINE" | awk '{print $2}')
CFG_STAR=$(echo "$CFG_LINE" | awk '{print $3}')
[ -z "$PARTICLES_STAR" ] && PARTICLES_STAR="$CFG_STAR"
[ -z "$OUT_STAR" ] && OUT_STAR="$RESDIR/sel_${TM_LABEL:-state}.star"
KD="$RESDIR/analyze.${ANALYZE_EPOCH}/kmeans${ANALYZE_NUMK}"

echo ""
echo "Parameters:"
echo "  - Result dir:       $RESDIR"
echo "  - Epoch / numk:     $ANALYZE_EPOCH / $ANALYZE_NUMK"
echo "  - Particles star:   $PARTICLES_STAR"
echo "  - Box D / apix:     $CFG_D / $CFG_APIX"
echo "  - Select clusters:  $SELECT_CLUSTERS"
echo "  - Output star:      $OUT_STAR"

if [ ! -f "$PARTICLES_STAR" ]; then
    echo "ERROR: particles star not found: $PARTICLES_STAR"
    exit 1
fi

# OPUS-ET refines poses during training (estpose), saved as pose.<epoch>.pkl. Inject them into
# the star so the selection carries the REFINED poses forward to fixed-mode + M (not the
# original template-matching poses). -D is the SUBTOMOGRAM box (CFG_D) and --Apix the
# subtomogram angpix (CFG_APIX): the pkl's refined translations are fractions of the subtomo
# box, scaled to pixels by -D (OPUS-ET's PoseTracker uses box = lattice D-1 = the subtomo box).
# NOTE: the training scripts generate the INITIAL pkl with -D SUBTOMO_BOX_SIZE (the lattice box,
# NOT TEMPLATERES); initial translations are zero anyway. The refined translations here are
# non-zero, so -D MUST be the subtomo box, never TEMPLATERES (the decoder output size).
USE_REFINED_POSES="${USE_REFINED_POSES:-1}"
REFINED_POSE_PKL="$RESDIR/pose.${ANALYZE_EPOCH}.pkl"
SPLIT_INPUT="$PARTICLES_STAR"
if [ "$USE_REFINED_POSES" = "1" ] && [ -f "$REFINED_POSE_PKL" ]; then
    REFINED_STAR="$RESDIR/$(basename "${PARTICLES_STAR%.star}")_refined.star"
    echo "== Step 0: inject OPUS-ET-refined poses =="
    echo "  $REFINED_POSE_PKL  (-D $CFG_D --Apix $CFG_APIX)  ->  $REFINED_STAR"
    if dsd parse_pose_star "$PARTICLES_STAR" -D "$CFG_D" --Apix "$CFG_APIX" \
           --poses "$REFINED_POSE_PKL" --out-star "$REFINED_STAR" && [ -f "$REFINED_STAR" ]; then
        SPLIT_INPUT="$REFINED_STAR"
    else
        echo "WARNING: refined-pose injection failed; falling back to original poses."
    fi
else
    echo "Refined poses: none injected (USE_REFINED_POSES=$USE_REFINED_POSES, pkl present: $([ -f "$REFINED_POSE_PKL" ] && echo yes || echo no))."
fi

# Split by cluster (only needed once; safe to re-run). Splitting the refined-pose star (same
# row order as the training star, so k-means labels still align) -> per-cluster stars inherit
# the refined poses.
echo ""
echo "== Step 1: dsdsh parse_pose (split $ANALYZE_NUMK clusters) — input: $SPLIT_INPUT =="
dsdsh parse_pose "$SPLIT_INPUT" "$CFG_D" "$CFG_APIX" "$RESDIR" "$ANALYZE_EPOCH" "$ANALYZE_NUMK"
if [ $? -ne 0 ]; then echo "ERROR: dsdsh parse_pose failed"; exit 1; fi

# Verify the chosen cluster stars exist
missing=0
for k in $SELECT_CLUSTERS; do
    [ -f "$KD/pre${k}.star" ] || { echo "MISSING: $KD/pre${k}.star"; missing=1; }
done
[ "$missing" = "1" ] && { echo "ERROR: expected pre<k>.star files missing; check $KD"; exit 2; }

# Chain-combine the chosen clusters
echo ""
echo "== Step 2: chain dsdsh combine_star over: $SELECT_CLUSTERS =="
arr=($SELECT_CLUSTERS)
TMP="$RESDIR/_sel_tmp.star"
TMP2="$RESDIR/_sel_tmp2.star"     # combine_star requires the output to end in .star
cp "$KD/pre${arr[0]}.star" "$TMP"
for k in ${arr[@]:1}; do
    dsdsh combine_star "$TMP" "$KD/pre${k}.star" "$TMP2" || { echo "ERROR: combine_star failed on cluster $k"; exit 1; }
    mv "$TMP2" "$TMP"
done
mv "$TMP" "$OUT_STAR"

echo ""
echo "== DONE =="
python - "$OUT_STAR" <<'PY'
import starfile, sys, pandas as pd
d = starfile.read(sys.argv[1])
df = d if isinstance(d, pd.DataFrame) else list(d.values())[-1]
print(f"selected particles: {len(df)} -> {sys.argv[1]}")
PY
echo ""
echo "Next: build a pose pickle for the selection, then fixed-mode + M:"
echo "  dsd parse_pose_star $OUT_STAR -D $CFG_D --Apix $CFG_APIX -o ${OUT_STAR%.star}_pose.pkl"
echo "End time: $(date)"
