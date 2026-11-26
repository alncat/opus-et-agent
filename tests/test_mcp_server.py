"""Tests for the OPUS-ET plugin MCP stdio server (.agents/plugins/opus-et/scripts/mcp_server.py)."""
from __future__ import annotations

import importlib.util
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path

import mrcfile
import numpy as np
import pandas as pd
import pytest
import starfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / ".agents" / "plugins" / "opus-et"
SERVER_SCRIPT = PLUGIN_DIR / "scripts" / "mcp_server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location("opus_et_mcp_server", SERVER_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


srv = _load_server()


def _session(*, elicit=False):
    session = srv.Session()
    session.handle(_initialize("2025-06-18", {"elicitation": {}} if elicit else {}))
    session.handle(READY)
    return session


def _call_tool(name: str, arguments: dict, *, reviewer=None):
    session = _session(elicit=reviewer is not None)
    resp = session.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    if reviewer is not None:
        assert resp["method"] == "elicitation/create"
        resp = session.handle({"jsonrpc": "2.0", "id": resp["id"], "result": {
            "action": "accept", "content": {"reviewer": reviewer}}})
    assert resp is not None
    assert "result" in resp, resp
    result = resp["result"]
    payload = json.loads(result["content"][0]["text"])
    return result["isError"], payload


def test_initialize_and_tools_list():
    session = srv.Session()
    init = session.handle(_initialize("2025-03-26"))
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["serverInfo"]["name"] == "opus-et"
    assert "tools" in init["result"]["capabilities"]

    # Notification produces no response
    assert session.handle(READY) is None

    listed = session.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    expected = {
        "opus_preflight",
        "opus_get_run_state",
        "opus_open_checkpoint",
        "opus_record_checkpoint",
        "opus_exclude_tomostar",
        "opus_validate_phase",
        "opus_extract_config",
        "opus_compute_fsc",
        "opus_gen_gallery_cxc",
        "opus_gen_artiax_scene",
        "opus_check_sbatch",
    }
    assert expected == names


def test_run_state_checkpoint_and_sbatch_guard_flow(tmp_path):
    # Initialize + open Gate 1 checkpoint
    err, res = _call_tool("opus_open_checkpoint", {
        "work_dir": str(tmp_path),
        "gate": "alignment_qc",
        "next_phase": "6",
    })
    assert not err
    assert res["status"] == "checkpoint"
    assert res["gate_approvals"]["alignment_qc"] is False

    # Pre-check sbatch should DENY before approval
    err, guard_res = _call_tool("opus_check_sbatch", {
        "work_dir": str(tmp_path),
        "command_line": "sbatch opus-et-warp/scripts/gen_template_from_mrc.slurm",
    })
    assert not err
    assert guard_res["decision"] == "deny"
    assert "alignment_qc" in guard_res["reason"]

    # Record Gate 1 approval + exclude a bad tilt series
    err, rec = _call_tool("opus_record_checkpoint", {
        "work_dir": str(tmp_path),
        "gate": "alignment_qc",
        "decision": "approved",
        "next_phase": "6",
        "evidence": {"kept": ["TS_001.tomostar"]},
    }, reviewer="Test Scientist")
    assert not err
    assert rec["gate_approvals"]["alignment_qc"] is True

    err, excl = _call_tool("opus_exclude_tomostar", {
        "work_dir": str(tmp_path),
        "ts_name": "TS_099.tomostar",
    })
    assert not err
    assert excl["excluded_tomostar"] == ["TS_099.tomostar"]

    # Now sbatch check passes
    err, guard_ok = _call_tool("opus_check_sbatch", {
        "work_dir": str(tmp_path),
        "command_line": "sbatch opus-et-warp/scripts/gen_template_from_mrc.slurm",
    })
    assert not err
    assert guard_ok["decision"] == "allow"
    assert guard_ok["execution_host"] == srv.HOST

    # Read back full state
    err, state_res = _call_tool("opus_get_run_state", {"work_dir": str(tmp_path)})
    assert not err
    assert state_res["state"]["phases"]["6"]["status"] == "ready"
    assert state_res["state"]["excluded_tomostar"] == ["TS_099.tomostar"]


def test_extract_config_tool(tmp_path):
    cfg_path = tmp_path / "config.pkl"
    with open(cfg_path, "wb") as fh:
        pickle.dump(
            {
                "lattice_args": {"D": 65},
                "model_args": {"Apix": 3.92, "zdim": 8},
                "dataset_args": {
                    "downfrac": 0.5,
                    "particles": "ribo.star",
                    "poses": "pose.pkl",
                },
            },
            fh,
        )
    err, res = _call_tool("opus_extract_config", {"config_path": str(cfg_path)})
    assert not err
    assert res["effective_box_size"] == 64
    assert res["original_Apix"] == pytest.approx(1.96)
    assert res["zdim"] == 8


