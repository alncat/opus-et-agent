#!/usr/bin/env python3
"""Persistent run-state for the opus-et-conductor.

A single JSON file at ``$WORK_DIR/.opus_run_state.json`` is the source of truth
for pipeline progress. It is designed to survive Claude Code session restarts:
phase status can be re-derived from disk (Task 4) and live jobs reconciled
against squeue.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
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
            "work_dir": str(Path(work_dir).absolute()),
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


@contextmanager
def _project_lock(work_dir):
    # A separate persistent lock file survives atomic replacement of the state file.
    with (Path(work_dir) / (STATE_FILENAME + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _check_project(state, destination):
    recorded = Path(state["project"]["work_dir"])
    if recorded.resolve() != Path(destination).resolve():
        raise ValueError(f"Run state belongs to {recorded}, requested {destination}")


def _write_locked(state, destination):
    target = state_path(destination)
    updated = dict(state, revision=state.get("revision", 0) + 1)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(updated, indent=2))
    os.replace(tmp, target)
    state["revision"] = updated["revision"]
    return target


def save_state(state, *, work_dir=None):
    """Save a previously read snapshot only if its on-disk revision still matches."""
    destination = work_dir if work_dir is not None else state["project"]["work_dir"]
    _check_project(state, destination)
    with _project_lock(destination):
        current = load_state(destination) if state_path(destination).exists() else {}
        if current.get("revision", 0) != state.get("revision", 0):
            raise ValueError("Run state changed; reload before saving or use edit_state")
        return _write_locked(state, destination)


@contextmanager
def edit_state(work_dir):
    """Read, modify and atomically commit one transaction under the project lock."""
    with _project_lock(work_dir):
        state = load_state(work_dir) if state_path(work_dir).exists() else new_state(work_dir)
        _check_project(state, work_dir)
        yield state
        _write_locked(state, work_dir)


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
    with _project_lock(work_dir):
        if state_path(work_dir).exists():
            state = load_state(work_dir)
            _check_project(state, work_dir)
            return state
        state = new_state(work_dir, **kw)
        _write_locked(state, work_dir)
        return state


def gate_generation(state, gate):
    return sum(c.get("gate") == gate for c in state["checkpoints"])


def confirm_gate(work_dir, gate, decision, phase, evidence, generation, reviewer, channel):
    """Commit exactly the reviewed gate decision, after the human interaction."""
    with edit_state(work_dir) as state:
        if gate_generation(state, gate) != generation:
            raise ValueError("Checkpoint changed during review; review the current checkpoint again")
        latest = next((c for c in reversed(state["checkpoints"]) if c.get("gate") == gate), {})
        if phase is None and latest.get("decision") == "pending":
            phase = latest.get("phase")
        confirmation = {"reviewer": reviewer, "channel": channel,
                        "time_utc": datetime.now(timezone.utc).isoformat()}
        record_checkpoint(state, gate, decision, phase,
                          **dict(evidence, confirmation=confirmation))
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


if __name__ == "__main__":
    import argparse
    import pwd
    import socket
    import sys

    parser = argparse.ArgumentParser(description="Review an open scientific checkpoint at the terminal.")
    parser.add_argument("action", choices=["confirm"])
    parser.add_argument("work_dir", type=Path)
    parser.add_argument("gate", choices=dict(GATES))
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(1, "Checkpoint review requires an interactive terminal.\n")
    state = load_state(args.work_dir)
    _check_project(state, args.work_dir)
    records = [c for c in state["checkpoints"] if c["gate"] == args.gate]
    if not records or records[-1].get("decision") != "pending":
        parser.exit(1, "Open this checkpoint before reviewing it.\n")
    print(f"Host {socket.gethostname()}, project {args.work_dir.resolve()}")
    print(json.dumps(records[-1], indent=2))
    decision = input("Enter approved or rejected, or press Return to cancel: ").strip()
    if decision not in ("approved", "rejected"):
        parser.exit(1, "No decision recorded.\n")
    evidence = {k: v for k, v in records[-1].items() if k not in ("gate", "decision", "phase", "confirmation")}
    confirm_gate(args.work_dir, args.gate, decision, records[-1].get("phase"), evidence,
                 gate_generation(state, args.gate), pwd.getpwuid(os.getuid()).pw_name, "terminal")
    print(f"Recorded {decision} for {args.gate}.")
