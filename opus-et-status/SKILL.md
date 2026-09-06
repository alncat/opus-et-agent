---
name: opus-et-status
description: "Local web dashboard for a running cryo-ET pipeline. Use when the user asks to check job status, see whether SLURM jobs are running or failed, view pipeline phase progress, find which human gate is blocking, read a failed job's log, or adjust a tuning knob in the species config. Polls the cluster over SSH from the local machine; nothing is installed or left running on the cluster."
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
- The server binds `127.0.0.1` only and has **no authentication**. Do not
  change `--bind` to expose it on a network.

## What it shows

Nine tabs, in the order the pipeline runs: **Run** (gates, phases, jobs,
log), **Frames** (per tilt series: CTF quality, dose and alignment),
**Dataset** (read-only acquisition facts), **Visual QC** (tomogram slices and
template-matching overlays), **Training** (OPUS-ET runs under `opuset/` with
loss curves), **Inventory** (template-matching scores, particle funnel, and
which artifacts exist per series), **Refinement** (M's gold-standard FSC per
species), **Config** (processing then species, in pipeline order) and **Runs**
(other runs on the cluster, via `--runs-parent`). The stale banner and the
"waiting on you" callout stay visible across all tabs. Tabs deep-link through
the URL hash (`#qc`) and switch with keys `1`–`9`.

Every per-tilt-series number is in one table, under **Frames**. Dataset keeps
only the facts that describe the whole dataset.

**The Jobs table is scoped to this run directory.** `squeue` and `sacct` are
account-wide, and scoping matters more than it sounds: on the cluster this was
built against, all fourteen running jobs belonged to other directories, so an
unscoped panel described work with nothing to do with the run -- and rolled
its GPUs into the run's GPU-hours. Two things do the scoping:

- A **live** job is claimed by `squeue %Z`, the directory it was submitted
  from, matching the run dir or anything under it. Not by job name: the same
  cluster had an `analyze_opuset` job running in an unrelated directory. A
  job whose workdir is unknown (an older `squeue` without `%Z`) is never
  claimed -- showing nothing beats showing the whole account.
- A **finished** job is claimed by the log SLURM wrote into this run's
  `logs/`, since `<name>_<jobid>.out` makes the directory listing a job list.
  `sacct` is then queried for exactly those ids. This matters where `sacct`
  has no accounting data at all -- it returns zero rows for every query on
  this cluster -- and without the log listing the run's 58 finished jobs
  would be invisible and their logs unreachable.

A job known only from its log shows **no record** rather than an invented
outcome; its log still opens. Jobs of yours running elsewhere are counted in
the Jobs bar and can be shown with a checkbox, since a job can legitimately be
submitted from another directory.

The Jobs table carries a GPU column (from sacct `AllocTRES`; squeue has no
allocation data) and a summary strip totalling GPU-hours across this run. The inventory matrix probes the run dir's filesystem: stack,
alignment outputs, reconstruction, any species' TM star, and exported
subtomogram STARs, with Gate 1 exclusions marked. The Training tab scans
`opuset/` for `weights.*.pkl` run dirs and plots one loss point per epoch:
from `loss.txt` when the trainer wrote one, else reduced remotely from the
slurm log, whose tqdm progress lines carry a batch-level `loss=` (CR-split,
last loss per epoch). A run's curve comes from its newest log only —
warm-start segments are not merged across runs. An upstream log-format change
degrades to no curve, never to a wrong one.

| Panel | Source | Refresh |
|---|---|---|
| SLURM jobs (this run only) | `squeue` `%Z` + `logs/` + `sacct -j` | 15 s / 60 s |
| Phase progress | `.opus_run_state.json` + `validate.sh --json` | 15 s / 120 s |
| Gate status | checkpoints in `.opus_run_state.json` | 15 s |
| Failure log tail | `logs/` on the cluster | on demand |
| Dataset facts (read-only) | `pipeline.conf` | on demand |
| Tilt series (name, tilt range, dose) | `tomostar/*.tomostar` | 300 s |
| Visual QC images | `qc/`, `gate1_qc/` … `gate4_qc/` | 300 s |
| Inventory matrix | filesystem checks in the run dir | 120 s |
| Training runs + loss curves | `opuset/**/weights.*.pkl`, `loss.txt`, `logs/` | 120 s |
| Frame quality histograms | `warp_{frame,tilt}series/processed_items.json` | 300 s |
| Template-matching scores | `template_matching/*/particles/*_particles.xml` | 600 s |
| Acquisition order and dose | `mdoc/*.mdoc` | 900 s |
| Tilt-series alignment | `warp_tiltseries/tiltstack/*/*.aln`, `warp_tiltseries/*.xml` | 300 s |
| Runs index (`--runs-parent`) | sibling `.opus_run_state.json` files | 300 s |

