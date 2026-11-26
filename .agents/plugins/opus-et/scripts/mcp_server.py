#!/usr/bin/env python3
"""Stdlib-only MCP (Model Context Protocol) stdio server for the OPUS-ET plugin.

Exposes typed cryo-ET tools across the five OPUS-ET skills:
  - Conductor & state: opus_preflight, opus_get_run_state, opus_open_checkpoint,
                       opus_record_checkpoint, opus_exclude_tomostar
  - Validation:        opus_validate_phase
  - Analysis:          opus_extract_config, opus_compute_fsc
  - Visualization:     opus_gen_gallery_cxc, opus_gen_artiax_scene
  - Safety guard:      opus_check_sbatch

Uses newline-delimited JSON-RPC 2.0 over stdin/stdout (MCP stdio transport).
Requires zero third-party packages to run; scientific tools invoke the workspace
`.venv/bin/python` (or `OPUS_ET_PYTHON` / `sys.executable`) via subprocess.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import socket
import subprocess
import uuid
import sys
from pathlib import Path
from typing import Any

SERVER_INFO = {"name": "opus-et", "version": "1.0.0"}
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
HOST = socket.gethostname()

PLUGIN_ROOT = Path(os.environ.get("PLUGIN_ROOT") or Path(__file__).resolve().parent.parent).resolve()
SKILLS_DIR = PLUGIN_ROOT / "skills"


def _skill_path(skill_name: str, *parts: str) -> Path:
    return (SKILLS_DIR / skill_name).resolve().joinpath(*parts)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sci_python() -> str:
    override = os.environ.get("OPUS_ET_PYTHON")
    if override:
        return override
    # Check repo root relative to .agents/plugins/opus-et and current working dir
    candidates = [
        PLUGIN_ROOT.parent.parent.parent / ".venv" / "bin" / "python",
        Path.cwd() / ".venv" / "bin" / "python",
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return sys.executable


# Lazily cached stdlib helper modules from sibling skills & plugin scripts
_MOD_CACHE: dict[str, Any] = {}


def _mod(key: str):
    if key not in _MOD_CACHE:
        targets = {
            "run_state": _skill_path("opus-et-conductor", "scripts", "run_state.py"),
            "preflight": _skill_path("opus-et-conductor", "scripts", "preflight.py"),
            "guard_sbatch": PLUGIN_ROOT / "scripts" / "guard_sbatch.py",
        }
        _MOD_CACHE[key] = _load_module(f"_opus_mcp_{key}", targets[key])
    return _MOD_CACHE[key]


def _gate_summary(state: dict[str, Any]) -> dict[str, bool]:
    rs = _mod("run_state")
    return {gate: bool(rs.gate_approved(state, gate)) for gate, _ in rs.GATES}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _tool_preflight(args: dict[str, Any]) -> dict[str, Any]:
    pf = _mod("preflight")
    known = {
        k: args[k]
        for k in ("warp_dir", "warp_env", "opuset_env", "pytom_env")
        if args.get(k)
    }
    result = pf.probe(known)
    work_dir = args.get("work_dir")
    if work_dir:
        rs = _mod("run_state")
        with rs.edit_state(work_dir) as state:
            state["environment"] = result["environment"]
            state["cluster"] = result["cluster"]
            state["preflight"] = {
                "status": "done" if not result["missing"] else "pending",
                "missing": list(result["missing"]),
            }
            state["environment"]["execution_host"] = HOST
        result["state_saved"] = str(rs.state_path(work_dir))
    return result


def _tool_get_run_state(args: dict[str, Any]) -> dict[str, Any]:
    rs = _mod("run_state")
    work_dir = args["work_dir"]
    if args.get("create_if_missing", False):
        state = rs.init_state(work_dir)
    else:
        state = rs.load_state(work_dir)
    return {
        "state_path": str(rs.state_path(work_dir)),
        "gate_approvals": _gate_summary(state),
        "state": state,
    }


def _tool_open_checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    rs = _mod("run_state")
    work_dir = args["work_dir"]
    with rs.edit_state(work_dir) as state:
        rs.open_checkpoint(state, args["gate"], args["next_phase"])
    path = rs.state_path(work_dir)
    return {
        "state_path": str(path),
        "gate": args["gate"],
        "phase": str(args["next_phase"]),
        "status": state["phases"][str(args["next_phase"])]["status"],
        "gate_approvals": _gate_summary(state),
    }


def _tool_record_checkpoint(args: dict[str, Any]) -> dict[str, Any]:
    rs = _mod("run_state")
    work_dir = args["work_dir"]
    if args["decision"] != "pending":
        raise ValueError("A human must confirm this decision")
    with rs.edit_state(work_dir) as state:
        rs.record_checkpoint(state, args["gate"], "pending", args.get("next_phase"),
                             **args.get("evidence", {}))
    return _checkpoint_result(args, state)


def _checkpoint_result(args, state):
    path = _mod("run_state").state_path(args["work_dir"])
    return {
        "state_path": str(path),
        "gate": args["gate"],
        "decision": args["decision"],
        "gate_approvals": _gate_summary(state),
    }


def _tool_exclude_tomostar(args: dict[str, Any]) -> dict[str, Any]:
    rs = _mod("run_state")
    work_dir = args["work_dir"]
    with rs.edit_state(work_dir) as state:
        rs.exclude_tomostar(state, args["ts_name"])
    path = rs.state_path(work_dir)
    return {
        "state_path": str(path),
        "excluded_tomostar": list(state.get("excluded_tomostar", [])),
    }


def _tool_validate_phase(args: dict[str, Any]) -> dict[str, Any]:
    validate_sh = _skill_path("opus-et-warp", "validate.sh")
    cmd = ["bash", str(validate_sh), "--json"]
    if args.get("phase") is not None:
        cmd += ["--phase", str(args["phase"])]
    if args.get("species_conf"):
        cmd += ["--species", str(args["species_conf"])]
    if args.get("assume_env"):
        cmd.append("--assume-env")
    if args.get("dry_run"):
        cmd.append("--dry-run")
    if args.get("config_only"):
        cmd.append("--config-only")

    env = os.environ.copy()
    if args.get("pipeline_conf"):
        env["PIPELINE_CONF"] = str(args["pipeline_conf"])

    cwd = args.get("work_dir") or None
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    report: Any = None
    phase_completion: dict[str, Any] = {}
    if proc.stdout.strip():
        try:
            report = json.loads(proc.stdout)
            if isinstance(report, dict):
                phase_completion = _mod("run_state").phase_status_from_validate(report)
        except json.JSONDecodeError:
            report = {"raw_stdout": proc.stdout}
    return {
        "returncode": proc.returncode,
        "report": report,
        "phase_completion": phase_completion,
        "stderr": proc.stderr.strip(),
    }


def _tool_extract_config(args: dict[str, Any]) -> dict[str, Any]:
    script = _skill_path("opus-et-analysis", "scripts", "extract_config.py")
    proc = subprocess.run([_sci_python(), str(script), args["config_path"]],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"extract_config exited {proc.returncode}")
    return json.loads(proc.stdout)


def _tool_compute_fsc(args: dict[str, Any]) -> dict[str, Any]:
    script = _skill_path("opus-et-analysis", "scripts", "compute_fsc.py")
    cmd = [
        _sci_python(),
        str(script),
        "--half1", str(args["half1"]),
        "--half2", str(args["half2"]),
        "--apix", str(args["apix"]),
        "-o", str(args["out_prefix"]),
    ]
    if args.get("mask"):
        cmd += ["--mask", str(args["mask"])]
    if args.get("phase_randomize", True) is False:
        cmd.append("--no-phase-randomize")
    if args.get("rand_res") is not None:
        cmd += ["--rand-res", str(args["rand_res"])]
    if args.get("seed") is not None:
        cmd += ["--seed", str(args["seed"])]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"compute_fsc exited {proc.returncode}")
    out_prefix = str(args["out_prefix"])
    return {
        "tsv": f"{out_prefix}_fsc.tsv",
        "png": f"{out_prefix}_fsc.png",
        "stdout": proc.stdout.strip(),
    }


def _tool_gen_gallery_cxc(args: dict[str, Any]) -> dict[str, Any]:
    script = _skill_path("opus-et-visualize", "scripts", "gen_gallery_cxc.py")
    cmd = [
        _sci_python(),
        str(script),
        "--maps-dir", str(args["maps_dir"]),
        "--out", str(args["out"]),
    ]
    if args.get("pattern"):
        cmd += ["--pattern", str(args["pattern"])]
    if args.get("percentile") is not None:
        cmd += ["--percentile", str(args["percentile"])]
    if args.get("level") is not None:
        cmd += ["--level", str(args["level"])]
    if args.get("columns") is not None:
        cmd += ["--columns", str(args["columns"])]
    if args.get("still"):
        cmd += ["--still", str(args["still"])]
    if args.get("movie"):
        cmd += ["--movie", str(args["movie"])]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"gen_gallery_cxc exited {proc.returncode}")
    return {"out_cxc": str(args["out"]), "stdout": proc.stdout.strip()}


def _tool_gen_artiax_scene(args: dict[str, Any]) -> dict[str, Any]:
    script = _skill_path("opus-et-visualize", "scripts", "gen_artiax_scene.py")
    cmd = [
        _sci_python(),
        str(script),
        "--star", str(args["star"]),
        "--tomogram", str(args["tomogram"]),
        "--map", str(args["map_path"]),
        "--coords-angpix", str(args["coords_angpix"]),
        "--out-cxc", str(args["out_cxc"]),
    ]
    opt_value_flags = (
        ("tomo_angpix", "--tomo-angpix"),
        ("origin_angpix", "--origin-angpix"),
        ("stars_dir", "--stars-dir"),
        ("labels", "--labels"),
        ("tomo_name", "--tomo-name"),
        ("contour_level", "--contour-level"),
        ("contour_sd", "--contour-sd"),
        ("bg_color", "--bg-color"),
        ("tilt_x", "--tilt-x"),
        ("still_out", "--still-out"),
        ("movie_out", "--movie-out"),
        ("movie_rock", "--movie-rock"),
        ("movie_step", "--movie-step"),
    )
    for key, flag in opt_value_flags:
        if args.get(key) is not None:
            cmd += [flag, str(args[key])]
    if args.get("silhouettes"):
        cmd.append("--silhouettes")
    if args.get("shadows"):
        cmd.append("--shadows")

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"gen_artiax_scene exited {proc.returncode}")
    return {"out_cxc": str(args["out_cxc"]), "stdout": proc.stdout.strip()}


def _tool_check_sbatch(args: dict[str, Any]) -> dict[str, Any]:
    guard = _mod("guard_sbatch")
    work_dir = args.get("work_dir") or str(Path.cwd())
    payload = {
        "hookEventName": "PreToolUse",
        "cwd": work_dir,
        "workspacePaths": [work_dir],
        "toolCall": {
            "id": "mcp_precheck",
            "name": "run_command",
            "args": {"CommandLine": args["command_line"]},
        },
    }
    return guard.evaluate_hook(payload)


# ---------------------------------------------------------------------------
# Tool registry (MCP schemas + dispatch)
# ---------------------------------------------------------------------------

GATE_ENUM = ["setup", "alignment_qc", "tm_params", "picks_qc", "state_selection", "refine"]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "opus_preflight",
        "description": "Probe the local/cluster environment for WARP fork, conda envs, cryo-ET tools, and SLURM partitions, optionally persisting results to .opus_run_state.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "warp_dir": {"type": "string", "description": "Path to WARP binary directory (alncat fork)."},
                "warp_env": {"type": "string", "description": "WARP conda environment name."},
                "opuset_env": {"type": "string", "description": "OPUS-ET conda environment name."},
                "pytom_env": {"type": "string", "description": "PyTOM conda environment name."},
                "work_dir": {"type": "string", "description": "Optional run directory to persist preflight results."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_get_run_state",
        "description": "Read .opus_run_state.json and summarize gate approvals (Gates 0-4 + tm_params).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_dir": {"type": "string", "description": "Run directory containing .opus_run_state.json."},
                "create_if_missing": {"type": "boolean", "default": False},
            },
            "required": ["work_dir"],
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_open_checkpoint",
        "description": "Park the next pipeline phase at a human gate checkpoint without recording approval.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_dir": {"type": "string"},
                "gate": {"type": "string", "enum": GATE_ENUM},
                "next_phase": {"type": "string", "description": "Phase id parked at this gate (e.g. '1', '6', '7', '8c', 'M')."},
            },
            "required": ["work_dir", "gate", "next_phase"],
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_record_checkpoint",
        "description": "Record an explicit human decision ('approved', 'rejected', or 'pending') for a scientific gate in .opus_run_state.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_dir": {"type": "string"},
                "gate": {"type": "string", "enum": GATE_ENUM},
                "decision": {"type": "string", "enum": ["approved", "rejected", "pending"]},
                "next_phase": {"type": "string", "description": "Optional next phase id to move to 'ready' (approved) or 'checkpoint'."},
                "evidence": {"type": "object", "description": "Optional key-value metadata to attach to the checkpoint record."},
            },
            "required": ["work_dir", "gate", "decision"],
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_exclude_tomostar",
        "description": "Idempotently add a tilt series name to excluded_tomostar in .opus_run_state.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_dir": {"type": "string"},
                "ts_name": {"type": "string", "description": "Tilt-series identifier to exclude (e.g. 'TS_026.tomostar')."},
            },
            "required": ["work_dir", "ts_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_validate_phase",
        "description": "Run opus-et-warp/validate.sh --json and return structured validation checks plus per-phase completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "Phase id (e.g. '1', '3.5', '6', '8b', 'M')."},
                "species_conf": {"type": "string", "description": "Path to species.conf override."},
                "pipeline_conf": {"type": "string", "description": "Path to pipeline.conf override."},
                "work_dir": {"type": "string", "description": "Working directory in which to run validate.sh."},
                "assume_env": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": False},
                "config_only": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_extract_config",
        "description": "Extract training_Apix, downfrac, original_Apix, lattice_D, effective_box_size, and dataset paths from an OPUS-ET config.pkl.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string", "description": "Path to a trusted OPUS-ET config.pkl."},
            },
            "required": ["config_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_compute_fsc",
        "description": "Compute gold-standard masked FSC (with phase-randomization correction by default) between two half-maps for Gate-4 resolution sign-off.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "half1": {"type": "string", "description": "Path to half-map 1 MRC."},
                "half2": {"type": "string", "description": "Path to half-map 2 MRC."},
                "apix": {"type": "number", "description": "Pixel size in Angstroms."},
                "out_prefix": {"type": "string", "description": "Output prefix (writes <prefix>_fsc.tsv and <prefix>_fsc.png)."},
                "mask": {"type": "string", "description": "Optional molecule mask MRC (auto-derived if omitted)."},
                "phase_randomize": {"type": "boolean", "default": True},
                "rand_res": {"type": "number", "description": "Optional phase-randomization cutoff in Angstroms."},
                "seed": {"type": "integer", "default": 0},
            },
            "required": ["half1", "half2", "apix", "out_prefix"],
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_gen_gallery_cxc",
        "description": "Emit a fair-colored, tiled ChimeraX .cxc script for a Gate-3 k-means state gallery (lighting soft after tile).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "maps_dir": {"type": "string"},
                "out": {"type": "string", "description": "Output .cxc file path."},
                "pattern": {"type": "string", "default": "*.mrc"},
                "percentile": {"type": "number", "description": "Per-map voxel percentile contour (e.g. 98)."},
                "level": {"type": "number", "description": "Common absolute contour level."},
                "columns": {"type": "integer", "default": 5},
                "still": {"type": "string"},
                "movie": {"type": "string"},
            },
            "required": ["maps_dir", "out"],
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_gen_artiax_scene",
        "description": "Generate per-state RELION STAR files and a local ChimeraX/ArtiaX .cxc in-cell scene placing a refined map at every particle pose.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "star": {"type": "string"},
                "tomogram": {"type": "string"},
                "map_path": {"type": "string"},
                "coords_angpix": {"type": "number"},
                "out_cxc": {"type": "string"},
                "tomo_angpix": {"type": "number"},
                "origin_angpix": {"type": "number"},
                "stars_dir": {"type": "string"},
                "labels": {"type": "string", "description": "Optional OPUS-ET labels.pkl (attached before tomo_name filter)."},
                "tomo_name": {"type": "string"},
                "contour_level": {"type": "number"},
                "contour_sd": {"type": "number"},
                "bg_color": {"type": "string"},
                "tilt_x": {"type": "number"},
                "silhouettes": {"type": "boolean"},
                "shadows": {"type": "boolean"},
                "still_out": {"type": "string"},
                "movie_out": {"type": "string"},
                "movie_rock": {"type": "integer"},
                "movie_step": {"type": "integer"},
            },
            "required": ["star", "tomogram", "map_path", "coords_angpix", "out_cxc"],
            "additionalProperties": False,
        },
    },
    {
        "name": "opus_check_sbatch",
        "description": "Pre-check a proposed SLURM sbatch command against the OPUS-ET safety guard (demo_replay, ChimeraX local-only rule, and human gate approvals).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command_line": {"type": "string", "description": "Shell command line to inspect."},
                "work_dir": {"type": "string", "description": "Directory containing .opus_run_state.json."},
            },
            "required": ["command_line"],
            "additionalProperties": False,
        },
    },
]

HANDLERS = {
    "opus_preflight": _tool_preflight,
    "opus_get_run_state": _tool_get_run_state,
    "opus_open_checkpoint": _tool_open_checkpoint,
    "opus_record_checkpoint": _tool_record_checkpoint,
    "opus_exclude_tomostar": _tool_exclude_tomostar,
    "opus_validate_phase": _tool_validate_phase,
    "opus_extract_config": _tool_extract_config,
    "opus_compute_fsc": _tool_compute_fsc,
    "opus_gen_gallery_cxc": _tool_gen_gallery_cxc,
    "opus_gen_artiax_scene": _tool_gen_artiax_scene,
    "opus_check_sbatch": _tool_check_sbatch,
}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 / MCP dispatcher
# ---------------------------------------------------------------------------

CLUSTER_TOOLS = {"opus_preflight", "opus_get_run_state", "opus_open_checkpoint",
                 "opus_record_checkpoint", "opus_exclude_tomostar", "opus_validate_phase"}
DESKTOP_TOOLS = {"opus_gen_gallery_cxc", "opus_gen_artiax_scene"}
for tool in TOOLS:
    tool["inputSchema"]["properties"]["expected_host"] = {
        "type": "string", "description": "Optional exact execution hostname from the server handshake."}

INPUT_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in TOOLS}
JSON_TYPES = {"string": (str,), "object": (dict,), "boolean": (bool,),
              "number": (float, int), "integer": (int,)}


def _validated_arguments(schema, arguments):
    """Validate the flat input schemas published by tools/list before dispatch."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    properties = schema["properties"]
    missing = set(schema.get("required", ())) - arguments.keys()
    if missing:
        raise ValueError("missing required arguments: " + ", ".join(sorted(missing)))
    extra = arguments.keys() - properties.keys()
    if extra and not schema.get("additionalProperties", True):
        raise ValueError("unexpected arguments: " + ", ".join(sorted(extra)))

    validated = arguments.copy()
    for key, value in arguments.items():
        rule = properties[key]
        kind = rule["type"]
        # JSON Schema treats an integral JSON number such as 2.0 as an integer.
        if kind == "integer" and isinstance(value, float) and value.is_integer():
            validated[key] = value = int(value)
        if type(value) not in JSON_TYPES[kind]:
            raise ValueError(f"{key} must be a JSON {kind}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"{key} must be one of {rule['enum']}")
    return validated


def _error_response(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_result(req_id, data, *, error=False):
    payload = dict(data, execution_host=HOST)
    return {"jsonrpc": "2.0", "id": req_id, "result": {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2)}], "isError": error}}


