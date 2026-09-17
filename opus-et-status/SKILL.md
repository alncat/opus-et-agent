---
name: opus-et-status
description: Use when the user asks what is running or failed on the cluster, whether a SLURM job finished, which phase or human gate the pipeline is at, to read a failed job's log, to browse QC images, to compare tilt series by CTF fit, dose or alignment, to see template-matching score distributions, or to change a tuning knob in species.conf without editing it by hand. Polls the cluster over SSH from the local machine.
---

# OPUS-ET Status Server

A dependency-free (stdlib-only) web dashboard that polls the cluster over SSH
and shows SLURM job state, pipeline phase progress, gate status, failure log
tails, and a narrow typed config editor.

## Launch

```bash
python3 opus-et-status/scripts/status_server.py \
    --host <CLUSTER_HOST> \
    --work-dir <RUN_DIR> \
    --port 8080
```

Then open <http://127.0.0.1:8080>.

- `--host` is an SSH host or alias that already works non-interactively
  (`ssh <CLUSTER_HOST> true` must succeed without a password prompt; the client
  uses `BatchMode=yes`).
- `--work-dir` is the run directory on the cluster.
- `--species-conf` defaults to `species.conf`; pass it only to start on a
  different one. Naming a conf that is not there is not fatal -- the Config tab
  says which file it could not read and every other panel still fills.
- One run per instance. To watch a second run, start another instance on a
  different `--port`; the header names the host and run directory each one
  watches.
- Default `--bind` is `127.0.0.1`. There is **no authentication**. To view it
  on the lab network, pass `--bind <LAN-IP>` (not `0.0.0.0`); Origin checks
  then allow that IP as well as loopback.

## What it shows
Nine tabs in pipeline order -- Run, Frames, Dataset, Visual QC, Training, Inventory, Refinement, Config, Runs -- each reading what the pipeline already wrote; the source and refresh interval of every panel, and what each column means. Full text: `references/panels.md`.

## Frames: CTF quality
Per-movie CTF histograms from WARP's `processed_items.json` -- what `filter_quality --histograms` reads -- and why WarpTools itself is not run. Full text: `references/panels.md`.

## Template-matching scores
The FLCF score distribution from PyTOM's particle XML (the only file that carries it), log-scaled, with the cumulative keep-at-or-above curve and the extraction-cap check. Full text: `references/panels.md`.

## Dose and alignment
Acquisition order and accumulated dose from the mdocs joined to CTF resolution; AreTomo `.aln` shift tracks and dark-frame counts; handedness from WARP's XML. Full text: `references/panels.md`.

## Visual QC
Browse the pipeline's QC images by check (Reconstruction, Gate 1, Gate 2, …)
then tilt series, render slices and pick overlays on demand, and how the species
filter treats overlays whose filenames name no species. Full text: `references/panels.md`.

## Species: switching and creating

A run usually has several species confs (`species_ribo.conf`,
`species_fas.conf`). The chips under **Species** switch which one the Config tab
reads and writes; `--species-conf` only sets the initial one.

**Create species conf** clones the selected conf into `species_<name>.conf`. The
name must be a plain slug and the file always lands beside the other species
confs — the name is never used to build a path. It refuses to overwrite, and if
`validate.sh --species <new>` rejects the result the new file is deleted, since
a create has no prior version to roll back to. Creating is allowed while jobs
run: a new conf is sourced by nothing.

## Config editing

Only the keys in [references/config_keys.md](references/config_keys.md) are
editable. Each key declares its file: species keys go to the conf chosen by
`--species-conf`, processing keys go to `pipeline.conf` (what each file holds is defined once, in
`opus-et-warp/references/configuration.md`). Acquisition facts
(`ANGPIX`, `EXPOSURE`, dims) and all paths are **read-only** — they describe the
data, so changing them would invalidate work already done rather than
reconfigure it. `BINNING_FACTOR` is editable but load-bearing and requires an
extra confirm. Values are
parsed into a declared type and the line is regenerated (preserving its inline
comment and quoting style), so nothing arbitrary can reach a file that gets
`source`d. Every write makes a timestamped backup and is rolled back if
`validate.sh` rejects it.

An edit attempted while any job is `RUNNING`/`PENDING` returns **409** — editing
a conf mid-phase can corrupt a running job. Resubmit with
`confirm_running: true` to override when you are deliberately changing a knob
for the *next* phase.

## Tests

```bash
.venv/bin/python -m pytest opus-et-status/tests -q
```

All tests run with no cluster and no network (the SSH runner and the clock are
injected). Use `.venv/bin/python`, not bare `python3`.
