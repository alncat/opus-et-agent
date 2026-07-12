import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import preflight as pf


class FakeProc:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def make_run(table):
    """table: dict mapping the first two argv tokens -> FakeProc."""
    def run(cmd):
        key = " ".join(cmd[:2])
        return table.get(key, FakeProc("", 1))
    return run


def test_detect_conda_envs_parses_list():
    run = make_run({"conda env": FakeProc(
        "# conda environments:\nbase   /opt/conda\nwarp_build  /opt/conda/envs/warp_build\n"
    )})
    envs = pf.detect_conda_envs(run)
    assert "base" in envs and "warp_build" in envs


def test_check_warp_fork_true_when_flag_present():
    run = make_run({"/w/WarpTools ts_export_particles": FakeProc(
        "  --dont_correct_ctf_3d   skip 3D CTF\n  --output_ctf_csv   ...\n"
    )})
    assert pf.check_warp_fork("/w", run) is True


def test_check_warp_fork_false_when_flag_absent():
    run = make_run({"/w/WarpTools ts_export_particles": FakeProc("  --box   box size\n")})
    assert pf.check_warp_fork("/w", run) is False


def test_probe_reports_missing_when_nothing_found():
    run = make_run({})            # every command "fails"
    result = pf.probe(known={}, run=run, which=lambda x: None)
    assert "warp_dir" in result["missing"]
    assert result["environment"]["discovered"] is False


def test_probes_degrade_instead_of_raising_when_binary_missing():
    def raising_run(cmd):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: {cmd[0]!r}")

    assert pf.detect_conda_envs(raising_run) == []
    assert pf.detect_partitions(raising_run) == []
    assert pf.check_warp_fork("/w", raising_run) is False

    result = pf.probe(known={"warp_dir": "/w"}, run=raising_run, which=lambda tool: None)
    assert result["environment"]["discovered"] is False
    assert result["missing"]


def test_probe_uses_known_hints_and_marks_discovered():
    run = make_run({
        "conda env": FakeProc("base /o\nopuset_env /o/e/opuset_env\npytom_env /o/e/pytom_env\nwarp_build /o/e/warp_build\n"),
        "/w/WarpTools ts_export_particles": FakeProc("--dont_correct_ctf_3d x\n"),
        "sinfo -h": FakeProc("gpu\nnormal\n"),
    })
    known = {"warp_dir": "/w", "warp_env": "warp_build",
             "opuset_env": "opuset_env", "pytom_env": "pytom_env"}
    result = pf.probe(known=known, run=run, which=lambda x: "/usr/bin/" + x)
    assert result["environment"]["warp_fork_ok"] is True
    assert result["environment"]["discovered"] is True
    assert "gpu" in result["cluster"]["partitions"]
    assert result["missing"] == []
