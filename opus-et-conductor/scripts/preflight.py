#!/usr/bin/env python3
"""Environment & cluster discovery for the opus-et-conductor preflight (spec §5.0).

Probes what it can and reports what is missing. External calls go through an
injected ``run``/``which`` so the module is fully unit-testable without a cluster.
The conductor (Claude) asks the user for anything in ``missing``.
"""
import shutil
import subprocess


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
