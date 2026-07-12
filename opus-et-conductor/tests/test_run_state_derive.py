import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_state as rs


def test_phase_status_from_validate_maps_completion():
    vj = {"checks": [
        {"status": "phase_completion", "message": "AreTomo", "phase": "3", "done": 5, "total": 5},
        {"status": "phase_completion", "message": "recon", "phase": "5", "done": 2, "total": 5},
        {"status": "phase_completion", "message": "frames", "phase": "1", "done": 0, "total": 5},
        {"status": "pass", "message": "irrelevant"},
    ]}
    got = rs.phase_status_from_validate(vj)
    assert got["3"]["completion"] == "done"
    assert got["5"]["completion"] == "partial"
    assert got["1"]["completion"] == "pending"
    assert got["5"]["done"] == 2 and got["5"]["total"] == 5


def test_phase_status_aggregates_multiple_rows_same_phase():
    vj = {"checks": [
        {"status": "phase_completion", "message": "subtomos", "phase": "7", "done": 5, "total": 5},
        {"status": "phase_completion", "message": "export STAR", "phase": "7", "done": 0, "total": 1},
    ]}
    got = rs.phase_status_from_validate(vj)
    assert got["7"]["done"] == 5 and got["7"]["total"] == 6
    assert got["7"]["completion"] == "partial"


def test_phase_status_done_only_when_all_rows_complete():
    vj = {"checks": [
        {"status": "phase_completion", "message": "subtomos", "phase": "7", "done": 5, "total": 5},
        {"status": "phase_completion", "message": "export STAR", "phase": "7", "done": 1, "total": 1},
    ]}
    got = rs.phase_status_from_validate(vj)
    assert got["7"]["completion"] == "done"
    assert got["7"]["done"] == 6 and got["7"]["total"] == 6


def test_parse_squeue_ids_strips_array_suffix():
    out = "12401\n12402_3\n12402_4\n"
    assert rs.parse_squeue_ids(out) == {"12401", "12402"}


def test_reconcile_jobs_flips_finished_running_to_verifying(tmp_path):
    st = rs.new_state(tmp_path)
    rs.set_phase(st, 3, "running", job_ids=[12401])
    rs.set_phase(st, 5, "running", job_ids=[99999])
    rs.reconcile_jobs(st, active_ids={"12401"})
    assert st["phases"]["3"]["status"] == "running"   # still active
    assert st["phases"]["5"]["status"] == "verifying"  # gone from squeue


def test_exclude_and_active_tomostars(tmp_path):
    st = rs.new_state(tmp_path)
    rs.exclude_tomostar(st, "TS_030")
    rs.exclude_tomostar(st, "TS_030")  # idempotent
    assert st["excluded_tomostar"] == ["TS_030"]
    active = rs.active_tomostars(["TS_026", "TS_030", "TS_031"], st)
    assert active == ["TS_026", "TS_031"]
