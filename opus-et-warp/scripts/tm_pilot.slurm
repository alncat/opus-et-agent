#!/bin/bash
#SBATCH --job-name=tm_pilot
#SBATCH --partition=normal
#SBATCH --nodes=1
#SBATCH --ntasks=4
#SBATCH --cpus-per-task=4
#SBATCH --gres=gpu:4
#SBATCH --mem=256G
#SBATCH --time=48:00:00
#SBATCH --output=logs/tm_pilot_%j.out
#SBATCH --error=logs/tm_pilot_%j.err

# Run the ordinary TM stages against exactly one active series in fresh output directories.
set -e
if [[ $# != 1 || ! $1 =~ ^[[:alnum:]_][[:alnum:]_.-]*$ ]]; then
    echo "Usage: sbatch tm_pilot.slurm <tomostar basename without extension>" >&2
    exit 2
fi
series=$1
export SKILL_DIR=${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
SCRIPT_DIR=$SKILL_DIR
source "$SKILL_DIR/pipeline.conf"
species_conf=${SPECIES_CONF:-"$SKILL_DIR/species.conf"}
species_conf=$(realpath "$species_conf")
source "$species_conf"
cd "$WORK_DIR"
input="$TOMOSTAR_DIR/$series.tomostar"
if [[ ! -f "$input" ]]; then
    echo "No active tomostar at $input" >&2
    exit 1
fi

# Repeated or concurrent pilots must never reuse scores or scratch STARs.
mkdir -p "$WORK_DIR/tm_pilots"
pilot=$(mktemp -d "$WORK_DIR/tm_pilots/$series.XXXXXXXX")
echo "Pilot directory: $pilot"
mkdir "$pilot/tomostar"
cp -- "$input" "$pilot/tomostar/"
export SPECIES_CONF="$pilot/species.conf"
# Each phase sources the normal project configuration first. Preserve the resolved
# input paths before moving its working directory here.
{
    printf 'source %q || return 1\n' "$species_conf"
    for key in TILTSTACK_DIR TEMPLATE_MRC TM_MASK_MRC; do
        printf '%s=%q\n' "$key" "$(realpath "${!key}")"
    done
    if [[ -f "$ANGLE_LIST" ]]; then
        printf 'ANGLE_LIST=%q\n' "$(realpath "$ANGLE_LIST")"
    fi
    printf 'WORK_DIR=%q\n' "$pilot"
    printf '%s\n' 'TOMOSTAR_DIR="$WORK_DIR/tomostar"' 'TM_JOBS_DIR="$WORK_DIR/jobs"' \
        'TM_PARTICLES_DIR="$WORK_DIR/particles"'
} > "$SPECIES_CONF"

for stage in gen_tm_jobs_aretomo run_tm_sequential extract_tm_candidates_parallel; do
    bash "$SKILL_DIR/scripts/$stage.slurm"
done
source ~/.bashrc
conda activate "$PYTOM_ENV"
result="$pilot/${series}_scored.star"
python3 "$SKILL_DIR/scripts/tm_pilot_star.py" "$pilot/particles/${series}_particles.xml" "$result"
echo "Pilot candidate STAR: $result"
