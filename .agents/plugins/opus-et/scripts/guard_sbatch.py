#!/usr/bin/env python3
"""Antigravity PreToolUse safety hook for OPUS-ET SLURM submissions.

Reads the Antigravity PreToolUse camelCase JSON payload on stdin and writes
a JSON decision object to stdout:
  - {"decision": "allow"}
  - {"decision": "deny", "reason": "..."}
  - {"decision": "force_ask", "reason": "..."}

Guards enforced on `run_command` tool calls that invoke `sbatch` (directly,
chained with shell operators, or wrapped in `ssh` / `sh -c` / `bash -c`):
  1. Never submit local ChimeraX / ArtiaX visualization scripts via `sbatch`.
  2. Never submit any `sbatch` job when `.opus_run_state.json` has
     `"demo_replay": true`.
  3. Never submit a gated pipeline SLURM script when its prerequisite human
     gate is not approved in `.opus_run_state.json`.
  4. When no local `.opus_run_state.json` is found (e.g. remote SSH submit
     where state lives on the cluster), require explicit user confirmation
     (`force_ask`).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

STATE_FILENAME = ".opus_run_state.json"

GATE_LABELS = {
    "setup": "Gate 0 - setup",
    "alignment_qc": "Gate 1 - alignment QC",
    "tm_params": "TM parameter selection",
    "picks_qc": "Gate 2 - picks QC",
    "state_selection": "Gate 3 - state selection",
    "refine": "Gate 4 - resolution sign-off",
}

# Prerequisite human gates per SLURM script (from opus-et-conductor/references/gate_protocols.md
# and opus-et-warp/scripts/manifest.yml).
SCRIPT_REQUIRED_GATES: dict[str, tuple[str, ...]] = {
    # Phases 1-5c: require Gate 0 (setup)
    "warp_frameseries_import.slurm": ("setup",),
    "warp_tiltseries_setup.slurm": ("setup",),
    "warp_export_stacks.slurm": ("setup",),
    "warp_aretomo_align_negate.slurm": ("setup",),
    "warp_alignment_qc.slurm": ("setup",),
    "warp_update_tomo_dims.slurm": ("setup",),
    "warp_import_alignments.slurm": ("setup",),
    "warp_ts_ctf.slurm": ("setup",),
    "warp_ts_reconstruct.slurm": ("setup",),
    "warp_geometry_qc.slurm": ("setup",),
    # Phases 6a-6b: template/mask prep after Gate 1 (alignment_qc)
    "gen_template_from_mrc.slurm": ("alignment_qc",),
    "gen_sphere_mask.slurm": ("alignment_qc",),
    # Phases 6c-6g: TM jobs & extraction require Gate 1 + tm_params
    "tm_pilot.slurm": ("alignment_qc",),
    "gen_tm_jobs_aretomo.slurm": ("alignment_qc", "tm_params"),
    "run_tm_sequential.slurm": ("alignment_qc", "tm_params"),
    "extract_tm_candidates_parallel.slurm": ("alignment_qc", "tm_params"),
    "convert_to_star.slurm": ("alignment_qc", "tm_params"),
    "convert_pytom_to_warp.slurm": ("alignment_qc", "tm_params"),
    # Phases 7 & 8a-8b: export & OPUS-ET training require Gate 2 (picks_qc)
    "warp_export_particles.slurm": ("picks_qc",),
    "gen_training_mask.slurm": ("picks_qc",),
    "train_opuset.slurm": ("picks_qc",),
    "analyze_opuset.slurm": ("picks_qc",),
    # Phases 8c-8d: selected-state fixed-mode half-maps require Gate 3 (state_selection)
    "select_state.slurm": ("state_selection",),
    "train_opuset_fixed.slurm": ("state_selection",),
    "prepare_m_halfmaps.slurm": ("state_selection",),
    # Phase M: multi-particle refinement requires Gate 4 (refine)
    "warp_m_setup.slurm": ("refine",),
    "warp_m_create_species.slurm": ("refine",),
    "warp_m_refine.slurm": ("refine",),
    "warp_m_update_mask.slurm": ("refine",),
    "warp_m_export.slurm": ("refine",),
}

LOCAL_ONLY_MARKERS = (
    "chimerax",
    "gen_artiax_scene",
    "gen_gallery_cxc",
    "build_insitu_scene",
)

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SSH_FLAGS_WITH_ARG = frozenset(
    {"-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m",
     "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w"}
)


def _split_shell_segments(cmd: str) -> list[tuple[str, str]]:
    """Split a shell command string on ; && || | \\n outside quotes."""
    segments = []
    buf: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    n = len(cmd)

    while i < n:
        ch = cmd[i]
        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\" and not in_single:
            buf.append(ch)
            escaped = True
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            i += 1
            continue
        if not in_single and not in_double:
            if cmd[i:i + 2] in ("&&", "||"):
                seg = "".join(buf).strip()
                if seg:
                    segments.append((seg, cmd[i:i + 2]))
                buf = []
                i += 2
                continue
            if ch in (";", "|", "\n"):
                seg = "".join(buf).strip()
                if seg:
                    segments.append((seg, ch))
                buf = []
                i += 1
                continue
        buf.append(ch)
        i += 1

    seg = "".join(buf).strip()
    if seg:
        segments.append((seg, ""))
    return segments


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _extract_ssh_remote_command(tokens: list[str]) -> str | None:
    """Return the remote command string passed to `ssh`, if any."""
    i = 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-") and len(tok) >= 2:
            if tok in _SSH_FLAGS_WITH_ARG:
                i += 2
                continue
            # Combined flag+value like -p2222 or short flag bundle
            i += 1
            continue
        # First positional is destination host; everything after is the remote command.
        if i + 1 < n:
            return " ".join(tokens[i + 1:])
        return None
    if i + 1 < n:
        return " ".join(tokens[i + 1:])
    return None


def _extract_subshell_command(tokens: list[str]) -> str | None:
    """Return the command string passed to `sh -c` / `bash -c` / `zsh -c`."""
    for i in range(1, len(tokens) - 1):
        if tokens[i] in ("-c", "-lc", "-ic"):
            return tokens[i + 1]
    return None


def _local_path(value: str, cwd: Path | None) -> Path | None:
    if not value or value == "-" or any(ch in value for ch in "$`*?[]"):
        return None
    path = Path(os.path.expanduser(value))
    if path.is_absolute():
        return path
    return cwd / path if cwd is not None else None


def _submission_context(tokens, cwd, env):
    """Slurm's working directory and exported job variables, scoped to this call."""
    env = env.copy()
    options = iter(tokens[1:])
    for option in options:
        if option.endswith(".slurm"):
            break
        key, sep, value = option.partition("=")
        if key in ("--chdir", "-D", "--export"):
            if not sep:
                value = next(options, "")
            if key == "--export":
                for item in value.split(","):
                    name, assigned, val = item.partition("=")
                    if assigned:
                        env[name] = env.get(name, val) if val in (f"${name}", "${" + name + "}") else val
            else:
                cwd = _local_path(value, cwd)
        elif option.startswith("-D") and len(option) > 2:
            cwd = _local_path(option[2:], cwd)
    return cwd, env


