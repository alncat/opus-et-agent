# opus-et-warp: configuration

The one place the two-file convention is defined. `pipeline.conf` is shared by every species and holds what describes the dataset and the cluster; `species.conf` is per species and holds what describes one target. Every SLURM script sources both itself, located through `SKILL_DIR`.

## Configuration (pipeline.conf + species.conf)

**Two config files** — copy from examples, then edit:

```bash
cp pipeline.example.conf pipeline.conf
cp species.example.conf species.conf
vim pipeline.conf    # tilt-series-wide: paths, microscope, detector, cluster, binning
vim species.conf     # per-species: TM_LABEL, DIAMETER, box sizes, training params
```

`pipeline.conf` is shared by all species. `species.conf` is per-species — keep separate copies for different species (e.g. `species_ribo.conf`, `species_26s.conf`). Override via `SPECIES_CONF=species_26s.conf sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/...`. 

**SLURM copies scripts to a temp directory, so `$0` is not the original path.** Always submit with `SKILL_DIR` so scripts can find their config files:

```bash
sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/warp_frameseries_import.slurm
```

In scripts, `SCRIPT_DIR` falls back to `$0` if `SKILL_DIR` is unset:

```bash
SCRIPT_DIR="${SKILL_DIR:-$(cd "$(dirname "$0")" && pwd)}"
source "${SCRIPT_DIR}/pipeline.conf" 2>/dev/null || { echo "ERROR: pipeline.conf not found..."; exit 1; }
SPECIES_CONF="${SPECIES_CONF:-${SCRIPT_DIR}/species.conf}"
source "$SPECIES_CONF" 2>/dev/null || { echo "ERROR: $SPECIES_CONF not found..."; exit 1; }
```
