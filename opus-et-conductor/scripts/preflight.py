#!/usr/bin/env python3
"""Environment & cluster discovery for the opus-et-conductor preflight (spec §5.0).

Probes what it can and reports what is missing. External calls go through an
injected ``run``/``which`` so the module is fully unit-testable without a cluster.
The conductor (Claude) asks the user for anything in ``missing``.
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _default_run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def detect_conda_envs(run=_default_run):
    try:
        proc = run(["conda", "env", "list"])
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(proc, "returncode", 1) != 0:
        return []
    envs = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        envs.append(line.split()[0])
    return envs


def detect_partitions(run=_default_run):
    try:
        proc = run(["sinfo", "-h", "-o", "%P"])
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(proc, "returncode", 1) != 0:
        return []
    parts = []
    for line in proc.stdout.splitlines():
        name = line.strip().rstrip("*")  # default partition is marked with '*'
        if name:
            parts.append(name)
    return parts


def check_warp_fork(warp_dir, run=_default_run):
    try:
        proc = run([f"{warp_dir}/WarpTools", "ts_export_particles", "--help"])
    except (OSError, subprocess.SubprocessError):
        return False
    return "dont_correct_ctf_3d" in getattr(proc, "stdout", "")


def probe(known=None, run=_default_run, which=shutil.which):
    known = known or {}
    missing = []
    env: dict = {"discovered": False}

    warp_dir = known.get("warp_dir")
    if warp_dir:
        env["warp_dir"] = warp_dir
        env["warp_fork_ok"] = check_warp_fork(warp_dir, run)
        if not env["warp_fork_ok"]:
            missing.append("warp_fork(alncat)")
    else:
        missing.append("warp_dir")

    conda_envs = detect_conda_envs(run)
    env["conda"] = {}
    for key in ("warp_env", "opuset_env", "pytom_env"):
        val = known.get(key)
        if val and val in conda_envs:
            env["conda"][key] = val
        else:
            missing.append(key)

    # Cluster tools only. ChimeraX/ArtiaX rendering runs LOCALLY on the Mac (the
    # opus-et-visualize finale), so it is NOT a cluster preflight requirement.
    env["tools"] = {}
    for tool in ("AreTomo2", "MTools", "MCore", "dsdsh", "headerPyTom"):
        path = which(tool)
        env["tools"][tool] = path
        if path is None:
            missing.append(tool)

    cluster = {"partitions": detect_partitions(run)}
    if not cluster["partitions"]:
        missing.append("cluster_partitions")

    env["discovered"] = len(missing) == 0
    return {"environment": env, "cluster": cluster, "missing": missing}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Probe cluster environment, conda envs, tools, and SLURM partitions."
    )
    ap.add_argument("--warp-dir", default=None, help="Path to WARP bin dir (alncat fork)")
    ap.add_argument("--warp-env", default=None, help="WARP conda env name")
    ap.add_argument("--opuset-env", default=None, help="OPUS-ET conda env name")
    ap.add_argument("--pytom-env", default=None, help="PyTOM conda env name")
    ap.add_argument(
        "--work-dir",
        default=None,
        help="Persist environment/cluster/preflight results to <work-dir>/.opus_run_state.json",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any required tool, env, or partition is missing",
    )
    args = ap.parse_args(argv)
    known = {
        k: v
        for k, v in {
            "warp_dir": args.warp_dir,
            "warp_env": args.warp_env,
            "opuset_env": args.opuset_env,
            "pytom_env": args.pytom_env,
        }.items()
        if v is not None
    }
    result = probe(known)
    if args.work_dir:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import run_state

        with run_state.edit_state(args.work_dir) as state:
            state["environment"] = result["environment"]
            state["cluster"] = result["cluster"]
            state["preflight"] = {
                "status": "done" if not result["missing"] else "pending",
                "missing": list(result["missing"]),
            }
    print(json.dumps(result, indent=2))
    return 1 if (args.strict and result["missing"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