def _project_for_job(tokens, cwd, env):
    project = _local_path(env["WORK_DIR"], cwd) if "WORK_DIR" in env else cwd
    script = _detect_slurm_script(tokens)
    if script not in SCRIPT_REQUIRED_GATES:
        return project

    # Stock scripts source SKILL_DIR/pipeline.conf, which can point to a dataset
    # outside the shell's cwd. Read the literal configured path without sourcing shell.
    if "SKILL_DIR" in env:
        skill = _local_path(env["SKILL_DIR"], cwd)
    else:
        script_arg = next(tok for tok in tokens[1:] if os.path.basename(tok) == script)
        path = _local_path(script_arg, cwd)
        if path is None or path.parent.name != "scripts":
            return project
        skill = path.parent.parent
        if not (skill / "pipeline.conf").exists():
            return project
    if skill is None:
        return None
    try:
        config = (skill / "pipeline.conf").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    matches = re.findall(r"^\s*(?:export\s+)?WORK_DIR=(.*)$", config, re.MULTILINE)
    if len(matches) != 1:
        return None
    try:
        fields = shlex.split(matches[0], comments=True)
    except ValueError:
        return None
    return _local_path(fields[0], cwd) if len(fields) == 1 else None


def extract_sbatch_calls(cmd, cwd, env=None, *, is_remote=False):
    """Track the effective directory and variables independently for each submission."""
    calls = []
    env = dict(env or {})
    segments = _split_shell_segments(cmd)
    # These constructs may execute a directory/variable change conditionally or
    # in a subshell. A linear scan cannot establish the target in that case.
    branching = any(operator in ("||", "|") for _, operator in segments)
    uncertain = False
    for segment, operator in segments:
        tokens = _tokenize(segment)
        assigned = {}
        while tokens and _ENV_ASSIGN_RE.match(tokens[0]):
            key, val = tokens.pop(0).split("=", 1)
            assigned[key] = val
        if not tokens:
            uncertain |= branching and bool(assigned)
            env.update(assigned)
            continue
        call_env = {**env, **assigned}
        exe = os.path.basename(tokens[0])
        if exe == "export":
            uncertain |= branching
            for token in tokens[1:]:
                if _ENV_ASSIGN_RE.match(token):
                    key, val = token.split("=", 1)
                    env[key] = val
        elif exe == "cd":
            uncertain |= branching
            dest = tokens[-1] if len(tokens) > 1 else "~"
            if dest in ("$WORK_DIR", "${WORK_DIR}"):
                dest = call_env.get("WORK_DIR", "")
            cwd = _local_path(dest, cwd)
            if operator != "&&" and (cwd is None or not cwd.is_dir()):
                uncertain = True
        elif exe == "sbatch":
            job_cwd, job_env = _submission_context(tokens, cwd, call_env)
            job_dir = None if is_remote or uncertain else _project_for_job(tokens, job_cwd, job_env)
            calls.append((tokens, job_dir, is_remote))
        elif exe == "ssh":
            remote = _extract_ssh_remote_command(tokens)
            if remote:
                # Keep the local launch directory only for the replay veto.
                remote_calls = extract_sbatch_calls(remote, None, is_remote=True)
                calls.extend((args, cwd, True) for args, _, _ in remote_calls)
        elif exe in ("sh", "bash", "zsh"):
            nested = _extract_subshell_command(tokens)
            if nested:
                calls.extend(extract_sbatch_calls(nested, None if uncertain else cwd, call_env, is_remote=is_remote))
    return calls


