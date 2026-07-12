---
name: opus-et-conductor
description: Supervised-autonomy orchestrator for the cryo-ET pipeline. Drives opus-et-warp (reconstruction) and opus-et-analysis (interpretation) end-to-end over SLURM, tracking progress in .opus_run_state.json, pausing at scientific checkpoints, and generating in-cell visualizations via opus-et-visualize. Use when the user wants to run, monitor, resume, or checkpoint a full cryo-ET run rather than a single phase.
---

# OPUS-ET Conductor

Orchestrates the cryo-ET pipeline as a supervised-autonomy agent: autonomous
through mechanical phases, paused at scientific judgment calls.

## Agent Rules — read before acting
- Do only what the user asks; one change at a time.
- Read before editing; verify tool behavior before asserting.
- Never hold pipeline progress only in conversation — the source of truth is
  `$WORK_DIR/.opus_run_state.json` plus files on disk.

## 0. Preflight (always first)
Before submitting any job, discover the environment and cluster:
`scripts/preflight.py` is a library, not a CLI (no `__main__`) — the conductor
imports it and calls `preflight.probe(...)` directly (there is no
`python scripts/preflight.py` invocation). `probe` auto-detects the WARP fork,
the three conda envs, AreTomo2/MTools/MCore/dsdsh/headerPyTom, and
SLURM partitions. For everything it returns under `missing`, ask the user
with a clarifying question. Persist answers into `.opus_run_state.json`
(`environment`, `cluster`, `preflight.status="done"`). A wrong WARP fork, absent
conda env, or unknown partition BLOCKS with a specific remediation.

## State model
`scripts/run_state.py` owns `.opus_run_state.json` (schema v1). On any (re)start:
1. `load_state(work_dir)`.
2. Get live jobs: `squeue --noheader -o %i` → `parse_squeue_ids` → `reconcile_jobs`
   (running phases whose jobs are gone become `verifying`).
3. Refresh disk-derived status: run `opus-et-warp/validate.sh --json --phase <N>`,
   feed to `phase_status_from_validate`, update phases. Note:
   `phase_status_from_validate` returns a `completion` axis ("done"/"partial"/
   "pending") that is SEPARATE from the phase-lifecycle `status` field below —
   its value must not be written directly into `status`.
Statuses: pending → ready → running → verifying → checkpoint → done (+ failed/skipped).
(These are the allowed `status` values, in `PHASE_STATUSES`.)

## Per-phase loop (between checkpoints)
For each phase from `opus-et-warp/scripts/manifest.yml` (skip phases marked
`optional: true` unless the user opts in — e.g. Phase 8a's density-shaped training
mask, since 8b auto-creates a default sphere):
1. `validate.sh --phase N --assume-env` (pre-check). Pass `--assume-env` because
   preflight (§0) already verified the toolchain — it skips validate.sh's
   env/tool-existence checks (sections 3 & 4) so they aren't re-run every phase,
   while all phase-readiness checks (config, required vars, derived sanity,
   per-tomostar completion) still run. Drop `--assume-env` only when running
   validate.sh standalone without a completed preflight. Resolve derived values.
2. If a gate precedes this phase and is unapproved → open checkpoint (see
   references/gate_protocols.md), wait, record the decision.
3. `sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/<script>` with the cluster's
   partition/gres. Record job_id; set status running.
4. Monitor squeue (background). On completion → verifying.
5. Verify outputs via `validate.sh --json`. On failure, consult
   references/diagnose_catalog.md; auto-fix only known-and-safe cases, else escalate.
6. Advance.

## Checkpoints
Human gates: 0 setup, 1 alignment QC, `tm_params` TM-parameter selection (before Phase 6),
2 picks QC, 3 state selection, 4 refine sign-off. See references/gate_protocols.md. Gates
0/1/2/3/4 are implemented (Gate 1 runs a parallel slice-preview QC Workflow, one agent per
tomogram; Gate 4 = half-map split + molecule mask + gold-standard FSC sign-off, via
`train_opuset_fixed.slurm` / `gen_mask_from_map.py` / `compute_fsc.py`). M refinement after
Gate-4 sign-off is also **implemented** — it's the non-optional manifest `M` phase
(`warp_m_setup/create_species/refine/update_mask/export.slurm`) that the per-phase loop drives,
and it has been run to convergence on the demo data. The remaining pending item is the
`tm_params` gate's matching-params auto-tune (its mask half is done via `tm_auto_mask.py`).

