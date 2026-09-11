---
name: warp-opus-et
description: Use when the user is processing cryo-ET data with WARP, AreTomo2, PyTOM or OPUS-ET: importing frame or tilt series from MDOCs, CTF estimation, tilt-series alignment, template matching, exporting subtomograms, M refinement, or training an OPUS-ET heterogeneity model. Also use on mentions of tomostar files, WarpTools, .st/.rawtlt stacks, defocus handedness, ts_export_particles, pipeline.conf or species.conf, or SLURM jobs for GPU tomography.
---

# WARP/OPUS-ET Cryo-ET Processing Workflow

## Agent Rules — read before acting

- **Do only what the user asks.** Don't anticipate, extend, or fix unreported issues — even obvious ones. One change at a time.
- **Read before editing.** Always read the current file/script before describing or modifying it. Don't rely on remembered content — the codebase changes.
- **Verify before asserting; discover, don't guess.** If uncertain how a tool, flag, or script behaves, confirm it before relying on it — in rough order of authority: run `<tool> --help` / `--help | grep <flag>`; read the tool's **source** (PyTom, cryodrgn/OPUS-ET, WarpTools, AreTomo are open — the source is the ground truth for undocumented conventions, e.g. the missing-wedge `Angle1`/`Angle2` sign, which `--help` does not spell out); consult **official/online docs** (WebSearch/WebFetch) when the local tool is thin; or run the tool on a tiny synthetic input and inspect the output. Never infer a convention from a flag's name — a wrong assumption baked into a job runs silently and corrupts results. Wrong documentation is worse than no documentation.
- **Enumerate scope before acting.** For multi-file changes, use `grep` to find all affected files and confirm scope with the user before making any edits.
- **Show before and after for every edit.** Before modifying a script or config, quote the relevant current lines. After editing, summarize exactly what changed — not just "done."
- **Don't chain changes.** Renaming a variable in one place does NOT mean you should rename it everywhere — confirm scope first.
- **Don't redesign.** Apparent inconsistencies may be intentional. Respect the existing pattern unless the user asks to change it.
- **When in doubt, show the current state and ask.** "Here's what I see. Do you want me to change X, Y, or both?"

## IMPORTANT: Gather Experimental Settings First
Collect the acquisition, tilt-series, template-matching, training, environment and M parameters before generating any command -- no placeholders. Frame type is detected from the file extension and MRC Z-dimension, not from names or MDOC counts. Full text: `references/settings.md`.

## Quick Start
Fill `pipeline.conf` + `species.conf`, run `validate.sh`, then submit each phase with `sbatch --export=ALL,SKILL_DIR="$(pwd)" scripts/<script>`; Phase 3.5 (TOMO_DIMS from AreTomo) is required, and the AreTomo/WARP dimension check between phases 3-5 is the one sanity check not to skip. Full text: `references/quickstart.md`.

## Phase Summary

| Phase | What happens | Key script(s) |
|-------|-------------|---------------|
| 1: Frame series | Settings → link/motion correct → CTF | `warp_frameseries_import.slurm` |
| 2: Tilt series | Import from MDOC → create settings | `warp_tiltseries_setup.slurm` |
| 3: Alignment | Export stacks → AreTomo2 → negate angles | `warp_export_stacks.slurm`, `warp_aretomo_align_negate.slurm` |
| 3.5: Update dims | Read binned dims from `_ali.mrc` → UNBINNED `TOMO_DIMS` → re-run `create_settings` | `warp_update_tomo_dims.slurm` |
| 4: Import alignments | Import .xf/.tlt into WARP | `warp_import_alignments.slurm` |
| 5: CTF + recon | Defocus handedness → CTF → reconstruct | `warp_ts_ctf.slurm`, `warp_ts_reconstruct.slurm` |
| 6: Template matching | Generate TM-ready template → mask → jobs → run TM → extract → convert | `gen_template_from_mrc.slurm`, `gen_sphere_mask.slurm`, `gen_tm_jobs_aretomo.slurm`, `run_tm_sequential.slurm`, `extract_tm_candidates_parallel.slurm`, `convert_to_star.slurm`, `convert_pytom_to_warp.slurm` |
| 7: Export | Export subtomograms for OPUS-ET | `warp_export_particles.slurm` |
| 8a: Training mask | Generate sphere/density mask at `OUTPUT_ANGPIX` from subtomo sample | `gen_training_mask.slurm` |
| 8b: Train (grad) | Train OPUS-ET heterogeneity model | `train_opuset.slurm` |
| 8c: Train (fixed) | Fixed-mode averaging → half-maps for M refinement | `train_opuset_fixed.slurm` |
| 8d: Prep half-maps | Link half-maps + create mask for M refinement | `prepare_m_halfmaps.slurm` |
| Ma: M setup | create_population + create_source (one-time) | `warp_m_setup.slurm` |
| Mb: M species | create_species with half-maps + mask + particles | `warp_m_create_species.slurm` |
| Mc: M refine | MCore iterative refinement (re-submit each pass) | `warp_m_refine.slurm` |
| Md: M mask | update_mask between MCore passes | `warp_m_update_mask.slurm` |
| Me: M export | Re-export refined particles | `warp_m_export.slurm` |

