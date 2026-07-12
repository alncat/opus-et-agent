import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WARP = REPO / "opus-et-warp"


def _minimal_pipeline_conf(tmp_path):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    conf = tmp_path / "pipeline.conf"
    conf.write_text(
        "WORK_DIR={work}\n"
        "WARP_DIR=/nonexistent/publish\n"
        "CONDA_LIB=/nonexistent/lib\n"
        "ANGPIX=1.0\n"
        "EXPOSURE=2.0\n"
        "FILE_EXTENSION=.eer\n"
        "FRAME_MODE=fs_motion_and_ctf\n"
        "CTF_RANGE_MAX=4.0\n"
        "CTF_DEFOCUS_MAX=6\n"
        "CTF_GRID=2x2x1\n"
        "BINNING_FACTOR=4\n"
        "ALIGN_ANGPIX=4.0\n"
        "TOMO_DIM_Z=2000\n"
        "MIN_INTENSITY=0\n"
        "TOMOSTAR_DIR={work}/tomostar\n"
        "TILTSTACK_DIR={work}/warp_tiltseries/tiltstack\n"
        "FRAMESERIES_DIR={work}/warp_frameseries/average\n"
        "TEMPLATES_DIR={work}/templates\n".format(work=work)
    )
    return conf, work


def _run_validate_json(tmp_path):
    """Run validate.sh --json --phase 1 and return parsed JSON data and return code."""
    conf, _ = _minimal_pipeline_conf(tmp_path)
    proc = subprocess.run(
        ["bash", str(WARP / "validate.sh"), "--json", "--phase", "1"],
        capture_output=True, text=True,
        env={**os.environ, "PIPELINE_CONF": str(conf)},  # inherit PATH so python3/awk resolve
    )
    # stdout must be exactly one parseable JSON object, no ANSI/human text
    data = json.loads(proc.stdout)
    return data, proc.returncode


def test_json_mode_emits_valid_json_only(tmp_path):
    data, returncode = _run_validate_json(tmp_path)
    assert data["phase"] == "1"
    assert isinstance(data["errors"], int)
    assert isinstance(data["checks"], list)
    assert all("status" in c and "message" in c for c in data["checks"])
    # exit code equals error count
    assert returncode == data["errors"]


def test_json_mode_emits_phase_completion_data(tmp_path):
    data, _ = _run_validate_json(tmp_path)
    # At least one check should have status == "phase_completion"
    phase_completion_checks = [c for c in data["checks"] if c.get("status") == "phase_completion"]
    assert len(phase_completion_checks) > 0, "Expected at least one phase_completion check"
    # For each phase_completion check, verify data fields
    for check in phase_completion_checks:
        assert isinstance(check.get("done"), int), f"'done' must be int, got {type(check.get('done'))}"
        assert isinstance(check.get("total"), int), f"'total' must be int, got {type(check.get('total'))}"
        assert "phase" in check, "'phase' must be present in check"


def _run_validate(tmp_path, *extra):
    """Run validate.sh --json --phase 1 with optional extra flags; return parsed JSON."""
    conf, _ = _minimal_pipeline_conf(tmp_path)
    proc = subprocess.run(
        ["bash", str(WARP / "validate.sh"), "--json", "--phase", "1", *extra],
        capture_output=True, text=True,
        env={**os.environ, "PIPELINE_CONF": str(conf)},
    )
    return json.loads(proc.stdout)


def test_assume_env_skips_env_and_tool_checks(tmp_path):
    # WARP_DIR/CONDA_LIB in the minimal conf are bogus, so the tool-existence
    # checks fail without --assume-env and must be skipped with it (preflight owns them).
    off = _run_validate(tmp_path)
    on = _run_validate(tmp_path, "--assume-env")
    assert any(c["status"] == "fail" and "WarpTools not found" in c["message"] for c in off["checks"]), \
        "expected a WarpTools-not-found failure without --assume-env"
    assert not any("WarpTools not found" in c["message"] for c in on["checks"]), \
        "--assume-env must skip the WarpTools existence check"
    # Errors strictly decrease once env/tool failures are removed.
    assert on["errors"] < off["errors"]
