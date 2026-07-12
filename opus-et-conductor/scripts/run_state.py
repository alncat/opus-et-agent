#!/usr/bin/env python3
"""Persistent run-state for the opus-et-conductor.

A single JSON file at ``$WORK_DIR/.opus_run_state.json`` is the source of truth
for pipeline progress. It is designed to survive Claude Code session restarts:
phase status can be re-derived from disk (Task 4) and live jobs reconciled
against squeue.
"""
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1
STATE_FILENAME = ".opus_run_state.json"

PHASE_STATUSES = {
    "pending", "ready", "running", "verifying",
    "checkpoint", "done", "failed", "skipped",
}


def state_path(work_dir):
    return Path(work_dir) / STATE_FILENAME


def new_state(work_dir, pipeline_conf="pipeline.conf", species=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {
            "work_dir": str(work_dir),
            "pipeline_conf": pipeline_conf,
            "species": list(species) if species else [],
        },
        "environment": {"discovered": False},
        "cluster": {},
        "preflight": {"status": "pending"},
        "phases": {},
        "checkpoints": [],
        "artifacts": {},
        "excluded_tomostar": [],
    }


def save_state(state):
    work_dir = state["project"]["work_dir"]
    target = state_path(work_dir)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, target)  # atomic on POSIX
    return target


def load_state(work_dir):
    target = state_path(work_dir)
    if not target.exists():
        raise FileNotFoundError(f"No run-state at {target}")
    data = json.loads(target.read_text())
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"run-state schema {data.get('schema_version')} != {SCHEMA_VERSION}"
        )
    return data


def init_state(work_dir, **kw):
    if state_path(work_dir).exists():
        return load_state(work_dir)
    state = new_state(work_dir, **kw)
    save_state(state)
    return state


def set_phase(state, phase, status, **fields):
    if status not in PHASE_STATUSES:
        raise ValueError(f"unknown phase status: {status!r}")
    entry = state["phases"].setdefault(str(phase), {})
    entry["status"] = status
    entry.update(fields)
    return state


def phase_status_from_validate(validate_json):
    """Reduce validate.sh --json phase_completion rows to per-phase disk completion.

    validate.sh may emit MULTIPLE phase_completion rows for one phase (e.g. phase 7
    has both per-tilt-series subtomograms and a combined export STAR). Rows sharing a
    phase are aggregated (summed) so the phase is only "done" when every one of its
    output groups is complete. Returns {phase: {"completion", "done": int, "total": int}}.
    The ``completion`` axis is separate from the phase-lifecycle ``status`` vocabulary.
    """
    agg = {}
    for check in validate_json.get("checks", []):
        if check.get("status") != "phase_completion":
            continue
        phase = str(check.get("phase", ""))
        if not phase:
            continue
        entry = agg.setdefault(phase, {"done": 0, "total": 0})
        entry["done"] += int(check.get("done", 0))
        entry["total"] += int(check.get("total", 0))
    out = {}
    for phase, entry in agg.items():
        done, total = entry["done"], entry["total"]
        if total > 0 and done == total:
            completion = "done"
        elif done > 0:
            completion = "partial"
        else:
            completion = "pending"
        out[phase] = {"completion": completion, "done": done, "total": total}
    return out


def parse_squeue_ids(squeue_stdout):
    """Base job ids from `squeue --noheader -o %i` output (array ids collapsed)."""
    ids = set()
    for line in squeue_stdout.splitlines():
        token = line.strip()
        if token:
            ids.add(token.split("_", 1)[0])
    return ids


def reconcile_jobs(state, active_ids):
    """A running phase whose jobs are all gone from SLURM needs verification."""
    active = {str(a) for a in active_ids}
    for entry in state["phases"].values():
        if entry.get("status") != "running":
            continue
        job_ids = [str(j) for j in entry.get("job_ids", [])]
        if job_ids and not any(j in active for j in job_ids):
            entry["status"] = "verifying"
    return state


def exclude_tomostar(state, ts_name):
    """Record a tilt series as excluded (keep-list, spec §12.2). Idempotent."""
    excluded = state.setdefault("excluded_tomostar", [])
    if ts_name not in excluded:
        excluded.append(ts_name)
    return state


def active_tomostars(all_names, state):
    """`all_names` minus the excluded set, original order preserved."""
    excluded = set(state.get("excluded_tomostar", []))
    return [n for n in all_names if n not in excluded]
