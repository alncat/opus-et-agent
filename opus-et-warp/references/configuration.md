# opus-et-warp: configuration

The one place the two-file convention is defined. `pipeline.conf` is shared by every species and holds what describes the dataset and the cluster; `species.conf` is per species and holds what describes one target. Scripts locate configuration through `SKILL_DIR`; species-specific phases also source `species.conf`.

## Configuration (pipeline.conf + species.conf)

**Working directory:** set `SKILL_DIR` once to the absolute WARP-skill directory
on the cluster (the directory containing `pipeline.conf`). All WARP reference
submission examples use this exported path and can be submitted from the run
directory, where `logs/` must exist. Analysis helpers use their own absolute
`<skill-dir>`; dashboard/ChimeraX commands run on the local machine.

**Two config files** — copy from examples, then edit:

```bash
cd /absolute/path/to/opus-et-warp
export SKILL_DIR="$(pwd)"
cp pipeline.example.conf pipeline.conf
cp species.example.conf species.conf
vim pipeline.conf    # tilt-series-wide: paths, microscope, detector, cluster, binning
vim species.conf     # per-species: TM_LABEL, DIAMETER, box sizes, training params
# Set both CTF_DEFOCUS_MIN and CTF_DEFOCUS_MAX to the validated search bounds.
source "$SKILL_DIR/pipeline.conf"
cd "$WORK_DIR"
mkdir -p logs
```

`pipeline.conf` is shared by all species. `species.conf` is per-species — keep separate copies for different species (e.g. `species_ribo.conf`, `species_26s.conf`). Override via `SPECIES_CONF="$SKILL_DIR/species_26s.conf" sbatch --export=ALL,SKILL_DIR="$SKILL_DIR" "$SKILL_DIR/scripts/..."`.

**SLURM copies scripts to a temp directory, so `$0` is not the original path.** Always submit with `SKILL_DIR` so scripts can find their config files:

```bash
sbatch --export=ALL,SKILL_DIR="$SKILL_DIR" "$SKILL_DIR/scripts/warp_frameseries_import.slurm"
```

In scripts, `SCRIPT_DIR` falls back to `$0` if `SKILL_DIR` is unset:

```bash
SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found..."; exit 1; }
SPECIES_CONF="${SPECIES_CONF:-${SCRIPT_DIR}/species.conf}"
source "$SPECIES_CONF" 2>/dev/null || { echo "ERROR: $SPECIES_CONF not found..."; exit 1; }
```
