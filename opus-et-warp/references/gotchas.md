# Gotchas and Script Design Patterns

---

## WARP Gotchas

### CTF range max exceeds Nyquist
- **Symptom**: `Error: Max frequency to fit is higher than the Nyquist frequency`
- **Cause**: `--range_max` / `--c_range_max` too high for the pixel size
- **Fix**: Must be ≥ 2× angpix. For 2.17 Å, Nyquist ≈ 4.35 Å → use at least 5 Å

### Missing LD_LIBRARY_PATH causes GLIBCXX errors
- Set `export LD_LIBRARY_PATH="${CONDA_LIB}:${WARP_DIR}:$LD_LIBRARY_PATH"` before running WarpTools
- Missing this causes `version 'GLIBCXX_3.4.20' not found`
- Always set `ulimit -v unlimited` to avoid virtual memory cap

### Tomogram dimensions must be UNBINNED
- `TS_XXX_ali.mrc` from AreTomo is **binned** (e.g., 8× or 16×)
- WARP `--tomo_dimensions` requires **unbinned** pixel count
- Use `headerPyTom TS_XXX_ali.mrc` to check binned dims, then multiply by binning factor
- **awk gotcha**: `$(NF-2)` not `$NF-2` when parsing from the end:
  ```bash
  nx=$(echo "$header_output" | awk '{print $(NF-2)}')  # CORRECT
  nx=$(echo "$header_output" | awk '{print $NF-2}')    # WRONG — subtracts 2 from value
  ```

### Wrong tilt angle convention (WARP ↔ AreTomo)
- WARP: +max descending to -min (e.g., +54, +51, …, 0, …, -54)
- AreTomo: -max ascending to +min (e.g., -54, -51, …, 0, …, +54)
- Fix: `awk '{print -$1}'` negates each angle; the order is preserved in the respective sign convention. Negate output back for WARP import.

### OutOfMemoryException
- Use 256 GB+ RAM in SLURM jobs
- Reduce `--perdevice` to 1
- All scripts include `ulimit -v unlimited`

---

## AreTomo Gotchas

### Job timeout
- AreTomo on many tilt series can run 12–24+ hours
- Increase `#SBATCH --time=48:00:00` for large datasets
- Consider splitting into batches

---

## Template Matching Gotchas

### Disable `set -e` in processing loops
- `set -e` causes bash to exit on any failure — including mid-loop
- Comment it out when using manual error handling:
  ```bash
  # set -e  # disabled — we handle errors manually
  for file in ...; do
      result=$(command "$file") || true
      if [ -z "$result" ]; then continue; fi
  done
  ```

### Run TM jobs sequentially to avoid GPU OOM
- Template matching is memory-intensive; max 2 concurrent jobs on 8-GPU node
- `run_tm_sequential.slurm` handles sequential execution automatically

### Match SLURM tasks to MPI processes
```bash
#SBATCH --ntasks=4           # must match mpiexec -n 4
#SBATCH --ntasks-per-node=4
#SBATCH --cpus-per-task=4
```

### Estimated time: ~5–10 minutes per tomogram
- Depends on template size, tomogram size, angle count (e.g., 7112 for `angles_12.85_7112.em`)
- Example: 30 tomograms ≈ 3–5 hours → use `--time=6:00:00`

### Multiple templates in same job directory
- PyTOM writes `scores_reference19.em` and `angles_reference19.em` per template
- Using `glob("scores_*.em")[0]` picks an arbitrary one
- Some job organizations put per-template results in a subdirectory instead;
  use the same reference/template name either way.
- **Fix**: Always pass explicit `--template reference19` to extraction scripts:
  ```python
  # GOOD
  scores_file = job_dir / f"scores_{template_name}.em"
  # BAD
  scores_files = list(job_dir.glob("scores_*.em"))  # arbitrary!
  ```