Phase rows carry human-readable stage names (mirroring
`opus-et-warp/scripts/manifest.yml`; unknown phases degrade to a bare
number), and the "waiting on you" banner names the parked phase's stage too.

The browser tab title stays live when the dashboard is in the background:
active job count plus a mark when a job fails or a source goes stale. The
palette follows the system color scheme (light/dark). The Config tab has a
filter box over keys and descriptions, and the log panel fetches 50 or 500
lines per stream with a refetch button. If several cluster sources fail at
once, the stale banner names each of them.

`/api/config` reads four things from three files and reports each
independently: a species conf that has been renamed or deleted leaves the
dataset facts and the species list intact and names the unreadable file in a
banner over the Config tab, rather than blanking every panel with no
explanation.

Jobs are listed live-first (RUNNING/PENDING before finished history), with a
**hide finished** toggle once history accumulates. The log panel names the job
it is showing and toggles between **stderr** and **stdout**; the newest failed
job's log opens by itself. Clicking anywhere on a job row opens its log.

In the Visual QC viewer, arrow keys (or the on-screen arrows) step through the
filtered images with a position counter; `Esc` closes.

`sacct` is polled alongside `squeue` because `squeue` forgets a job the moment
it finishes — without it, a completed or failed job would silently vanish
instead of showing its exit state.

The per-tilt-series table merges two lists rather than picking one. Name,
tilt count, tilt range and dose come from the `.tomostar` files -- the
imported truth, present from phase 1, with the tilt column index taken from
the STAR header (`_wrpAngleTilt #N`) rather than assuming a field order. CTF
columns come from WARP's cache, which holds only what it has processed, so a
series that is imported but not yet fitted still gets a row. Series dropped at
Gate 1 (`excluded_tomostar` in the run state) are marked **excluded**. A
strongly asymmetric range such as `-50 .. +34` is worth noticing: the
missing-wedge angles are directional and must not be sorted.

Dataset values that the conf computes when sourced (e.g.
`ALIGN_ANGPIX="$(awk "BEGIN {printf ..., $ANGPIX * $BINNING_FACTOR}")"`) are
resolved arithmetically: a bare `$VAR` reference and a `$A * $B` product are
the only two shapes recognised, and anything else is left as the literal
expression. The dashboard never sources a conf to expand them -- a read-only
viewer must not execute shell out of a file, so `$(rm -rf ~)` resolves to
nothing rather than running.

On clusters where `python3` is not on PATH in a non-interactive shell,
`validate.sh --json` cannot run and phase progress will show an error naming
the fix. Pass the environment prelude, e.g.:

```bash
--remote-init 'source ~/.bashrc && conda activate <env>'
```

Older SLURM (< 20.02) rejects `squeue --me`, so the user is selected with
`squeue -u "$(whoami)"` instead.

If the cluster becomes unreachable the dashboard keeps showing the last good
data behind a "stale" banner rather than blanking, and backs off its retries.

## Frames: CTF quality

The **Frames** tab answers the question `WarpTools filter_quality --settings
warp_frameseries.settings --histograms` answers, without running WarpTools.

It reads WARP's own quality cache -- `processed_items.json` in
`warp_frameseries/` and `warp_tiltseries/` -- which is where `filter_quality`
reads from. Two reasons not to shell out to the tool itself:

- WarpTools is a .NET binary and its GC cannot reserve its address space under
  a login node's `ulimit -v`. It dies with *"Failed to create CoreCLR,
  HRESULT: 0x8007000E"* before printing anything. Submitting a SLURM job to
  draw a histogram is not what a status dashboard should do.