def gate_approved(state: dict[str, Any], gate: str) -> bool:
    """Mirror opus-et-conductor/scripts/run_state.py::gate_approved."""
    checkpoints = state.get("checkpoints")
    if not isinstance(checkpoints, list):
        return False
    records = [
        c for c in checkpoints
        if isinstance(c, dict) and c.get("gate") == gate
    ]
    return bool(records) and records[-1].get("decision", "approved") == "approved"


def _is_local_viz_sbatch(tokens: list[str]) -> str | None:
    for tok in tokens[1:]:
        low = tok.lower()
        if low.endswith(".cxc"):
            return tok
        for marker in LOCAL_ONLY_MARKERS:
            if marker in low:
                return tok
    return None


def _detect_slurm_script(tokens: list[str]) -> str | None:
    """Return the basename of any known `.slurm` script referenced in `sbatch` args."""
    for tok in tokens[1:]:
        cleaned = tok.strip("\"'")
        if cleaned.endswith(".slurm"):
            return os.path.basename(cleaned)
    return None


def evaluate_hook(payload: dict[str, Any]) -> dict[str, str]:
    """Evaluate a PreToolUse hook payload and return the hook response dict."""
    tool_call = payload.get("toolCall") or {}
    tool_name = str(tool_call.get("name", ""))
    if tool_name and tool_name != "run_command":
        return {"decision": "allow"}

    args = tool_call.get("args") or {}
    cmd = args.get("CommandLine") or args.get("commandLine") or args.get("command") or ""
    if not isinstance(cmd, str) or not cmd.strip():
        return {"decision": "allow"}

    raw_cwd = args.get("Cwd", payload.get("cwd"))
    cwd = _local_path(raw_cwd, None) if isinstance(raw_cwd, str) else None
    result = {"decision": "allow"}
    for tokens, project, remote in extract_sbatch_calls(cmd, cwd):
        decision = _evaluate_submission(tokens, project, remote)
        if decision["decision"] == "deny":
            return decision
        if decision["decision"] == "force_ask":
            result = decision
    return result


def _evaluate_submission(tokens, project, remote):
    bad = _is_local_viz_sbatch(tokens)
    if bad is not None:
        return {"decision": "deny", "reason": f"{bad}: run ChimeraX/ArtiaX locally."}

    unknown = {"decision": "force_ask",
               "reason": "Confirm the target project's .opus_run_state.json and gate approvals before submitting."}
    if project is None:
        return unknown
    state_file = project / STATE_FILENAME
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "decision": "force_ask",
            "reason": (
                f"SLURM sbatch detected, but {state_file} could not be parsed ({exc}). "
                "Confirm before submitting."
            ),
        }

    if not isinstance(state, dict):
        return {
            "decision": "force_ask",
            "reason": (
                f"SLURM sbatch detected, but {state_file} is not a JSON object. "
                "Confirm before submitting."
            ),
        }

    # Guard 2: replay / demo recording mode must NEVER submit SLURM jobs.
    if state.get("demo_replay") is True:
        return {
            "decision": "deny",
            "reason": (
                f"Blocked sbatch: {state_file} has demo_replay=true. "
                "Replay/recording mode must never submit cluster jobs."
            ),
        }

    if remote:
        return unknown  # Local state cannot establish approvals on the cluster.

    script = _detect_slurm_script(tokens)
    required = SCRIPT_REQUIRED_GATES.get(script, ())
    missing = [g for g in required if not gate_approved(state, g)]
    if missing:
        formatted = ", ".join(f"{g} ({GATE_LABELS[g]})" for g in missing)
        return {"decision": "deny",
                "reason": f"Blocked {script}: missing approval in {state_file}: {formatted}."}

    return {"decision": "allow"}


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = evaluate_hook(payload)
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