### Custom SLURM running extractCandidates MUST set `ulimit -v unlimited`
- **Symptom**: `RuntimeError: std::bad_alloc` allocating the score/mask volumes (e.g. 960×928×460) — on BOTH login and compute nodes, even with `--mem=64G`
- **Cause**: inherited `RLIMIT_AS` (virtual-memory cap), NOT a dimension bug. Auto-margin=size gives a positive `vol(x-2*size, …)`, so the dims are fine — it is the address-space limit
- **Fix**: any hand-written SLURM that runs `pytom extractCandidates` (or allocates tomogram-sized volumes) must `ulimit -v unlimited`. The canonical train/select scripts already do; a re-extract slurm that omitted it bad_alloc'd

### PyTOM subregion attribute requires spaces
```xml
<!-- CORRECT — spaces inside quotes -->
<Volume Filename="tomo.mrc" Subregion=" 0,0,20,1000,1000,460 "/>
<!-- WRONG — no spaces → PyTOM parse error -->
<Volume Filename="tomo.mrc" Subregion="0,0,20,1000,1000,460"/>
```
Format: `" x_start,y_start,z_start,x_dim,y_dim,z_dim "` (leading + trailing space)

### Use AreTomo tomograms for TM, not WARP reconstructions
- `*_ali.mrc` from AreTomo has better contrast for template matching
- WARP's `reconstruction/*.mrc` is for visual inspection and downstream analysis

### dsdsh convert_pytom output naming
- Output name is based on **tilt series name argument**, not the input filename:
  ```bash
  dsdsh convert_pytom TS_026.star TS_026  # → TS_026_norm.star (NOT TS_026.star_norm.star)
  ```
- Changes to current working directory for output
- **Safe pattern**:
  ```bash
  cd "$OUTPUT_DIR"
  dsdsh convert_pytom "$star_file" "$ts_name"
  mv "${ts_name}_norm.star" "${ts_name}_warp.star"
  ```

### Extracting tilt series names — anchor to the tomostar list, never parse
The tilt-series name stamped into a STAR **must equal a real `<ts>.tomostar`
basename** — WARP `ts_export_particles` matches particles to tilt series by that
name. So *derive it by matching against the tomostar list, never by parsing the
string*:
- **Wrong**: `sed 's/_.*$//'` → `Position` (loses the number).
- **Wrong**: `grep -oP '^[A-Za-z]+_[0-9]+'` → assumes a `<letters>_<digits>` name.
  This **silently breaks** every other convention: `tomo01`, a bare `01`,
  `L1G1_ts_001` (letter immediately before a digit), and `24jan05a_grid2_0007`
  (leading digit) all fail to match → the series is dropped; a name with extra
  segments like `Position_1_2` is truncated to `Position_1` → particles are lost
  at export.
- **Correct**: in this skill's pipeline the star basename **already is** the exact
  tomostar name (`gen_tm_jobs_aretomo.slurm` names each TM job dir the bare
  `basename <ts>.tomostar`, which flows through `<ts>_particles.xml` → `<ts>.star`),
  so use it verbatim. For externally-organized PyTom jobs whose star carries extra
  segments (e.g. `Position_2_Position_2_ali`), recover the real name as the longest
  tomostar basename that prefixes it on a `_` boundary. See the resolution loop in
  `convert_pytom_to_warp.slurm` (exact-match fast path + tomostar-prefix fallback).

### File overwriting in multi-step conversions
- `convert.py` and `dsdsh` may not overwrite existing files cleanly
- Always remove output files before re-running:
  ```bash
  rm -f "$star_file" "$warp_star" "${OUTPUT_DIR}/${ts_name}_norm.star"
  convert.py -f input.xml ...
  ```

---

## OPUS-ET Training Gotchas

### --split flag is required and must use absolute path
```bash
# CORRECT
OUTPUT_DIR="$WORK_DIR/opuset/ribo/z8"
SPLIT_FILE="$OUTPUT_DIR/deep.pkl"   # in output dir, absolute path
torchrun ... --split "$SPLIT_FILE" -o "$OUTPUT_DIR"

# WRONG — relative paths break across cwd changes
torchrun ... --split deep.pkl -o opuset/ribo/z8
```

