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

GATES = [
    ('setup', 'Gate 0 - setup'),
    ('alignment_qc', 'Gate 1 - alignment QC'),
    ('tm_params', 'TM parameter selection'),
    ('picks_qc', 'Gate 2 - picks QC'),
    ('state_selection', 'Gate 3 - state selection'),
    ('refine', 'Gate 4 - resolution sign-off'),
]


def phase_job_ids(entry):
    """Canonical list, with compatibility for previously documented job keys."""
    values = entry.get('job_ids', entry.get('jobs', entry.get('job_id', [])))
    if values is None:
        return []
    if isinstance(values, (str, int)):
        values = [values]
    return [str(v) for v in values]


def gate_approved(state, gate):
    # Old protocols omitted decision; retain those recorded approvals. New
    # records always carry a decision, and the latest record supersedes earlier ones.
    records = [c for c in state.get('checkpoints', [])
               if isinstance(c, dict) and c.get('gate') == gate]
    return bool(records) and records[-1].get('decision', 'approved') == 'approved'


def record_checkpoint(state, gate, decision, phase=None, **evidence):
    """Record an explicit human decision; caller saves state with save_state."""
    if gate not in dict(GATES) or decision not in ('approved', 'rejected', 'pending'):
        raise ValueError('unknown gate or checkpoint decision')
    record = dict(evidence, gate=gate, decision=decision)
    if phase is not None:
        record['phase'] = str(phase)
        set_phase(state, phase, 'ready' if decision == 'approved' else 'checkpoint', gate=gate)
    state.setdefault('checkpoints', []).append(record)
    return state


def open_checkpoint(state, gate, phase):
    """Park the next phase at a gate without implying scientific approval."""
    return record_checkpoint(state, gate, 'pending', phase=phase)

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
    if any(k in fields for k in ('job_ids', 'jobs', 'job_id')):
        ids = phase_job_ids(fields)
        for key in ('jobs', 'job_id'):
            fields.pop(key, None)
            entry.pop(key, None)
        fields['job_ids'] = ids
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


def reconcile_jobs(state, active_ids, query_succeeded=True):
    """A running phase whose jobs are all gone from SLURM needs verification."""
    if not query_succeeded or active_ids is None:
        return state
    active = {str(a) for a in active_ids}
    for entry in state["phases"].values():
        if entry.get("status") != "running":
            continue
        job_ids = phase_job_ids(entry)
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
