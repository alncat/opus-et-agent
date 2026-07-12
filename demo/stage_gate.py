#!/usr/bin/env python3
"""Stage a gate for a clean demo take — snapshot the run-state, reopen one gate so
the conductor stops there, and turn on replay mode. Restore between takes.

Why this works: in `.opus_run_state.json` a gate is "approved" exactly when a
`checkpoints[]` entry records its decision (opus-et-conductor/SKILL.md §"Per-phase
loop": "If a gate precedes this phase and is unapproved -> open checkpoint"). So
**reopening a gate = removing its checkpoint entry.** Upstream phase statuses are
left untouched (still `done`), so a fresh conductor session fast-forwards past the
completed phases and stops at the reopened gate. Setting `demo_replay: true` makes
the conductor refuse to `sbatch` (recording must never launch a multi-hour job).

The state file usually lives on the cluster at
`$WORK_DIR/.opus_run_state.json` — run this there, or point --state at it over a
mounted path. Snapshots go in `<state_dir>/.demo_snapshots/<gate>.json`.

Usage:
  stage_gate.py list [--state PATH]
      Show the current checkpoints (index, gate, key fields) and demo_replay flag.
  stage_gate.py stage <gate> [--state PATH]
      Snapshot the state, remove the matching checkpoint(s), set demo_replay:true.
  stage_gate.py restore <gate> [--state PATH]
      Restore the snapshot taken by `stage <gate>` (reset before a re-take).

<gate> may be a friendly name/number or a checkpoint index from `list`:
  1 | gate1 | alignment_qc      Gate 1 — alignment QC
  2 | gate2 | picks_qc          Gate 2 — picks QC
  3 | gate3 | state_selection   Gate 3 — state selection
  4 | gate4 | refine            Gate 4 — resolution / refine sign-off
  0 | setup                     Gate 0 — setup
  tm_params                     the pre-Phase-6 TM-params sub-gate
Any other string is matched as a case-insensitive substring of the checkpoint's
`gate` field, and a bare integer is treated as a checkpoint index.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

FRIENDLY = {
    "0": "setup",
    "1": "alignment_qc", "gate1": "alignment_qc",
    "2": "picks_qc", "gate2": "picks_qc",
    "3": "state_selection", "gate3": "state_selection",
    "4": "refine", "gate4": "refine",
}


def load(state_path):
    p = Path(state_path)
    if not p.exists():
        sys.exit(f"error: no run-state at {p} (pass --state)")
    return json.loads(p.read_text())


def save(state, state_path):
    """Atomic write, mirroring run_state.save_state (indent=2 + os.replace)."""
    p = Path(state_path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, p)


def snapshot_path(state_path, token):
    snap_dir = Path(state_path).resolve().parent / ".demo_snapshots"
    snap_dir.mkdir(exist_ok=True)
    return snap_dir / f"{token}.json"


def resolve_token(gate):
    """A friendly name/number -> canonical gate substring; else the raw string."""
    return FRIENDLY.get(str(gate).lower(), str(gate))


def checkpoint_matches(entry, token, index):
    """True if this checkpoint should be reopened for `token` (name substring) or
    if `token` is the integer index of this checkpoint."""
    if token.isdigit() and int(token) == index:
        return True
    gate = str(entry.get("gate", "")).lower()
    return token.lower() in gate and not token.isdigit()


def cmd_list(state, state_path):
    cps = state.get("checkpoints", [])
    replay = state.get("demo_replay", False)
    print(f"state: {state_path}")
    print(f"demo_replay: {replay}")
    if not cps:
        print("checkpoints: (none — every gate is currently unapproved)")
        return
    print(f"checkpoints ({len(cps)}):")
    for i, cp in enumerate(cps):
        gate = cp.get("gate", "?")
        extras = {k: v for k, v in cp.items() if k != "gate"}
        summary = ", ".join(f"{k}={v!r}" for k, v in list(extras.items())[:3])
        print(f"  [{i}] gate={gate!r}   {summary}")


def cmd_stage(state, state_path, gate):
    token = resolve_token(gate)
    cps = state.get("checkpoints", [])
    keep, removed = [], []
    for i, cp in enumerate(cps):
        (removed if checkpoint_matches(cp, token, i) else keep).append(cp)
    if not removed:
        print(f"warning: no checkpoint matched {gate!r} (token {token!r}).")
        print("         Nothing to reopen — run `list` to see current checkpoints.")
        return
    snap = snapshot_path(state_path, token)
    shutil.copyfile(state_path, snap)
    state["checkpoints"] = keep
    state["demo_replay"] = True
    save(state, state_path)
    print(f"snapshot: {snap}")
    print(f"reopened gate {gate!r} — removed {len(removed)} checkpoint(s): "
          f"{[cp.get('gate') for cp in removed]}")
    print("demo_replay: True  (conductor will NOT sbatch)")
    print("Now start a FRESH Claude Code session and resume the run "
          "(prompt B/C/D/E in demo_recording.md).")


def cmd_restore(state_path, gate):
    token = resolve_token(gate)
    snap = snapshot_path(state_path, token)
    if not snap.exists():
        sys.exit(f"error: no snapshot at {snap} — did you `stage {gate}` first?")
    shutil.copyfile(snap, state_path)
    print(f"restored {state_path} from {snap}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "stage", "restore"])
    ap.add_argument("gate", nargs="?", help="gate name/number/index (see help)")
    ap.add_argument("--state", default=".opus_run_state.json",
                    help="path to .opus_run_state.json (default: ./)")
    args = ap.parse_args()

    if args.action == "restore":
        if not args.gate:
            ap.error("restore needs a <gate>")
        cmd_restore(args.state, args.gate)
        return

    state = load(args.state)
    if args.action == "list":
        cmd_list(state, args.state)
    elif args.action == "stage":
        if not args.gate:
            ap.error("stage needs a <gate>")
        cmd_stage(state, args.state, args.gate)


if __name__ == "__main__":
    main()