### Check tilt angles before setting TILT_RANGE and TILT_STEP
```bash
head -1 warp_tiltseries/tiltstack/TS_XXX/TS_XXX.tlt  # max angle (e.g., 54)
head -3 warp_tiltseries/tiltstack/TS_XXX/TS_XXX.tlt  # first 3 to compute step
```
Default in scripts: `TILT_RANGE=50`, `TILT_STEP=2` — always verify from actual `.tlt` files.

### Fixed-mode reuses a stale pose pkl / deep_split for a changed selection
- **Symptom**: all torchrun ranks exit 1 with `PoseTracker.load` asserting `Input rotations have shape (Nold,3,3) but expected (Nnew,3,3)`. The `.out` shows only `FAILED (exit code 1)` with **no traceback**
- **Cause**: `train_opuset_fixed.slurm` generated the pose pkl only `if [ ! -f ]`, so re-running fixed-mode for a NEW selection at the same `TM_LABEL` path silently reused the PREVIOUS selection's pose pkl (different particle count)
- **Fix** (already in the script): regenerate the pose pkl AND drop `deep_split.pkl` whenever the star is newer than them
- **Diagnostic rule**: torchrun "all ranks exit 1, no stdout traceback" → the assert traceback goes to the job `.err` HEAD, not `.out`. Read the `.err` HEAD first

### Fixed-mode per-rank tmp maps are DDP-synchronized — they are IDENTICAL, not partials
- **Myth**: `tmp<rank>.mrc` (`tmp0.mrc`..`tmp3.mrc` for 4 GPUs) are per-rank partial half-maps that need to be summed/averaged into the real half-map
- **Verified fact**: each `tmp<rank>.mrc` is already the COMPLETE half-map — DDP synchronizes them across ranks. `corr(tmp0, tmp1) = 1.0`, `max|tmp0 - tmp1| ~ 1e-6`
- **Fix**: use `tmp0.mrc` directly as the half-map; `prepare_m_halfmaps.slurm` already links `tmp0` correctly. Do NOT sum/mean the ranks believing they're partials — meaning them is a harmless no-op (identical inputs), but it signals a wrong mental model of the DDP output and wastes a step

### species.conf per-run overrides must respect an already-exported env var
- **Symptom**: two fixed-mode jobs submitted with `--export=...,FIXED_SUBSET_LABEL=1` and `FIXED_SUBSET_LABEL=2` both silently process subset1 — subset2's log shows `..._subset1.star` / `fixed_subset1`
- **Cause**: `species.conf` sets `FIXED_SUBSET_LABEL=1` as a hardcoded default. Sourcing `species.conf` AFTER the SLURM `--export` clobbers the exported value back to `1` for every job
- **Fix**: any per-run override in `species.conf` MUST use the env-respecting form so an already-exported value wins:
  ```bash
  # WRONG — clobbers --export=...,FIXED_SUBSET_LABEL=2
  FIXED_SUBSET_LABEL=1
  # CORRECT — keeps the exported value if set, defaults to 1 otherwise
  FIXED_SUBSET_LABEL="${FIXED_SUBSET_LABEL:-1}"
  ```
- Same class of bug as the CTF/angpix override traps — any config file sourced after job submission is a clobber hazard for every var it (re)assigns unconditionally

---

## Script Design Patterns

### Canonical tilt series loop — always use tomostar files as source

Tomostar files represent tilt series successfully imported into WARP. Loop over them rather than over MRC files directly.

```bash
TOMOSTAR_DIR="$WORK_DIR/tomostar"
TOMO_DIR="$WORK_DIR/warp_tiltseries/tiltstack"

mapfile -t tomostar_files < <(find "$TOMOSTAR_DIR" -name "*.tomostar" -type f | sort)

PROCESSED=0; FAILED=0

for tomostar_file in "${tomostar_files[@]}"; do
    ts_name=$(basename "$tomostar_file" .tomostar)

    # Find aligned tomogram with wildcard (use find, not glob variable)
    tomo_file=$(find "$TOMO_DIR/$ts_name" -name "*_ali.mrc" -type f -print -quit 2>/dev/null)

    if [ -z "$tomo_file" ]; then
        echo "Skipping $ts_name — aligned tomogram not found"
        ((FAILED++)); continue
    fi

    # Process...
    ((PROCESSED++))
done

echo "Done: $PROCESSED processed, $FAILED failed"
```

