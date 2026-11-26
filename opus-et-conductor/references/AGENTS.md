# OPUS-ET Agent Workspace Rules

These rules apply across the entire OPUS-ET five-skill pack (`opus-et-conductor`,
`opus-et-warp`, `opus-et-analysis`, `opus-et-visualize`, `opus-et-status`).

## Scientific & Tool Discipline (always)

- **Verify before asserting; discover, don't guess.** If uncertain how a tool, flag, or script behaves, confirm it before relying on it — in order of authority: `<tool> --help`, the tool's **source** (PyTom, cryodrgn/OPUS-ET, WarpTools, AreTomo are open — ground truth for undocumented conventions like the missing-wedge `Angle1`/`Angle2` sign), official docs, or a tiny synthetic input. Never infer a convention from a flag's name — a wrong assumption runs silently and corrupts multi-hour GPU jobs.
- **Read before editing.** Always inspect the current file/script before describing or modifying it.
- **Respect existing conventions.** Apparent inconsistencies across scripts or configs may be intentional; do not redesign unless asked.
- **Ask when genuinely ambiguous.** Show the current state and options rather than guessing.

## Scoped Edits vs. Pipeline Execution

- **When modifying this skill pack (scripts, configs, docs):** Keep edits tightly scoped to what the user asked — don't chain unrequested refactors or rename variables across files without confirming scope first. After editing, summarize what changed.
- **When driving a cryo-ET pipeline run (`opus-et-conductor`):** Operate with supervised autonomy — proceed autonomously through mechanical phases (validation, job submission, monitoring, QC preparation, and cataloged safe fixes) and pause for human sign-off at the scientific gates (0–4 and `tm_params`).

## Pack Conventions

- For a managed package install, use `opus-et status` to check the installed revision and `opus-et update` when the user requests an update. Add `--remote <ssh-alias>` when the request includes a cluster. Start a fresh client session after updating.

- **Codex with desktop and cluster MCP connections.** Use the cluster connection for project state, preflight and validation, and the desktop connection for ChimeraX scene generation. Before submitting a SLURM job, call `opus_check_sbatch` on the cluster with the project’s absolute `work_dir` and the proposed `command_line`. A denial blocks submission. The MCP check is a dry run. Record scientific decisions through the checkpoint confirmation flow.
- **Configuration single source of truth:** The `pipeline.conf` / `species.conf` split, `SKILL_DIR`, and `SPECIES_CONF` override live in `opus-et-warp/references/configuration.md`. Cite it rather than redefining it.
- **SLURM submission guard:** When the Antigravity plugin is enabled, `.agents/plugins/opus-et/hooks.json` enforces a `PreToolUse` check on `run_command` that blocks `sbatch` in replay mode (`demo_replay: true`), blocks `sbatch` on local ChimeraX/ArtiaX visualization scripts, and checks prerequisite human gate approvals in the target project’s `.opus_run_state.json`. For a skills-only installation, follow the conductor’s gate protocol before submitting each job.
- **MCP server:** When the Antigravity plugin is enabled, `.agents/plugins/opus-et/mcp_config.json` exposes 11 typed cryo-ET tools (`opus_preflight`, `opus_get_run_state`, `opus_open_checkpoint`, `opus_record_checkpoint`, `opus_exclude_tomostar`, `opus_validate_phase`, `opus_extract_config`, `opus_compute_fsc`, `opus_gen_gallery_cxc`, `opus_gen_artiax_scene`, `opus_check_sbatch`) over stdlib-only stdio JSON-RPC.
- **Tests:** Run `.venv/bin/python -m pytest -q` (never bare `python3`).