## Multiple species (same tomograms, different templates)
Template matching onward is namespaced by `TM_LABEL`, so several species share one
reconstruction set. To add a species (e.g. `fas` alongside `ribo`):
- Copy `species.conf` → `species_<label>.conf`; set `TM_LABEL`, the template (a high-res
  `INPUT_MRC`/`MAP_ANGPIX` to resample, or a ready `TM_BOX_SIZE`³ TM template copied
  straight to `templates/<label>_tm.mrc`), `DIAMETER`, mask via `tm_auto_mask.py`,
  `TEMPLATE_INVERT`, `NUM_CANDIDATES` (per abundance), and a species-specific `DATADIR`
  (e.g. `subtomo_<label>`) so exports don't collide.
- Pass it to every Phase-6+ script via `--export=ALL,SKILL_DIR="$(pwd)",SPECIES_CONF="$(pwd)/species_<label>.conf"`.
- Outputs auto-namespace under the label: `template_matching/<label>/`, `opuset/<label>/z<ZDIM>/`.
- Species run independently (and in parallel) on the same tomograms; validate each on one
  reference-rich tomogram first (see Gate `tm_params`).

## Visualization finale
After a high-res map + poses exist, hand off to the `opus-et-visualize` skill.
The **in-cell ArtiaX render** (`gen_artiax_scene.py`) runs **LOCALLY on the Mac, not on the
cluster** — ChimeraX 1.10 + ArtiaX 0.7.0 in GUI mode (`/Applications/ChimeraX-1.10.app`). Do
NOT `sbatch` it; pull the maps/poses (`.mrc`, `sel_*.star`) down from the cluster first, then
render locally. (So ChimeraX is not a cluster preflight requirement.) The matplotlib QC —
Gate-2 picks (`tm_picks_overlay.py`) and Gate-1 slice previews (`slice_preview.py`), both in
`opus-et-visualize` — can run on the cluster; the numeric pick metric is
`opus-et-analysis/scripts/tm_eval_agreement.py`.

## Demo / replay recording
To screen-record the conductor reaching a gate **without re-running the heavy SLURM jobs**,
run in **replay mode** (`demo_replay: true` in `.opus_run_state.json`, or told "replay mode —
recording"). In replay mode the conductor NEVER `sbatch`es: every upstream phase's outputs
must already exist on disk, so it fast-forwards (the per-phase loop already skips a phase
whose outputs are complete), reaches the next unapproved gate, runs only the fast QC on the
existing outputs, and presents the checkpoint. If any output is missing, STOP — do not submit
(a recording must never launch a multi-hour job). See `references/demo_recording.md` for the
staging + record runbook. All gates (1–4) are recordable now — Gate-4 assets are on disk
in `demo/qc/finale/` and `demo/qc/gate4_resolution/`.

## Reference: manifest-driven phases
`opus-et-warp/scripts/manifest.yml` is the phase graph. Do not duplicate it here.

## Files in this skill
```
scripts/
  preflight.py         # library — toolchain/partition discovery probes (probe())
  run_state.py         # library — owns .opus_run_state.json (schema v1): init / load / save / derive
references/
  gate_protocols.md    # per-gate Prepare/Decide/Persist protocols (0, 1, tm_params, 2, 3, 4)
  demo_recording.md    # replay-mode record runbook (per-gate copy-paste prompts)
  diagnose_catalog.md  # known-failure catalog for self-correction
tests/                 # pytest — test_preflight, test_run_state, test_run_state_derive, test_validate_json
```
The phase scripts + `validate.sh` + `manifest.yml` live in the `opus-et-warp` skill, not here.