- The same per-movie numbers otherwise live in `warp_frameseries/*.xml`, which
  is ~200 MB for a 400-movie run. The cache is ~100 KB.

Nothing is re-estimated: every number shown is one WARP already wrote.

**Histograms** are drawn for defocus, astigmatism (the magnitude of WARP's
`AsX`/`AsY` components, which is the `DefocusDelta` the per-movie XML records),
CTF resolution, phase shift, motion, masked percentage and particle count --
but only for metrics that both exist and vary. A metric WARP never measured
(`null`, e.g. motion on single-frame data) and one that is identical
everywhere (phase shift with no phase plate) are named in a line under the
grid instead of drawn as an empty card. `null` is kept distinct from `0`: an
unmeasured metric is not a measured zero.

**Per tilt series** shows the tilt-series-level fit -- tilt range, defocus
range, astigmatism, CTF resolution, specimen inclination and the largest
alignment shift -- plus a sparkline of per-movie CTF resolution across the
series, on a scale shared by every row so the rows can be compared with each
other. Best resolution is at the top, so a drooping line is a series
degrading across its tilts.

Frames are grouped by the `Tlts` array each tilt series lists, never by
splitting a movie name on `_`. That is WARP's own mapping, so it holds for
`TS_034_01`, `Position_1_01`, `L1G1_ts_001_01` alike. A tilt WARP lists but
has no entry for stays a gap in the sparkline rather than shifting the
remaining tilts left.

**Worst CTF resolution** names the eight worst fits with their series. One bad
tilt rarely matters; several from one series is a reason to look at that
series.

## Template-matching scores

The score distribution is what Gate 2 turns on, and it exists in exactly one
place: `template_matching/<species>/particles/<TS>_particles.xml`. **Neither
STAR file carries it** -- not PyTOM's `star_files/` nor WARP's
`warp_star/*_warp.star`, both of which stop at coordinates and Euler angles.

Scores are binned on the cluster over the fixed [0, 1] domain of a correlation
coefficient, so no range scan is needed and 100 counts cross the wire instead
of thousands of floats per tilt series.

The bars are **log-scaled**, because the counts span four orders of magnitude
(9305 at the noise peak against 1 in the tail) and the tail is where the real
particles are. The green curve is the cumulative count at or above each score,
which answers the actual question -- cut here, keep how many? -- without
needing the conf.

Each series reports whether it hit `extractCandidates --numberCandidates`,
read from the extraction log rather than guessed from round counts: this run
used 600, 800, 5000, 6000 and 8000 for different series. When a particle file
holds *more* particles than its log claims, the log is stale (PyTOM re-run
without rewriting it) and the cap is reported as unknown rather than wrong.

## Dose and alignment

The **mdocs** are the only record of acquisition *order*. In a dose-symmetric
scheme `ZValue` and tilt angle are different sequences -- `ZValue 0` here is
tilt -0.005 deg, `ZValue 43` is tilt -44 deg -- so accumulated dose can only
be reconstructed from the mdoc. Joined to the CTF resolution in WARP's frame
cache on the movie name, that gives resolution against dose (the damage curve)
rather than against tilt. `SubFramePath` is reduced to a bare movie name, so a
Windows path from the microscope PC still joins. The scheme is inferred from
how often the tilt angle changes sign, and stage drift is the largest move
from the series' first recorded position.

The **alignment** table reads AreTomo's `.aln` per series: fitted tilt axis,
per-tilt shift track, and the count of tilts discarded as too dark. WARP's own
`MaxShiftX/Y` is deliberately not shown beside it -- WARP imports this same
alignment and stores the same movement in angstrom. From
`warp_tiltseries/<TS>.xml` only `AreAnglesInverted` is taken; its `PlaneNormal`
inclination reproduces the `CtfInclination` the per-series table already shows.

## Visual QC

The **Visual QC** tab browses PNGs the pipeline produced: reconstructed
tomogram slices (`qc/<ts>_{xy,xz}.png`), the handedness montage, and
template-matching pick overlays (`gate2_qc/`).

Images are shown as a **thumbnail grid**, not a list of filenames — a QC
browser is useless if you must open each image to find out what it holds.
Thumbnails are generated once on the cluster into `qc_thumbs/` (240 px grey
JPEG, ~14 KB versus ~2.2 MB for the original) and returned base64 in a single
request, so a filtered view of ~15 images costs about 200 KB and under two
seconds rather than 33 MB. Clicking one opens the full image in the viewer
directly above the listing.

