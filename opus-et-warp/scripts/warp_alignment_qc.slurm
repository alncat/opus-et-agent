#!/bin/bash
#SBATCH --job-name=alignment_qc          # Job name
#SBATCH --partition=normal               # CPU is enough
#SBATCH --nodes=1                        # Number of nodes
#SBATCH --ntasks=1                       # Number of tasks
#SBATCH --cpus-per-task=4                # CPU cores per task
#SBATCH --mem=24G                        # Memory per node
#SBATCH --time=04:00:00                  # Time limit (~20 s per tilt series)
#SBATCH --output=logs/alignment_qc_%j.out # Standard output log
#SBATCH --error=logs/alignment_qc_%j.err  # Standard error log

# =============================================================================
# AreTomo alignment QC (Phase 3c, required before importing alignments)
# For every active tilt series, apply AreTomo's TS.xf to the exported stack and
# measure how far each tilt still sits from its angle neighbours. Fails on a
# tilt that is off its neighbours' track or a pair with no correlation peak.
# Why: a global AreTomo alignment reports no residual, and its shift track
# cannot tell a real stage jump from a lost track. See references/phases.md.
# =============================================================================

echo "============================================================"
echo "AreTomo alignment QC started: $(date)"
echo "Job ID: $SLURM_JOB_ID"
echo "============================================================"

# -----------------------------------------------------------------------------
# CONFIGURATION — source shared pipeline.conf, then override per-script values
# -----------------------------------------------------------------------------
SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found. Set SKILL_DIR or copy pipeline.example.conf -> pipeline.conf and edit."; exit 1; }

# Script-specific overrides
QC_ENV="${QC_ENV:-$OPUSET_ENV}"          # needs numpy, scipy, mrcfile (+ matplotlib for PNGs)
QC_DIR="$WORK_DIR/gate1_qc/alignment"
QC_MAX="${QC_MAX:-0}"                    # 0 = every active tilt series

if [ -z "$ALIGN_ANGPIX" ]; then
    echo "ERROR: ALIGN_ANGPIX is empty; it is the stack pixel size the distances are measured in"
    exit 1
fi

source ~/.bashrc
conda activate "$QC_ENV" || exit 1
cd "$WORK_DIR" || exit 1
mkdir -p "$QC_DIR"
METRICS="$WORK_DIR/gate1_qc/alignment_metrics.tsv"
# An interrupted or partial rerun must not leave a previously passing summary.
rm -f "$METRICS"

FAILED=()
SERIES=()
N=0
for tomostar in "$TOMOSTAR_DIR"/*.tomostar; do
    [ -f "$tomostar" ] || continue
    [ "$QC_MAX" -gt 0 ] && [ "$N" -ge "$QC_MAX" ] && break
    ts=$(basename "$tomostar" .tomostar)
    SERIES+=("$ts")
    dir="$TILTSTACK_DIR/$ts"
    N=$((N + 1))
    # Old per-series results must not stand in for this run if an input is missing.
    rm -f "$QC_DIR/${ts}_alignment.json" "$QC_DIR/${ts}_alignment.png"
    missing=""
    for f in "$dir/$ts.st" "$dir/$ts.xf" "$dir/$ts.rawtlt"; do
        [ -f "$f" ] || missing="$missing $f"
    done
    if [ -n "$missing" ]; then
        echo "$ts: MISSING$missing"
        FAILED+=("$ts")
        continue
    fi
    aln=()
    [ -f "$dir/$ts.st.aln" ] && aln=(--aln "$dir/$ts.st.aln")
    python "${SCRIPT_DIR}/scripts/alignment_qc.py" --stack "$dir/$ts.st" --xf "$dir/$ts.xf" \
        --tilts "$dir/$ts.rawtlt" --angpix "$ALIGN_ANGPIX" "${aln[@]}" \
        --out-prefix "$QC_DIR/$ts" || FAILED+=("$ts")
done

# One row per tilt series for the Gate 1 review.
python - "$QC_DIR" "${SERIES[@]}" > "$METRICS.tmp" <<'PY'
import json, pathlib, sys
print('TS\tverdict\tn_tilts\tmax_local_A\tmedian_local_A\tmax_drift_A\theight_A\tmedian_cc'
      '\tbad_tilts\tlow_signal_tilts\tproblems')
for ts in sorted(sys.argv[2:]):
    f = pathlib.Path(sys.argv[1]) / (ts + '_alignment.json')
    if not f.exists():
        print(ts + '\tFAIL' + '\t' * 9 + 'QC did not complete')
        continue
    r = json.loads(f.read_text())
    fmt = lambda x: '' if x is None else '{:g}'.format(x)
    bad = [t['tilt'] for t in r['tilts'] if (t['local_error_A'] or 0) > r.get('local_fail_A', 30)]
    low = [t['tilt'] for t in r['tilts'] if t['low_signal']]
    print('\t'.join([r['tilt_series'], 'pass' if r['pass'] else 'FAIL', str(r['n_tilts']),
                     fmt(r['max_local_error_A']), fmt(r['median_local_error_A']),
                     fmt(r['max_drift_A']), fmt(r['specimen_height_A']), fmt(r['median_cc']),
                     ','.join('{:g}'.format(t) for t in bad), ','.join('{:g}'.format(t) for t in low),
                     '; '.join(r['problems'])]))
PY
if [ "$?" -ne 0 ]; then
    rm -f "$METRICS.tmp"
    echo "ERROR: could not summarize alignment QC results"
    exit 1
fi
mv "$METRICS.tmp" "$METRICS" || exit 1

echo ""
echo "Checked $N tilt series; metrics: $METRICS; plots: $QC_DIR/"
if [ "$N" -eq 0 ]; then
    echo "ERROR: no active tomostar files found in $TOMOSTAR_DIR"
    exit 1
fi
if [ "${#FAILED[@]}" -gt 0 ]; then
    echo "ALIGNMENT QC FAILED for ${#FAILED[@]} tilt series: ${FAILED[*]}"
    echo "Do not import these alignments into WARP until the failing tilts are realigned or excluded."
    exit 1
fi
echo "Alignment QC passed for all $N tilt series."