def test_validate_phase_config_only_tool(tmp_path):
    pipe = ROOT / "opus-et-warp" / "pipeline.example.conf"
    spec = ROOT / "opus-et-warp" / "species.example.conf"
    err, res = _call_tool("opus_validate_phase", {
        "config_only": True,
        "pipeline_conf": str(pipe),
        "species_conf": str(spec),
        "work_dir": str(tmp_path),
    })
    assert not err
    assert isinstance(res["report"], dict)
    assert "checks" in res["report"]


def test_visualization_and_fsc_tools(tmp_path):
    # 1. Gallery .cxc
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    (maps_dir / "vol_1.mrc").write_bytes(b"")
    (maps_dir / "vol_2.mrc").write_bytes(b"")
    gallery_cxc = tmp_path / "gallery.cxc"
    err, gal = _call_tool("opus_gen_gallery_cxc", {
        "maps_dir": str(maps_dir),
        "out": str(gallery_cxc),
        "columns": 2,
    })
    assert not err
    assert gallery_cxc.is_file()
    assert "2 maps" in gal["stdout"]

    # 2. ArtiaX scene .cxc
    star = tmp_path / "picks.star"
    starfile.write(
        pd.DataFrame({
            "rlnMicrographName": ["TS_001.tomostar"],
            "rlnCoordinateX": [10.0],
            "rlnCoordinateY": [12.0],
            "rlnCoordinateZ": [14.0],
            "rlnAngleRot": [0.0],
            "rlnAngleTilt": [0.0],
            "rlnAnglePsi": [0.0],
        }),
        star,
        overwrite=True,
    )
    scene_cxc = tmp_path / "scene.cxc"
    err, scene = _call_tool("opus_gen_artiax_scene", {
        "star": str(star),
        "tomogram": "TS_001.mrc",
        "map_path": "ref.mrc",
        "coords_angpix": 7.84,
        "out_cxc": str(scene_cxc),
    })
    assert not err
    assert scene_cxc.is_file()
    assert "1 particles" in scene["stdout"]

    # 3. Gold-standard FSC between two identical synthetic half-maps
    rng = np.random.default_rng(0)
    vol = np.zeros((16, 16, 16), dtype=np.float32)
    vol[5:11, 5:11, 5:11] = 1.0
    h1 = tmp_path / "h1.mrc"
    h2 = tmp_path / "h2.mrc"
    with mrcfile.new(h1, overwrite=True) as m:
        m.set_data((vol + 0.05 * rng.standard_normal(vol.shape)).astype(np.float32))
    with mrcfile.new(h2, overwrite=True) as m:
        m.set_data((vol + 0.05 * rng.standard_normal(vol.shape)).astype(np.float32))
    fsc_prefix = tmp_path / "gate4"
    err, fsc = _call_tool("opus_compute_fsc", {
        "half1": str(h1),
        "half2": str(h2),
        "apix": 4.0,
        "out_prefix": str(fsc_prefix),
        "phase_randomize": False,
    })
    assert not err
    assert Path(fsc["tsv"]).is_file()
    assert Path(fsc["png"]).is_file()
    assert "FSC=0.143" in fsc["stdout"]


