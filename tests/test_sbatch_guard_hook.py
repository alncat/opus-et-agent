"""Tests for the Antigravity PreToolUse SLURM safety hook (.agents/scripts/guard_sbatch.py)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".agents" / "plugins" / "opus-et"
HOOK_SCRIPT = PLUGIN_DIR / "scripts" / "guard_sbatch.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("guard_sbatch", HOOK_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


guard = _load_hook()


def _payload(cmd: str, cwd: Path, workspaces: list[Path] | None = None, tool: str = "run_command"):
    return {
        "hookEventName": "PreToolUse",
        "cwd": str(cwd),
        "workspacePaths": [str(w) for w in (workspaces or [cwd])],
        "toolCall": {
            "id": "toolu_test",
            "name": tool,
            "args": {"CommandLine": cmd},
        },
    }


def _write_state(work_dir: Path, **overrides) -> Path:
    state = {
        "schema_version": 1,
        "project": {"work_dir": str(work_dir)},
        "phases": {},
        "checkpoints": [],
    }
    state.update(overrides)
    path = work_dir / ".opus_run_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


@pytest.mark.parametrize("cmd", [
    "pytest -q",
    "grep -rn sbatch opus-et-warp/",
    'echo "next: sbatch scripts/warp_ts_ctf.slurm"',
    "git commit -m 'fix sbatch guard'",
    "squeue --noheader -o %i",
])
def test_non_sbatch_commands_allowed(tmp_path, cmd):
    res = guard.evaluate_hook(_payload(cmd, tmp_path))
    assert res == {"decision": "allow"}


def test_non_run_command_tool_allowed(tmp_path):
    res = guard.evaluate_hook(_payload("sbatch job.slurm", tmp_path, tool="read_file"))
    assert res == {"decision": "allow"}


@pytest.mark.parametrize("cmd", [
    "sbatch --wrap='ChimeraX --exit scene.cxc'",
    "sbatch opus-et-visualize/scripts/gen_artiax_scene.py",
    "ssh cluster 'sbatch demo/gen_gallery_cxc.py'",
    "sbatch build_insitu_scene.slurm",
])
def test_blocks_sbatch_on_local_chimerax_viz(tmp_path, cmd):
    _write_state(tmp_path, checkpoints=[{"gate": "refine", "decision": "approved"}])
    res = guard.evaluate_hook(_payload(cmd, tmp_path))
    assert res["decision"] == "deny"
    assert "ChimeraX/ArtiaX" in res["reason"]


def test_blocks_sbatch_in_demo_replay_mode(tmp_path):
    _write_state(
        tmp_path,
        demo_replay=True,
        checkpoints=[{"gate": "setup", "decision": "approved"}],
    )
    res = guard.evaluate_hook(
        _payload("sbatch opus-et-warp/scripts/warp_frameseries_import.slurm", tmp_path)
    )
    assert res["decision"] == "deny"
    assert "demo_replay" in res["reason"]


def test_blocks_ssh_sbatch_in_demo_replay_mode(tmp_path):
    _write_state(tmp_path, demo_replay=True)
    res = guard.evaluate_hook(
        _payload('ssh cluster "cd /remote/run && sbatch scripts/warp_ts_ctf.slurm"', tmp_path)
    )
    assert res["decision"] == "deny"
    assert "demo_replay" in res["reason"]


def test_blocks_gated_script_when_gate_unapproved(tmp_path):
    _write_state(
        tmp_path,
        checkpoints=[
            {"gate": "setup", "decision": "approved"},
            {"gate": "alignment_qc", "decision": "pending"},
        ],
    )
    res = guard.evaluate_hook(
        _payload("sbatch opus-et-warp/scripts/gen_template_from_mrc.slurm", tmp_path)
    )
    assert res["decision"] == "deny"
    assert "alignment_qc" in res["reason"]


def test_blocks_when_latest_gate_decision_is_rejected(tmp_path):
    _write_state(
        tmp_path,
        checkpoints=[
            {"gate": "picks_qc", "decision": "approved"},
            {"gate": "picks_qc", "decision": "rejected"},
        ],
    )
    res = guard.evaluate_hook(
        _payload("sbatch opus-et-warp/scripts/warp_export_particles.slurm", tmp_path)
    )
    assert res["decision"] == "deny"
    assert "picks_qc" in res["reason"]


def test_blocks_m_refinement_without_gate4_approval(tmp_path):
    _write_state(
        tmp_path,
        checkpoints=[{"gate": "state_selection", "decision": "approved"}],
    )
    res = guard.evaluate_hook(
        _payload("sbatch --gres=gpu:4 opus-et-warp/scripts/warp_m_refine.slurm", tmp_path)
    )
    assert res["decision"] == "deny"
    assert "refine" in res["reason"]


def test_allows_gated_script_when_gate_approved(tmp_path):
    _write_state(
        tmp_path,
        checkpoints=[
            {"gate": "alignment_qc", "decision": "approved"},
            {"gate": "tm_params", "decision": "approved"},
        ],
    )
    (tmp_path / "pipeline.conf").write_text(f'WORK_DIR="{tmp_path}"\n')
    res = guard.evaluate_hook(
        _payload(
            'cd "' + str(tmp_path) + '" && sbatch --export=ALL,SKILL_DIR="' + str(tmp_path) + '" /s/scripts/run_tm_sequential.slurm',
            tmp_path,
        )
    )
    assert res == {"decision": "allow"}


def test_allows_legacy_checkpoint_without_decision_field(tmp_path):
    _write_state(tmp_path, checkpoints=[{"gate": "setup"}])
    res = guard.evaluate_hook(
        _payload("sbatch opus-et-warp/scripts/warp_frameseries_import.slurm", tmp_path)
    )
    assert res == {"decision": "allow"}


def test_finds_state_via_work_dir_assignment_in_command(tmp_path):
    run_dir = tmp_path / "run_a"
    run_dir.mkdir()
    _write_state(run_dir, demo_replay=True)
    res = guard.evaluate_hook(
        _payload(
            f"WORK_DIR={run_dir} sbatch opus-et-warp/scripts/warp_ts_ctf.slurm",
            tmp_path,
        )
    )
    assert res["decision"] == "deny"
    assert "demo_replay" in res["reason"]


def test_force_asks_when_no_local_run_state_exists(tmp_path):
    res = guard.evaluate_hook(
        _payload('ssh cluster "sbatch scripts/warp_ts_ctf.slurm"', tmp_path)
    )
    assert res["decision"] == "force_ask"
    assert ".opus_run_state.json" in res["reason"]


def test_force_asks_when_state_json_is_corrupted(tmp_path):
    (tmp_path / ".opus_run_state.json").write_text("{not-json", encoding="utf-8")
    res = guard.evaluate_hook(
        _payload("sbatch opus-et-warp/scripts/warp_ts_ctf.slurm", tmp_path)
    )
    assert res["decision"] == "force_ask"


def test_cli_stdio_contract_matches_hooks_json_working_dir(tmp_path):
    _write_state(tmp_path, demo_replay=True)
    proc = subprocess.run(
        [sys.executable, "./scripts/guard_sbatch.py"],
        cwd=PLUGIN_DIR,
        input=json.dumps(_payload("sbatch opus-et-warp/scripts/warp_ts_ctf.slurm", tmp_path)),
        text=True,
        capture_output=True,
        check=True,
    )
    out = json.loads(proc.stdout)
    assert out["decision"] == "deny"
    assert "demo_replay" in out["reason"]


def test_tool_cwd_selects_project_without_workspace_fallback(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _write_state(tmp_path, checkpoints=[{"gate": "setup"}])
    event = _payload("sbatch warp_ts_ctf.slurm", tmp_path)
    event["toolCall"]["args"]["Cwd"] = str(other)
    assert guard.evaluate_hook(event)["decision"] == "force_ask"
    _write_state(other)
    assert guard.evaluate_hook(event)["decision"] == "deny"


def test_each_submission_checks_its_own_project(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    _write_state(tmp_path, checkpoints=[{"gate": "setup"}])
    _write_state(other)
    command = "sbatch warp_ts_ctf.slurm && cd other && sbatch warp_ts_ctf.slurm"
    assert guard.evaluate_hook(_payload(command, tmp_path))["decision"] == "deny"


def test_local_approval_does_not_authorize_remote_job(tmp_path):
    _write_state(tmp_path, checkpoints=[{"gate": "setup"}])
    event = _payload("ssh super 'sbatch warp_ts_ctf.slurm'", tmp_path)
    assert guard.evaluate_hook(event)["decision"] == "force_ask"


def test_unknown_remote_state_cannot_mask_local_replay(tmp_path):
    other = tmp_path / "replay"
    other.mkdir()
    _write_state(other, demo_replay=True)
    cmd = "ssh super 'sbatch job.slurm' && cd replay && sbatch job.slurm"
    assert guard.evaluate_hook(_payload(cmd, tmp_path))["decision"] == "deny"


def test_selection_requires_gate3(tmp_path):
    _write_state(tmp_path, checkpoints=[{"gate": "state_selection", "decision": "pending"}])
    event = _payload("sbatch select_state.slurm", tmp_path)
    assert guard.evaluate_hook(event)["decision"] == "deny"
    _write_state(tmp_path, checkpoints=[{"gate": "state_selection", "decision": "approved"}])
    assert guard.evaluate_hook(event)["decision"] == "allow"


def test_only_single_tomo_pilot_can_run_before_tm_params(tmp_path):
    event = _payload("sbatch tm_pilot.slurm TS_034", tmp_path)
    _write_state(tmp_path)
    assert guard.evaluate_hook(event)["decision"] == "deny"
    _write_state(tmp_path, checkpoints=[{"gate": "alignment_qc"}])
    assert guard.evaluate_hook(event)["decision"] == "allow"
    for script in ("gen_tm_jobs_aretomo", "run_tm_sequential", "extract_tm_candidates_parallel"):
        assert guard.evaluate_hook(_payload(f"sbatch {script}.slurm", tmp_path))["decision"] == "deny"


def test_configured_pipeline_project_owns_approvals(tmp_path):
    skill = tmp_path / "installed-skill"
    run = tmp_path / "run"
    skill.mkdir()
    run.mkdir()
    _write_state(tmp_path, checkpoints=[{"gate": "setup"}])
    _write_state(run)
    (skill / "pipeline.conf").write_text(f'WORK_DIR="{run}"\n')
    cmd = f"sbatch --export=ALL,SKILL_DIR={skill} {skill}/scripts/warp_ts_ctf.slurm"
    assert guard.evaluate_hook(_payload(cmd, tmp_path))["decision"] == "deny"


def test_relative_cd_and_slurm_chdir_are_local_to_each_call(tmp_path):
    active = tmp_path / "active"
    active.mkdir()
    (active / "pending").mkdir()
    _write_state(active, checkpoints=[{"gate": "setup"}])
    _write_state(active / "pending")
    cmd = "cd active && sbatch warp_ts_ctf.slurm && sbatch --chdir=pending warp_ts_ctf.slurm"
    assert guard.evaluate_hook(_payload(cmd, tmp_path))["decision"] == "deny"


def test_hook_process_environment_cannot_supply_project_approval(tmp_path, monkeypatch):
    another = tmp_path / "another"
    another.mkdir()
    _write_state(another, checkpoints=[{"gate": "setup"}])
    monkeypatch.setenv("WORK_DIR", str(another))
    assert guard.evaluate_hook(_payload("sbatch warp_ts_ctf.slurm", tmp_path))["decision"] == "force_ask"


def test_conditional_directory_change_cannot_supply_approval(tmp_path):
    another = tmp_path / "another"
    another.mkdir()
    _write_state(another, checkpoints=[{"gate": "setup"}])
    event = _payload("test -d data || cd another; sbatch warp_ts_ctf.slurm", tmp_path)
    assert guard.evaluate_hook(event)["decision"] == "force_ask"