class Session:
    def __init__(self, role="all"):
        self.role = role
        excluded = {"all": set(), "desktop": CLUSTER_TOOLS, "cluster": DESKTOP_TOOLS}[role]
        self.tools = {tool["name"]: tool for tool in TOOLS if tool["name"] not in excluded}
        self.version = None
        self.ready = False
        self.can_elicit = False
        self.pending = {}

    def handle(self, msg):
        if isinstance(msg, list):
            if not msg or self.version != "2025-03-26" or not self.ready:
                return _error_response(None, -32600, "Batch unavailable before initialization or for this revision")
            replies = [self._message(item) for item in msg]
            return [r for r in replies if r is not None] or None
        return self._message(msg)

    def _message(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error_response(None, -32600, "Expected JSON-RPC 2.0 object")
        req_id = msg.get("id")
        if "id" in msg and type(req_id) not in (int, str):
            return _error_response(None, -32600, "Invalid request id")
        if "method" not in msg and ("result" in msg or "error" in msg):
            return self._confirmation_response(req_id, msg)
        method = msg.get("method")
        if not isinstance(method, str):
            return _error_response(req_id, -32600, "Expected method name")
        params = msg.get("params", {})
        if req_id is None:
            if method == "notifications/initialized" and self.version is not None:
                self.ready = True
            if method == "notifications/cancelled" and isinstance(params, dict):
                self.pending = {k: v for k, v in self.pending.items() if v[0] != params.get("requestId")}
            return None
        if not isinstance(params, dict):
            return _error_response(req_id, -32602, "Expected object params")
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "initialize":
            if self.version is not None:
                return _error_response(req_id, -32600, "Session already initialized")
            version = params.get("protocolVersion")
            caps = params.get("capabilities")
            if not isinstance(version, str) or not isinstance(caps, dict) or not isinstance(params.get("clientInfo"), dict):
                return _error_response(req_id, -32602, "Expected protocolVersion, capabilities and clientInfo")
            self.version = version if version in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            elicitation = caps.get("elicitation")
            self.can_elicit = (self.version == "2025-06-18" and isinstance(elicitation, dict)
                               and (not elicitation or "form" in elicitation))
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": self.version, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO,
                "instructions": f"Execution host {HOST}, role {self.role}. Paths and tools run on this host."}}
        if not self.ready:
            return _error_response(req_id, -32600, "Complete the MCP initialization handshake first")
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": list(self.tools.values())}}
        if method != "tools/call":
            return _error_response(req_id, -32601, f"Unknown method {method}")
        name = params.get("name")
        if not isinstance(name, str) or name not in self.tools:
            return _error_response(req_id, -32602, f"Unknown tool on {HOST} ({self.role}): {name}")
        try:
            args = _validated_arguments(INPUT_SCHEMAS[name], params.get("arguments", {}))
        except ValueError as exc:
            return _error_response(req_id, -32602, str(exc))
        try:
            expected = args.pop("expected_host", HOST)
            if expected != HOST:
                raise ValueError(f"Requested host {expected}, executing on {HOST}")
            if name == "opus_record_checkpoint" and args["decision"] != "pending":
                return self._ask_scientist(req_id, args)
            return _tool_result(req_id, HANDLERS[name](args))
        except Exception as exc:
            return _tool_result(req_id, {"error": str(exc)}, error=True)

    def _ask_scientist(self, req_id, args):
        rs = _mod("run_state")
        work_dir = Path(args["work_dir"]).resolve()
        state = rs.load_state(work_dir) if rs.state_path(work_dir).exists() else rs.new_state(work_dir)
        rs._check_project(state, work_dir)
        if not self.can_elicit:
            raise ValueError("This client cannot request human confirmation. Open the checkpoint and "
                             "have the scientist run run_state.py confirm WORK_DIR GATE in a terminal.")
        latest = next((c for c in reversed(state["checkpoints"]) if c.get("gate") == args["gate"]), {})
        if latest.get("decision") == "pending":
            args = dict(args)
            if "next_phase" not in args and "phase" in latest:
                args["next_phase"] = latest["phase"]
            prior_evidence = {k: v for k, v in latest.items()
                              if k not in ("gate", "decision", "phase", "confirmation")}
            args["evidence"] = dict(prior_evidence, **args.get("evidence", {}))
        key = "gate-" + uuid.uuid4().hex
        self.pending[key] = (req_id, args, rs.gate_generation(state, args["gate"]))
        detail = {k: args[k] for k in ("gate", "decision", "next_phase", "evidence") if k in args}
        return {"jsonrpc": "2.0", "id": key, "method": "elicitation/create", "params": {
            "message": f"Record this scientific decision on {HOST} for {work_dir}?\n" + json.dumps(detail, indent=2),
            "requestedSchema": {"type": "object", "properties": {
                "reviewer": {"type": "string", "title": "Your name or initials", "minLength": 1}},
                "required": ["reviewer"]}}}

    def _confirmation_response(self, key, msg):
        pending = self.pending.pop(key, None)
        if pending is None:
            return None
        req_id, args, generation = pending
        try:
            result = msg.get("result")
            if "error" in msg or not isinstance(result, dict) or result.get("action") != "accept":
                raise ValueError("Checkpoint decision was not confirmed")
            content = result.get("content")
            reviewer = content.get("reviewer") if isinstance(content, dict) else None
            if not isinstance(reviewer, str) or not reviewer.strip():
                raise ValueError("Missing reviewer in confirmation")
            state = _mod("run_state").confirm_gate(
                args["work_dir"], args["gate"], args["decision"], args.get("next_phase"),
                args.get("evidence", {}), generation, reviewer.strip(), "mcp_elicitation")
            return _tool_result(req_id, _checkpoint_result(args, state))
        except Exception as exc:
            return _tool_result(req_id, {"error": str(exc)}, error=True)


def serve_stdio(stdin=None, stdout=None, *, role="all") -> int:
    in_stream = stdin if stdin is not None else sys.stdin
    out_stream = stdout if stdout is not None else sys.stdout
    session = Session(role)
    for line in in_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error_response(None, -32700, f"parse error: {exc}")
        else:
            response = session.handle(message)
        if response is not None:
            out_stream.write(json.dumps(response) + "\n")
            out_stream.flush()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("all", "desktop", "cluster"), default="all")
    raise SystemExit(serve_stdio(role=parser.parse_args().role))