def test_unknown_tool_and_error_handling(tmp_path):
    resp = _session().handle({
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "no_such_tool", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602

    err, payload = _call_tool("opus_get_run_state", {"work_dir": str(tmp_path / "missing")})
    assert err is True
    assert "error" in payload


def test_stdio_transport_end_to_end_with_system_python3():
    """Verify the exact command configured in mcp_config.json runs under bare python3."""
    messages = "\n".join([
        json.dumps(dict(_initialize("2024-11-05"), id=1)),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
    ]) + "\n"
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(PLUGIN_DIR)
    proc = subprocess.run(
        ["python3", str(SERVER_SCRIPT)],
        cwd=PLUGIN_DIR,
        env=env,
        input=messages,
        text=True,
        capture_output=True,
        check=True,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [m["id"] for m in lines] == [1, 2, 3]
    assert lines[0]["result"]["serverInfo"]["name"] == "opus-et"
    assert lines[1]["result"] == {}
    assert len(lines[2]["result"]["tools"]) == 11


@pytest.mark.parametrize("name, extra", [
    ("opus_open_checkpoint", {"gate": "setup", "next_phase": "1"}),
    ("opus_record_checkpoint", {"gate": "setup", "decision": "approved"}),
    ("opus_exclude_tomostar", {"ts_name": "TS_010"}),
    ("opus_preflight", {}),
])
def test_copied_state_cannot_write_to_another_project(tmp_path, monkeypatch, name, extra):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir()
    b.mkdir()
    rs = srv._mod("run_state")
    rs.init_state(str(a))
    original = rs.state_path(a).read_bytes()
    rs.state_path(b).write_bytes(original)
    monkeypatch.setattr(srv._mod("preflight"), "probe",
                        lambda known: {"environment": {}, "cluster": {}, "missing": []})
    error, data = _call_tool(name, {"work_dir": str(b), **extra})
    assert error and str(a) in data["error"] and str(b) in data["error"]
    assert rs.state_path(a).read_bytes() == rs.state_path(b).read_bytes() == original


@pytest.mark.parametrize("name, args", [
    ("opus_get_run_state", {"create_if_missing": "false"}),
    ("opus_get_run_state", {"create_if_missing": True, "extra": 1}),
    ("opus_open_checkpoint", {"gate": "setup"}),
    ("opus_open_checkpoint", {"gate": "unknown", "next_phase": "1"}),
    ("opus_record_checkpoint", {"gate": "setup", "decision": "approved", "evidence": []}),
    ("opus_preflight", None),
    ("opus_preflight", []),
    ("opus_compute_fsc", {"half1": "a", "half2": "b", "apix": True, "out_prefix": "out"}),
    ("opus_gen_gallery_cxc", {"maps_dir": "m", "out": "o", "columns": 1.5}),
])
def test_invalid_schema_arguments_never_reach_handler(tmp_path, name, args, monkeypatch):
    if name.startswith(("opus_get_", "opus_open_", "opus_record_")):
        args = {"work_dir": str(tmp_path), **args}
    monkeypatch.setitem(srv.HANDLERS, name, lambda _: pytest.fail("handler ran"))
    response = _session().handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                   "params": {"name": name, "arguments": args}})
    assert response["error"]["code"] == -32602
    assert not (tmp_path / ".opus_run_state.json").exists()


def test_release_bundle_runs_state_and_scientific_tools_over_stdio(tmp_path):
    import zipfile

    archive = tmp_path / "opus-et.zip"
    subprocess.run([sys.executable, str(ROOT / "tools/build_antigravity_plugin.py"), str(archive)],
                   cwd=tmp_path, check=True)
    plugin = tmp_path / "installed" / "opus-et"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(plugin)
    for skill in PLUGIN_DIR.joinpath("skills").iterdir():
        assert (plugin / "skills" / skill.name / "SKILL.md").is_file()
    assert (plugin / "rules/AGENTS.md").is_file()

    config = tmp_path / "config.pkl"
    config.write_bytes(pickle.dumps({
        "lattice_args": {"D": np.int32(65)}, "model_args": {"Apix": np.float32(4), "zdim": 8},
        "dataset_args": {"downfrac": 0.5, "particles": "p.star", "poses": "pose.pkl"},
    }))
    calls = [("opus_get_run_state", {"work_dir": str(tmp_path), "create_if_missing": True}),
             ("opus_extract_config", {"config_path": str(config)})]
    wire = json.dumps(_initialize("2025-06-18")) + "\n" + json.dumps(READY) + "\n"
    wire += "".join(json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                               "params": {"name": name, "arguments": args}}) + "\n"
                   for i, (name, args) in enumerate(calls, 1))
    config_entry = json.loads((plugin / "mcp_config.json").read_text())["mcpServers"]["opus-et"]
    entry = config_entry["args"][0].replace("${PLUGIN_ROOT}", str(plugin))
    # -S excludes site packages in the MCP process. The explicitly chosen child interpreter has NumPy.
    proc = subprocess.run([config_entry["command"], "-S", entry], cwd=plugin, input=wire,
                          env={**os.environ, "PLUGIN_ROOT": str(plugin), "OPUS_ET_PYTHON": sys.executable},
                          text=True, capture_output=True, check=True)
    replies = [json.loads(line)["result"] for line in proc.stdout.splitlines()[1:]]
    assert len(replies) == 2 and all(not reply["isError"] for reply in replies), replies
    state, cfg = [json.loads(reply["content"][0]["text"]) for reply in replies]
    assert state["state_path"] == str(tmp_path / ".opus_run_state.json")
    assert cfg["original_Apix"] == 2 and cfg["effective_box_size"] == 64


def _initialize(version, capabilities=None):
    return {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": version, "capabilities": capabilities or {},
                       "clientInfo": {"name": "test", "version": "1"}}}


def _wire_call(i, name, args):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": name, "arguments": args}}


def _wire(messages, *argv):
    proc = subprocess.run([sys.executable, str(SERVER_SCRIPT), *argv],
                          input="".join(json.dumps(m) + "\n" for m in messages),
                          capture_output=True, text=True, check=True)
    return [json.loads(line) for line in proc.stdout.splitlines()]


