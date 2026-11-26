# OPUS-ET Agent Workspace Rules

These rules apply across the entire OPUS-ET five-skill pack (`opus-et-conductor`,
`opus-et-warp`, `opus-et-analysis`, `opus-et-visualize`, `opus-et-status`).

## General Agent Rules — read before acting

- **Do only what the user asks.** Don't anticipate, extend, or fix unreported issues — even obvious ones. One change at a time.
- **Read before editing.** Always read the current file/script before describing or modifying it. Don't rely on remembered content — the codebase changes.
- **Verify before asserting; discover, don't guess.** If uncertain how a tool, flag, or script behaves, confirm it before relying on it — in rough order of authority: run `<tool> --help` / `--help | grep <flag>`; read the tool's **source** (PyTom, cryodrgn/OPUS-ET, WarpTools, AreTomo are open — the source is the ground truth for undocumented conventions, e.g. the missing-wedge `Angle1`/`Angle2` sign, which `--help` does not spell out); consult **official/online docs** when the local tool is thin; or run the tool on a tiny synthetic input and inspect the output. Never infer a convention from a flag's name — a wrong assumption baked into a job runs silently and corrupts results. Wrong documentation is worse than no documentation.
- **Enumerate scope before acting.** For multi-file changes, use `grep` to find all affected files and confirm scope with the user before making any edits.
- **Show before and after for every edit.** Before modifying a script or config, quote the relevant current lines. After editing, summarize exactly what changed — not just "done."
- **Don't chain changes.** Renaming a variable in one place does NOT mean you should rename it everywhere — confirm scope first.
- **Don't redesign.** Apparent inconsistencies may be intentional. Respect the existing pattern unless the user asks to change it.
- **When in doubt, show the current state and ask.** "Here's what I see. Do you want me to change X, Y, or both?"

## Pack Conventions

- **Configuration single source of truth:** The `pipeline.conf` / `species.conf` split, `SKILL_DIR`, and `SPECIES_CONF` override live in `opus-et-warp/references/configuration.md`. Cite it rather than redefining it.
- **SLURM submission guard:** When the Antigravity plugin is enabled, `.agents/plugins/opus-et/hooks.json` enforces a `PreToolUse` check on `run_command` that blocks `sbatch` in replay mode (`demo_replay: true`), blocks `sbatch` on local ChimeraX/ArtiaX visualization scripts, and checks prerequisite human gate approvals in the target project’s `.opus_run_state.json`. For a skills-only installation, follow the conductor’s gate protocol before submitting each job.
- **Tests:** Run `.venv/bin/python -m pytest -q` (never bare `python3`).