---

## SLURM Scripts Reference
One row per script: what it does, which phase, memory. Scripts source both confs themselves. Full text: `references/scripts.md`.

## Deployment Files

| File | Purpose |
|------|---------|
| `pipeline.example.conf` | Template — copy to `pipeline.conf` and edit |
| `species.example.conf` | Template — copy to `species.conf` and edit (one per species) |
| `scripts/manifest.yml` | Machine-readable phase map — scripts, vars, tools, outputs per phase |
| `validate.sh` | Phase-aware pre-flight check, species-selectable |

**Setup:** `cp pipeline.example.conf pipeline.conf && cp species.example.conf species.conf`, then edit both. Scripts exit with a clear error if the real `.conf` file is missing.

## File Organization
The run-directory layout each phase writes into (`warp_frameseries/`, `warp_tiltseries/`, `template_matching/<label>/`, `opuset/`, `m/`) and what to expect in each. Full text: `references/scripts.md`.

## Critical Issues (Quick Reference)

- **CTF range max > Nyquist**: reduce `--range_max`; must be ≥ 2× angpix
- **GLIBCXX error**: missing `LD_LIBRARY_PATH` — set `${CONDA_LIB}:${WARP_DIR}`
- **OutOfMemory**: use 256 GB+, set `ulimit -v unlimited`, reduce `--perdevice` to 1
- **Wrong tilt convention**: WARP writes angles from positive max down to negative min (e.g. +54, +51, …, -54); AreTomo expects negative max up to positive max (e.g. -54, -51, …, +54). Fix: `awk '{print -$1}'` negates each angle — order is preserved in the respective sign convention.
- **WARP tomo_dimensions must be UNBINNED**: AreTomo output is binned — multiply by binning factor. Phase 2's initial `TOMO_DIMS` is only a guess; run Phase 3.5 (`warp_update_tomo_dims.slurm`) after AreTomo and before Phase 4 to rewrite settings with the true unbinned size. Skipping this silently corrupts coordinates in Phases 5–7.
- **Verify after Phase 5 — AreTomo vs WARP reconstruction must agree**: tell the user to compare `headerPyTom warp_tiltseries/tiltstack/$TS/${TS}_ali.mrc` against `headerPyTom warp_tiltseries/reconstruction/${TS}_*Apx.mrc`. WARP names tomograms with a pixel-size suffix (e.g. `TS_026_7.84Apx.mrc`). Both are at `ALIGN_ANGPIX`, so `nx × ny × nz` must match exactly. A mismatch indicates Phase 3.5 was skipped, `--alignment_angpix` was wrong in Phase 4, or `TOMO_DIMS` X/Y was incorrect. See `references/phases.md` for the failure-mode breakdown.
- **--split must be absolute path** in OPUS-ET training, placed inside the output directory
- **Tomostar loop only**: always iterate from `tomostar/*.tomostar`, not from MRC glob

**Before generating any command — the settings to collect:** read `references/settings.md`

**Config, validate, submit — the quick start:** read `references/quickstart.md`

**Per-script reference and run-directory layout:** read `references/scripts.md`

**For detailed troubleshooting and script patterns:** read `references/gotchas.md`

**For MTools refinement and OPUS-ET averaging:** read `references/advanced.md`

**For step-by-step phase commands:** read `references/phases.md`

**External reference for WarpTools API:**
<https://warpem.github.io/warp/reference/warptools/api/>

**External reference for MTools API:**
<https://warpem.github.io/warp/reference/mtools/api/>

**External reference for PyTom template matching:**
<https://github.com/SBC-Utrecht/PyTom/wiki/Template-matching>

**External reference for AreTomo `-VolZ` / `-AlignZ`:**
<https://gensoft.pasteur.fr/docs/AreTomo/1.3.4/AreTomoManual_1.3.0_09292022.pdf>