**Anti-patterns to avoid:**
```bash
# DON'T — loops over MRC directly (may include non-tomostar files)
for tomo in "$TOMO_DIR"/*/*.mrc; do ...

# DON'T — while read creates a subshell, counter updates are lost
find "$TOMOSTAR_DIR" -name "*.tomostar" | while read f; do
    ((PROCESSED++))  # this won't persist!

# DON'T — glob in variable isn't expanded by bash
PATTERN="*.mrc"
for f in "$DIR/$PATTERN"; do  # looks for a literal file named "*.mrc"
```

### Exact name matching — avoid substring collisions

`Position_1` must not match `Position_12`. Always use underscore suffix:

```bash
# BAD
if [[ "$subdir" == *"$ts_name"* ]]; then   # Position_1 matches Position_12!

# GOOD
if [[ "$subdir" == "${ts_name}_"* ]]; then  # Position_1_ only matches Position_1_*

# GOOD (Python)
if subdir.name.startswith(f"{ts_name}_"):
```

### Parallel processing with Python multiprocessing

For CPU-bound tasks on multiple tilt series, use Python's `multiprocessing.Pool` rather than bash loops:
- Better error handling and per-tilt-series log files
- Progress tracking with ✓/✗ status
- `--dry-run` capability
- See `scripts/extract_candidates_parallel.py` as a reference implementation

```python
def process_one(args_tuple):
    ts_name, ... = args_tuple
    # do work, return (ts_name, success, message)

with mp.Pool(processes=num_workers) as pool:
    results = pool.map(process_one, jobs)

for ts_name, success, msg in sorted(results):
    print(f"[{'✓' if success else '✗'}] {ts_name}: {msg}")
```

SLURM wrapper calls the Python script with `--jobs N` to set pool size.

| Task | Parallel method |
|------|-----------------|
| Template matching (PyTOM) | Sequential — GPU memory limited |
| Candidate extraction | Python multiprocessing |
| STAR conversion | Python multiprocessing |

---

## Resuming after a failed or killed job

The one place that needs explicit thought is `train_opuset` /
`train_opuset_fixed`: without `--load <weights.N.pkl>`, the script restarts
from epoch 0 and discards prior progress. Edit the script to add `--load`
before re-submitting.

For other phases, check what the relevant tool does on re-run before bulk
re-submitting (`WarpTools <command> --help`, AreTomo log behavior, etc.) —
behavior depends on the tool and the WARP version, and assumptions baked into
this skill may not hold on every install.

### `sacct` is non-functional on this cluster
Accounting is not configured, so `sacct` returns NO data — every query is
silently empty, which can look like "the job vanished". Use `squeue -j <ids>`
for running/pending state and the job LOGS (`logs/*_<jobid>.out` and `.err`)
for completed-job results and exit status. Do not rely on `sacct` for state or
exit codes here.

### Defocus handedness — pick the right flag

`WarpTools ts_defocus_hand` has five mutually exclusive modes (see WARP API
docs: <https://warpem.github.io/warp/reference/warptools/api/tilt_series/>):

| Flag | Behavior | Idempotent? |
|---|---|---|
| `--check` | Print correlation only; change nothing | yes (read-only) |
| `--set_flip` | Set handedness to "flip" for all tilt series | yes (absolute) |
| `--set_noflip` | Set handedness to "no flip" for all tilt series | yes (absolute) |
| `--set_auto` | Check correlation, then set the appropriate value automatically | yes (absolute) |
| `--set_switch` | Invert each tilt series' current value | **no — toggle** |

Recommended flow:

- **Easiest**: just use `--set_auto` once. Single submission, no manual
  decision based on correlation sign.
- **Manual**: run `--check` first, then re-run with `--set_flip` (negative
  correlation) or `--set_noflip` (positive correlation).
- **Avoid `--set_switch`** unless you genuinely want to toggle state — running
  it twice undoes itself, which is the only re-`sbatch` hazard in this phase.