READY = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_negotiated_batch_and_notifications():
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    notice = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 99}}
    messages = [_initialize("2025-03-26"), READY, [ping, notice, dict(ping, id=2)], [notice], []]
    init, batch, empty_error = _wire(messages)
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert [r["id"] for r in batch] == [1, 2]
    assert empty_error["error"]["code"] == -32600
    assert "error" in _wire([_initialize("2025-06-18"), READY, [ping]])[-1]


def test_desktop_cannot_probe_cluster_or_mutate_its_state(tmp_path):
    import socket
    init, listing, denied = _wire([_initialize("2025-06-18"), READY,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        _wire_call(2, "opus_preflight", {"work_dir": str(tmp_path)})], "--role", "desktop")
    assert socket.gethostname() in init["result"]["instructions"]
    assert "opus_preflight" not in {t["name"] for t in listing["result"]["tools"]}
    assert "error" in denied
    assert not (tmp_path / ".opus_run_state.json").exists()


def test_unconfirmed_stale_and_cancelled_reviews_do_not_write(tmp_path):
    rs = srv._mod("run_state")
    session = _session(elicit=True)
    args = {"gate": "setup", "decision": "approved", "work_dir": str(tmp_path), "next_phase": "1"}
    q = _wire_call(2, "opus_record_checkpoint", args)
    unconfirmed = _session().handle(q)
    assert unconfirmed["result"]["isError"]
    assert not rs.state_path(tmp_path).exists()

    for result in ({"action": "decline"}, {"action": "cancel"}, {"action": "accept", "content": {}}):
        ask = session.handle(q)
        assert ask["method"] == "elicitation/create" and str(tmp_path) in ask["params"]["message"]
        response = session.handle({"jsonrpc": "2.0", "id": ask["id"], "result": result})
        assert response["result"]["isError"]
        assert not rs.state_path(tmp_path).exists()

    def accept(ask):
        return session.handle({"jsonrpc": "2.0", "id": ask["id"],
                               "result": {"action": "accept", "content": {"reviewer": "RB"}}})

    first = session.handle(q)
    second = session.handle(_wire_call(3, "opus_record_checkpoint", args))
    assert not accept(second)["result"]["isError"]
    assert accept(first)["result"]["isError"]
    assert accept(second) is None
    record, = rs.load_state(tmp_path)["checkpoints"]
    assert record["confirmation"]["reviewer"] == "RB"
    assert record["confirmation"]["channel"] == "mcp_elicitation"


def test_two_stdio_processes_preserve_both_series_lists(tmp_path):
    def write_series(label):
        messages = [_initialize("2025-06-18"), READY]
        messages.extend(_wire_call(i + 1, "opus_exclude_tomostar",
            {"work_dir": str(tmp_path), "ts_name": f"{label}{i}"}) for i in range(24))
        return _wire(messages, "--role", "cluster")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        for responses in pool.map(write_series, ("A", "B")):
            assert all(not r["result"]["isError"] for r in responses[1:])
    state = srv._mod("run_state").load_state(tmp_path)
    assert set(state["excluded_tomostar"]) == {f"{k}{i}" for k in "AB" for i in range(24)}


def test_confirmation_form_displays_and_saves_pending_evidence(tmp_path):
    rs = srv._mod("run_state")
    with rs.edit_state(tmp_path) as state:
        rs.record_checkpoint(state, "setup", "pending", "1", voltage_kv=200)
    session = _session(elicit=True)
    form = session.handle(_wire_call(5, "opus_record_checkpoint", {
        "work_dir": str(tmp_path), "gate": "setup", "decision": "approved"}))
    detail = json.loads(form["params"]["message"].split("\n", 1)[1])
    assert detail["next_phase"] == "1" and detail["evidence"] == {"voltage_kv": 200}
    assert not rs.gate_approved(rs.load_state(tmp_path), "setup")
    response = session.handle({"jsonrpc": "2.0", "id": form["id"],
                               "result": {"action": "accept", "content": {"reviewer": "RB"}}})
    assert response["id"] == 5 and not response["result"]["isError"]
    saved = rs.load_state(tmp_path)
    assert saved["phases"]["1"]["status"] == "ready"
    assert saved["checkpoints"][-1]["voltage_kv"] == 200


def test_wrong_host_is_rejected_before_any_state_change(tmp_path):
    replies = _wire([_initialize("2025-06-18"), READY, _wire_call(2, "opus_exclude_tomostar", {
        "work_dir": str(tmp_path), "expected_host": "wrong-" + srv.HOST, "ts_name": "A"})])
    assert replies[-1]["result"]["isError"]
    assert not (tmp_path / ".opus_run_state.json").exists()
