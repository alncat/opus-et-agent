import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_state as rs


def test_new_state_shape(tmp_path):
    st = rs.new_state(tmp_path, species=["ribosome"])
    assert st["schema_version"] == rs.SCHEMA_VERSION
    assert st["project"]["work_dir"] == str(tmp_path)
    assert st["project"]["species"] == ["ribosome"]
    assert st["preflight"]["status"] == "pending"
    assert st["phases"] == {}


def test_save_then_load_roundtrip(tmp_path):
    st = rs.new_state(tmp_path)
    rs.save_state(st)
    assert (tmp_path / rs.STATE_FILENAME).exists()
    loaded = rs.load_state(tmp_path)
    assert loaded == st


def test_load_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        rs.load_state(tmp_path)


def test_load_schema_mismatch_raises(tmp_path):
    p = rs.state_path(tmp_path)
    p.write_text(json.dumps({"schema_version": 999}))
    with pytest.raises(ValueError):
        rs.load_state(tmp_path)


def test_init_is_idempotent(tmp_path):
    a = rs.init_state(tmp_path, species=["ribosome"])
    b = rs.init_state(tmp_path, species=["ignored-second-time"])
    assert b["project"]["species"] == ["ribosome"]  # did not overwrite


def test_set_phase_updates_status_and_fields(tmp_path):
    st = rs.new_state(tmp_path)
    rs.set_phase(st, 3, "running", job_ids=[12401])
    assert st["phases"]["3"]["status"] == "running"
    assert st["phases"]["3"]["job_ids"] == ["12401"]


def test_set_phase_rejects_unknown_status(tmp_path):
    st = rs.new_state(tmp_path)
    with pytest.raises(ValueError):
        rs.set_phase(st, 3, "bogus")


def test_save_is_atomic_no_tmp_left(tmp_path):
    st = rs.new_state(tmp_path)
    rs.save_state(st)
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_gate_decision_survives_restart_and_rejection_revokes_approval(tmp_path):
    st = rs.init_state(tmp_path)
    rs.open_checkpoint(st, 'picks_qc', '7')
    assert st['phases']['7']['status'] == 'checkpoint'
    rs.record_checkpoint(st, 'picks_qc', 'approved', phase='7', min_score=0.2)
    rs.save_state(st)
    st = rs.load_state(tmp_path)
    assert rs.gate_approved(st, 'picks_qc')
    assert st['phases']['7']['status'] == 'ready'
    rs.open_checkpoint(st, 'picks_qc', '7')
    assert not rs.gate_approved(st, 'picks_qc')
    rs.record_checkpoint(st, 'picks_qc', 'rejected', phase='7')
    assert not rs.gate_approved(st, 'picks_qc')
    assert st['phases']['7']['status'] == 'checkpoint'


def test_job_keys_are_normalized_without_iterating_digits(tmp_path):
    st = rs.new_state(tmp_path)
    rs.set_phase(st, '7', 'running', job_id=12345)
    assert st['phases']['7']['job_ids'] == ['12345']
    assert rs.phase_job_ids({'jobs': [12, 34]}) == ['12', '34']
    assert rs.phase_job_ids({'job_ids': '12345'}) == ['12345']


def test_failed_queue_query_does_not_advance_but_empty_success_does(tmp_path):
    st = rs.new_state(tmp_path)
    rs.set_phase(st, '7', 'running', job_ids=[123])
    rs.reconcile_jobs(st, [], query_succeeded=False)
    assert st['phases']['7']['status'] == 'running'
    rs.reconcile_jobs(st, [])
    assert st['phases']['7']['status'] == 'verifying'
