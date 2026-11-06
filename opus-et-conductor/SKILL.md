---
name: opus-et-conductor
description: "Use when the user wants a full cryo-ET run driven end-to-end on a SLURM cluster rather than one phase: start or resume a run, see where it stopped, find which human gate is waiting on them, or approve a checkpoint. Also use when a work dir already holds .opus_run_state.json."
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
`python scripts/preflight.py` invocation). `probe` checks the supplied WARP path/env names and discovers available tools
and SLURM partitions. Run these probes on the cluster login node (directly or
over SSH), not on the desktop. For everything it returns under `missing`, ask the user
with a clarifying question. Persist answers into `.opus_run_state.json`
(`environment`, `cluster`, `preflight.status="done"`). A wrong WARP fork, absent
conda env, or unknown partition BLOCKS with a specific remediation.

Set `SKILL_DIR` to the absolute cluster-side WARP-skill directory using
`opus-et-warp/references/configuration.md`; submit from `$WORK_DIR` with `logs/` present.

## State model
`scripts/run_state.py` owns `.opus_run_state.json` (schema v1). On any (re)start:
1. `init_state(work_dir)` (loads existing state or creates it).
2. Get live jobs: `squeue --noheader -o %i` → `parse_squeue_ids` → `reconcile_jobs`
   only after a successful query. Pass `query_succeeded=False` on SSH/SLURM failure;
   a successful empty queue still moves finished jobs to `verifying`.
3. Refresh disk-derived status: run `"$SKILL_DIR/validate.sh" --json --phase <N>`,
   feed to `phase_status_from_validate`, update phases. Note:
   `phase_status_from_validate` returns a `completion` axis ("done"/"partial"/
   "pending") that is SEPARATE from the phase-lifecycle `status` field below —
   its value must not be written directly into `status`.
Lifecycle: pending → ready → running → verifying → done (+ failed/skipped).
A gate parks the next phase at checkpoint; approval moves it to ready.
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
3. `sbatch --export=ALL,SKILL_DIR="$SKILL_DIR" "$SKILL_DIR/scripts/<script>"` with the cluster's
   partition/gres overrides (`sbatch --partition=<partition> --gres=gpu:<count> --export=ALL,SKILL_DIR="$SKILL_DIR" "$SKILL_DIR/scripts/<script>"`). Record `set_phase(state, N, "running", job_ids=[job_id])`; save state.
4. Monitor squeue (background). On completion → verifying. For a run that will
   span several phases, offer to launch the `opus-et-status` dashboard
   (`python3 opus-et-status/scripts/status_server.py --host <alias> --work-dir $WORK_DIR`):
   jobs, phases, open gates, log tails and QC images in a browser, scoped to this
   run directory, nothing installed on the cluster. It is a viewer — the conductor
   still owns `.opus_run_state.json`.
5. Verify outputs via `validate.sh --json`. On failure, consult
   references/diagnose_catalog.md; auto-fix only known-and-safe cases, else escalate.
6. Advance.

## Checkpoints
Human gates: 0 setup, 1 alignment QC, `tm_params` TM-parameter selection (before Phase 6),
2 picks QC, 3 state selection, 4 refine sign-off. See references/gate_protocols.md; the
`opus-et-status` Run tab shows which gate is open and what it is waiting on. Gates
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
  (e.g. `subtomo_<label>`) for training. `DATADIR` does not redirect WARP export.
- Pass it to every Phase-6+ script via `--export=ALL,SKILL_DIR="$SKILL_DIR",SPECIES_CONF="$SKILL_DIR/species_<label>.conf"`.
- Outputs auto-namespace under the label: `template_matching/<label>/`, `opuset/<label>/z<ZDIM>/`.
- Serialize exports sharing a WARP processing tree. Before another export, preserve
  `subtomo/` as `subtomo_<label>/` and update both STAR image paths and `DATADIR`;
  see `opus-et-warp/references/workflow.md` §7.1. Existing particles block export by default.
- State currently tracks one active species per phase. Use separate run/state directories
  for concurrent species; never reuse one species' approvals for another.

## Visualization finale
After a high-res map + poses exist, hand off to the `opus-et-visualize` skill.
The **in-cell ArtiaX render** (`gen_artiax_scene.py`) runs **LOCALLY on the Mac, not on the
cluster** — ChimeraX 1.10 + ArtiaX 0.7.0 in GUI mode (locate the installed ChimeraX executable or macOS app bundle). Do
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
staging + record runbook. The supplied demo state is anchored at Gate 1; its later-gate takes run QC directly on explicitly named outputs rather than replaying a complete state history. All gates (1–4) are recordable now — Gate-4 assets are on disk
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
The phase scripts + `validate.sh` + `manifest.yml` live in the `opus-et-warp` skill, not here;
the browser dashboard is the `opus-et-status` skill.