Renders you make here appear as **Rendered here**, directly under the Render
button that produced them; the archive below lists only pipeline output.

The archive is **two levels deep: section, then one row per tilt series.** A
section is the pipeline step that produced the images, in pipeline order
(`qc/` reconstruction, then `gate1_qc` … `gate4_qc`), each captioned with the
question it answers rather than only the directory it came from. Within a
section every tilt series gets a row, so the eye runs down a column of the
same view across series — which is the actual QC question, *which one is
bad?*, and the one a dropdown showing a single series at a time cannot answer.
Clicking a row's label narrows to that series and clicking it again restores
the rest.

Filenames are parsed into tomogram / kind / species / slab / variant and shown
as readable captions (`slab 142 · all picks`). The directories disagree on
separators — `gate2_qc` writes `TS028` where the tomostar says `TS_028` — so
names are compared normalised and unify onto one tilt series.

There are **two filters, not three**. `kind` and the source directory said
almost the same thing (`qc/` holds the slices, `gate2_qc/` the overlays), so
filtering on both narrowed nothing while costing two dropdowns, and grouping
on both nested a heading inside its own restatement. What remains is tilt
series (defaulting to *all*, so the comparison is the default view) and
species, which is the axis that actually splits the overlays.

The species filter is **strict about what the filenames claim**. A slice has
no species and is never filtered out by one. An overlay whose filename carries
no species — 120 of them in this run are plain `TS_026_slab142_all.png` — is
matched only by an explicit **unlabelled** chip, never by `ribo`, which would
be an attribution nothing on disk supports. The section says how many are
unattributed and that re-rendering produces labelled copies.

Only the discovered listing is servable: `/api/qc/image?path=…` matches the
requested path against that listing and refuses anything else with 403, so a
client path never builds a filesystem path. Images are fetched base64 over ssh,
so a large slice (a few MB) takes a moment.

### Rendering on demand

Pick a tilt series and a kind, then **Render**:

- **tomogram slices** → `qc_tools/slice_preview.py` → `qc_ondemand/<ts>[_z<N>]_{xy,xz}.png`.
  Set **z** (plane for the XY view) and **y** (plane for the XZ view); blank
  means central. The depth goes into the filename, so rendering another depth
  does not overwrite the previous one. Out-of-range values are clamped to the
  edge slice rather than failing.
- **TM pick overlay** → `qc_tools/tm_picks_overlay.py` → `qc_ondemand/<label>_<ts>_slab*.png`

For overlays you can set **slabs** (how many z-slabs to sample, 1–50),
**thickness** (tomogram px), **top-N** and **projection** (mean/min/max). These
are whitelisted and range-checked server-side before reaching the command line.

`tm_picks_overlay.py` **plans slab centres automatically**, evenly spaced across
the picks' z-range, so you choose how densely z is sampled rather than an
individual slab depth. Twelve slabs on a 500 px tomogram gives one roughly every
36 px. Naming an exact z would require adding a `--slab-z` option to the tool.

The overlay has its own **species** selector, so you can render FAS picks
without leaving the tab; it defaults to whatever the Config tab has selected.
The chosen conf's `TM_LABEL` and the pick-star path are resolved server-side,
never supplied by the browser. The tilt-series name is matched against the discovered tomostar listing
and additionally checked against `^[A-Za-z0-9][A-Za-z0-9._-]*$` before it can
reach a shell glob.

The depth controls require the deployed `qc_tools/slice_preview.py` to carry
`--z`/`--y`. Keep it in step with `opus-et-visualize/scripts/slice_preview.py`:

```bash
scp opus-et-visualize/scripts/slice_preview.py <CLUSTER_HOST>:<RUN_DIR>/qc_tools/
```

This **runs on the login node** (about 10–20 s per tomogram here) and needs
`--remote-init` where python3 is not on PATH non-interactively. New images
appear immediately — the QC source is invalidated rather than waiting out its
300 s TTL.

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
`--species-conf`, processing keys go to `pipeline.conf`. Acquisition facts
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
