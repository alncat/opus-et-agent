#!/bin/bash
#SBATCH --job-name=geometry_qc           # Job name
#SBATCH --partition=normal               # CPU is enough
#SBATCH --nodes=1                        # Number of nodes
#SBATCH --ntasks=1                       # Number of tasks
#SBATCH --cpus-per-task=4                # CPU cores per task
#SBATCH --mem=32G                        # Memory per node
#SBATCH --time=06:00:00                  # Time limit (~1 min per tilt series)
#SBATCH --output=logs/geometry_qc_%j.out # Standard output log
#SBATCH --error=logs/geometry_qc_%j.err  # Standard error log

# =============================================================================
# Gate 1 image geometry QC (Phase 5c, required before template matching)
# For every active tilt series, compare the raw 0-degree tilt, the AreTomo
# volume (the template-matching volume) and the WARP reconstruction in physical
# units. Fails on a scale error, poor agreement or a Z-mirror.
# Why: in 2026-09 WARP reconstructed the central half of every tomogram at 2x
# while dimensions, headers and sampling all checked out; only the images
# showed it. See references/gotchas.md.
# =============================================================================

echo "============================================================"
echo "Gate 1 geometry QC started: $(date)"
echo "Job ID: $SLURM_JOB_ID"
echo "============================================================"

# -----------------------------------------------------------------------------
# CONFIGURATION — source shared pipeline.conf, then override per-script values
# -----------------------------------------------------------------------------
SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found. Set SKILL_DIR or copy pipeline.example.conf -> pipeline.conf and edit."; exit 1; }

# Script-specific overrides
QC_ENV="${QC_ENV:-$OPUSET_ENV}"          # needs numpy, scipy, mrcfile (+ matplotlib for PNGs)
QC_DIR="$WORK_DIR/gate1_qc/geometry"
QC_MAX="${QC_MAX:-0}"                    # 0 = every active tilt series

source ~/.bashrc
conda activate "$QC_ENV" || exit 1
cd "$WORK_DIR" || exit 1
mkdir -p "$QC_DIR"
METRICS="$WORK_DIR/gate1_qc/geometry_metrics.tsv"
# An interrupted or partial rerun must not leave a previously passing summary.
rm -f "$METRICS"

# WARP names reconstructions by the requested pixel size, to two decimals.
REC_TAG=$(awk "BEGIN {printf \"%.2f\", $ALIGN_ANGPIX}")
FAILED=()
SERIES=()
N=0
for tomostar in "$TOMOSTAR_DIR"/*.tomostar; do
    [ -f "$tomostar" ] || continue
    [ "$QC_MAX" -gt 0 ] && [ "$N" -ge "$QC_MAX" ] && break
    ts=$(basename "$tomostar" .tomostar)
    SERIES+=("$ts")
    ali="$TILTSTACK_DIR/$ts/${ts}_ali.mrc"
    rec="$PROCESSING_DIR/reconstruction/${ts}_${REC_TAG}Apx.mrc"
    N=$((N + 1))
    # Old per-series results must not stand in for this run if an input is missing.
    rm -f "$QC_DIR/${ts}_geometry.json" "$QC_DIR/${ts}_geometry.png"
    if [ ! -f "$ali" ] || [ ! -f "$rec" ]; then
        echo "$ts: MISSING $( [ -f "$ali" ] || echo "$ali" ) $( [ -f "$rec" ] || echo "$rec" )"
        FAILED+=("$ts")
        continue
    fi
    python "${SCRIPT_DIR}/scripts/geometry_qc.py" --ali "$ali" --warp "$rec" \
        --tomostar "$tomostar" --frame-settings "${FRAMESERIES_DIR}.settings" \
        --out-prefix "$QC_DIR/$ts" || FAILED+=("$ts")
    if [ ! -s "$QC_DIR/${ts}_geometry.png" ]; then
        echo "$ts: MISSING geometry preview PNG"
        FAILED+=("$ts")
    fi
done

# One row per tilt series for the Gate 1 review.
python - "$QC_DIR" "${SERIES[@]}" > "$METRICS.tmp" <<'PY'
import json, pathlib, sys
print('TS\tverdict\twarp_scale\twarp_r\tz_direct_r\tz_mirrored_r\ttilt0_scale\ttilt0_r\tproblems')
for ts in sorted(sys.argv[2:]):
    f = pathlib.Path(sys.argv[1]) / (ts + '_geometry.json')
    if not f.exists():
        print(ts + '\tFAIL\t\t\t\t\t\t\tQC did not complete')
        continue
    r = json.loads(f.read_text())
    v, z, t = r['warp_vs_aretomo'], r.get('z_mirror') or {}, r.get('tilt0_vs_aretomo') or {}
    fmt = lambda x: '' if x is None else '{:.3f}'.format(x)
    print('\t'.join([r['tilt_series'], 'pass' if r['pass'] else 'FAIL', fmt(v['scale']), fmt(v['r']),
                     fmt(z.get('r_direct')), fmt(z.get('r_z_mirrored')), fmt(t.get('scale')),
                     fmt(t.get('r')), '; '.join(r['problems'])]))
PY
if [ "$?" -ne 0 ]; then
    rm -f "$METRICS.tmp"
    echo "ERROR: could not summarize geometry QC results"
    exit 1
fi
mv "$METRICS.tmp" "$METRICS" || exit 1

echo ""
echo "Checked $N tilt series; metrics: $METRICS; images: $QC_DIR/"
if [ "$N" -eq 0 ]; then
    echo "ERROR: no active tomostar files found in $TOMOSTAR_DIR"
    exit 1
fi
if [ "${#FAILED[@]}" -gt 0 ]; then
    echo "GEOMETRY QC FAILED for ${#FAILED[@]} tilt series: ${FAILED[*]}"
    echo "Do not run template matching or particle export until the images agree."
    exit 1
fi
echo "Geometry QC passed for all $N tilt series."
