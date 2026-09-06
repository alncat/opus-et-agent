import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import config_edit
import poller as poller_mod
import sources
import status_server


class StubEditor:
    species_conf_path = "/run/species.conf"
    pipeline_conf_path = "/run/pipeline.conf"

    def dataset_values(self):
        return {"ANGPIX": "3.37", "EXPOSURE": "2.36", "BINNING_FACTOR": "4"}

    def available_species(self):
        return ["species.conf", "species_fas.conf"]

    def recommendations(self, species=None):
        return {"SUBTOMO_BOX_SIZE": "~102 (up to ~152 for high-res)"}

    def create_species(self, name, values=None, template=None):
        if self.fail:
            raise config_edit.ValidationError(self.fail)
        self.created.append((name, values, template))
        return f"species_{name}.conf"

    def __init__(self, fail=None):
        self.applied = []
        self.created = []
        self.fail = fail

    def current_values(self, species=None):
        if species == "species_fas.conf":
            return {"ZDIM": "24", "BINNING_FACTOR": "4"}
        return {"ZDIM": "8", "OUTPUT_ANGPIX": "4.2", "BINNING_FACTOR": "4"}

    def apply(self, key, raw, species=None):
        if self.fail:
            raise config_edit.ValidationError(self.fail)
        self.applied.append((key, raw))
        return "/run/species.conf.bak.20260904T101500"


@pytest.fixture
def server():
    p = poller_mod.Poller({"squeue": lambda: [], "run_state": lambda: {"phases": {}}},
                          {"squeue": 10.0, "run_state": 10.0})
    p.refresh_due()
    editor = StubEditor()
    handler = status_server.make_handler(p, editor, csrf_token="tok123")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    yield f"http://127.0.0.1:{port}", editor
    httpd.shutdown()


@pytest.fixture
def server_with_logs():
    p = poller_mod.Poller({"squeue": lambda: []}, {"squeue": 10.0})
    p.refresh_due()

    def log_fetch(job, stream, lines):
        return "Traceback (most recent call last):\n  RuntimeError: boom\n"

    handler = status_server.make_handler(p, StubEditor(), "tok123", log_fetch)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    return urllib.request.urlopen(req)


def test_status_endpoint_returns_snapshot_json(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/api/status") as r:
        body = json.loads(r.read())
    assert "sources" in body
    assert body["sources"]["squeue"]["error"] is None


def test_dashboard_html_served(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/") as r:
        assert r.status == 200
        assert b"<html" in r.read().lower()


def test_page_injects_the_run_label():
    """Several instances on different ports is the documented pattern, so the
    page must say which host and run directory it is watching."""
    p = poller_mod.Poller({"squeue": lambda: []}, {"squeue": 10.0})
    p.refresh_due()
    handler = status_server.make_handler(p, StubEditor(), csrf_token="tok123",
                                         run_label="super · /work/agent")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/") as r:
            page = r.read().decode()
        assert "super · /work/agent" in page
        assert "__RUNLABEL__" not in page
    finally:
        httpd.shutdown()


def test_config_get_exposes_allowlist(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/api/config") as r:
        body = json.loads(r.read())
    assert "ZDIM" in body["keys"]
    # Path keys must never be offered for editing.
    assert "WARP_DIR" not in body["keys"]


def test_post_without_csrf_token_is_rejected(server):
    base, editor = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{base}/api/config", {"key": "ZDIM", "value": "16"},
              {"Content-Type": "application/json"})
    assert exc.value.code == 403
    assert editor.applied == []


def test_post_with_wrong_csrf_token_is_rejected(server):
    base, editor = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{base}/api/config", {"key": "ZDIM", "value": "16"},
              {"Content-Type": "application/json", "X-CSRF-Token": "wrong"})
    assert exc.value.code == 403
    assert editor.applied == []


def test_post_with_foreign_origin_is_rejected(server):
    base, editor = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{base}/api/config", {"key": "ZDIM", "value": "16"},
              {"Content-Type": "application/json", "X-CSRF-Token": "tok123",
               "Origin": "http://evil.example"})
    assert exc.value.code == 403
    assert editor.applied == []


def test_post_with_valid_token_applies_edit(server):
    base, editor = server
    resp = _post(f"{base}/api/config", {"key": "ZDIM", "value": "16"},
                 {"Content-Type": "application/json", "X-CSRF-Token": "tok123"})
    assert resp.status == 200
    assert editor.applied == [("ZDIM", "16")]


def test_validation_error_returns_400_with_reason(server):
    p = poller_mod.Poller({}, {})
    handler = status_server.make_handler(p, StubEditor(fail="ZDIM must be <= 64"),
                                         csrf_token="tok123")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(f"{base}/api/config", {"key": "ZDIM", "value": "999"},
                  {"Content-Type": "application/json", "X-CSRF-Token": "tok123"})
        assert exc.value.code == 400
        assert "must be <= 64" in exc.value.read().decode()
    finally:
        httpd.shutdown()


def test_edit_is_blocked_while_jobs_are_running_unless_confirmed():
    """Spec: editing a conf mid-phase can corrupt a running job, so an edit
    while anything is PENDING/RUNNING requires an explicit second confirm.
    Enforced server-side, not just in the UI."""
    p = poller_mod.Poller(
        {"squeue": lambda: [sources.Job("1", "train", "RUNNING", "1:00", "normal", "n1")]},
        {"squeue": 10.0},
    )
    p.refresh_due()
    editor = StubEditor()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                status_server.make_handler(p, editor, csrf_token="tok123"))
    Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    hdrs = {"Content-Type": "application/json", "X-CSRF-Token": "tok123"}
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(f"{base}/api/config", {"key": "ZDIM", "value": "16"}, hdrs)
        assert exc.value.code == 409
        assert editor.applied == []

        resp = _post(f"{base}/api/config",
                     {"key": "ZDIM", "value": "16", "confirm_running": True}, hdrs)
        assert resp.status == 200
        assert editor.applied == [("ZDIM", "16")]
    finally:
        httpd.shutdown()


def test_default_bind_is_loopback():
    """Must never default to 0.0.0.0 -- this server has no authentication."""
    args = status_server.parse_args(["--host", "h", "--work-dir", "/run"])
    assert args.bind == "127.0.0.1"


def test_build_phases_merges_run_state_status_with_validate_completion():
    run_state = {"phases": {"3": {"status": "done"}, "5": {"status": "running"},
                            "10": {"status": "pending"}}}
    completion = {"5": {"completion": "partial", "done": 7, "total": 10}}
    got = status_server.build_phases(run_state, completion)
    by_phase = {p["phase"]: p for p in got}
    assert by_phase["3"]["status"] == "done"
    assert by_phase["5"]["done"] == 7 and by_phase["5"]["total"] == 10
    # natural order: 10 must sort after 5, not between 1 and 2
    assert [p["phase"] for p in got] == ["3", "5", "10"]


def test_build_phases_sorts_lettered_subphases_naturally():
    run_state = {"phases": {"3a": {"status": "done"}, "3": {"status": "done"},
                            "8b": {"status": "running"}, "8a": {"status": "done"}}}
    got = [p["phase"] for p in status_server.build_phases(run_state, {})]
    assert got == ["3", "3a", "8a", "8b"]


def test_build_phases_sorts_dotted_followups_after_lettered_subphases():
    """Phase 3.5 rewrites what 3a/3b produced, so manifest order is
    3, 3a, 3b, 3.5 -- not ASCII order, which would put 3.5 first."""
    run_state = {"phases": {k: {"status": "done"}
                            for k in ("3", "3.5", "3a", "3b")}}
    got = [p["phase"] for p in status_server.build_phases(run_state, {})]
    assert got == ["3", "3a", "3b", "3.5"]


def test_build_phases_carry_human_readable_descriptions():
    """A bare phase number forces the reader to memorize the pipeline; each
    row must name its stage. Names mirror manifest.yml; an unknown phase
    (validate.sh fallbacks like 8d, Ma) degrades to an empty string."""
    got = status_server.build_phases(
        {"phases": {"5": {"status": "running"}, "3.5": {"status": "done"},
                    "8d": {"status": "pending"}}}, {})
    by_phase = {p["phase"]: p for p in got}
    assert by_phase["5"]["desc"] == "CTF estimation + tomogram reconstruction"
    assert by_phase["3.5"]["desc"].startswith("Update TOMO_DIMS")
    assert by_phase["8d"]["desc"] == ""


def test_build_gates_marks_a_gate_without_a_checkpoint_as_unapproved():
    """A gate is approved exactly when a checkpoints[] entry records its
    decision, so an absent entry means it is still waiting on the human."""
    run_state = {"checkpoints": [{"gate": "alignment_qc", "decision": "approved"}]}
    gates = {g["gate"]: g for g in status_server.build_gates(run_state)}
    assert gates["alignment_qc"]["approved"] is True
    assert gates["picks_qc"]["approved"] is False
    assert gates["state_selection"]["approved"] is False


def test_status_surfaces_a_phase_parked_at_a_checkpoint():
    p = poller_mod.Poller(
        {"run_state": lambda: {"phases": {"6": {"status": "checkpoint"}},
                               "checkpoints": []}},
        {"run_state": 10.0})
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    assert body["waiting"] == ["6"]



def test_stale_banner_lists_every_failing_source():
    """When several sources fail at once (cluster reboot), the banner must
    name each one, not only the first failure."""
    def boom():
        raise RuntimeError("ssh refused")
    p = poller_mod.Poller({"squeue": boom, "run_state": boom},
                          {"squeue": 10.0, "run_state": 10.0})
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    assert "squeue" in body["stale"] and "run_state" in body["stale"]


def test_build_status_accounting_rolls_up_gpu_hours():
    """sacct history carries allocated GPUs; gpu-hours = gpus * elapsed,
    summed over every job the accounting daemon still remembers."""
    p = poller_mod.Poller({
        "squeue": lambda: [],
        "run_jobs": lambda: {"logs": [], "sacct": [
            sources.Job("1", "a", "COMPLETED", "2:00:00", "main", "n1", gpu=2),
            sources.Job("2", "b", "FAILED", "1:00:00", "main", "n1", gpu=4),
            sources.Job("3", "c", "RUNNING", "30:00", "main", "n2", gpu=1),
            sources.Job("4", "d", "RUNNING", "10:00", "main", "n2"),
        ]}}, {"squeue": 10.0, "run_jobs": 10.0})
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    acc = body["accounting"]
    assert acc["gpu_hours"] == 8.5    # 2*2h + 4*1h + 1*0.5h; no gpu on job 4
    assert acc["n_done"] == 1 and acc["n_failed"] == 1 and acc["n_active"] == 2


def test_build_status_tolerates_missing_optional_sources():
    """Older fixtures construct pollers without inventory/training/runs; the
    payload must still build with empty defaults."""
    p = poller_mod.Poller({"squeue": lambda: []}, {"squeue": 10.0})
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    assert body["inventory"] == [] and body["training"] == [] and body["runs"] == []
    assert body["accounting"]["gpu_hours"] == 0


def test_page_has_inventory_training_and_runs_tabs():
    page = status_server.PAGE
    for t in ("inventory", "training", "runs"):
        assert f'data-t="{t}"' in page
        assert f'id="tab-{t}"' in page
    assert 'id="inv"' in page and 'id="training"' in page and 'id="runslist"' in page


def test_status_merges_sacct_history_into_the_jobs_table():
    """squeue forgets a job the moment it finishes, so a failed or completed
    job must arrive via sacct or it silently vanishes from the table."""
    p = poller_mod.Poller({
        "squeue": lambda: [sources.Job("1043", "align", "RUNNING", "1:00", "main", "n5")],
        "run_jobs": lambda: {"logs": [], "sacct": [
            sources.Job("1041", "import", "COMPLETED", "2:00", "main", "n3"),
            sources.Job("1042", "recon", "FAILED", "3:00", "main", "n4"),
            # both lists name 1043; the live squeue state must win
            sources.Job("1043", "align", "COMPLETED", "9:99", "main", "n3")]},
    }, {"squeue": 10.0, "run_jobs": 10.0})
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    by = {j["job_id"]: j for j in body["jobs"]}
    assert by["1041"]["state"] == "COMPLETED"
    assert by["1042"]["state"] == "FAILED"
    assert by["1043"]["state"] == "RUNNING"


def _jobs_poller(live, logs=(), hist=()):
    return poller_mod.Poller(
        {"squeue": lambda: list(live),
         "run_jobs": lambda: {"logs": list(logs), "sacct": list(hist)}},
        {"squeue": 10.0, "run_jobs": 10.0})


def test_jobs_are_scoped_to_the_run_directory():
    """squeue is account-wide. On the real cluster every one of the 14 running
    jobs belonged to a different directory, so the panel described work that
    had nothing to do with this run -- including its GPU-hours total."""
    p = _jobs_poller([
        sources.Job("1", "recon", "RUNNING", "1:00:00", "main", "n1",
                    gpu=2, workdir="/run/agent"),
        sources.Job("2", "sub", "RUNNING", "1:00:00", "main", "n2",
                    gpu=1, workdir="/run/agent/opuset/ribo"),
        sources.Job("3", "someone_else", "RUNNING", "5:00:00", "main", "n3",
                    gpu=8, workdir="/elsewhere/project"),
    ])
    p.refresh_due()
    body = status_server.build_status(p.snapshot(), work_dir="/run/agent")
    assert [j["job_id"] for j in body["jobs"]] == ["1", "2"]
    assert [j["job_id"] for j in body["other_jobs"]] == ["3"]
    # The unrelated job's 8 GPUs must not land in this run's accounting.
    assert body["accounting"]["gpu_hours"] == 3.0
    assert body["accounting"]["n_active"] == 2


def test_a_sibling_directory_is_not_inside_the_run():
    """/run/agent2 starts with /run/agent as a string but is a different run."""
    p = _jobs_poller([sources.Job("9", "x", "RUNNING", "1:00", "main", "n1",
                                  workdir="/run/agent2")])
    p.refresh_due()
    body = status_server.build_status(p.snapshot(), work_dir="/run/agent")
    assert body["jobs"] == [] and [j["job_id"] for j in body["other_jobs"]] == ["9"]


def test_a_job_with_no_workdir_is_not_claimed_by_the_run():
    """An older squeue without %Z reports nothing; claiming those jobs would
    put the whole account back in the table."""
    p = _jobs_poller([sources.Job("9", "x", "RUNNING", "1:00", "main", "n1")])
    p.refresh_due()
    body = status_server.build_status(p.snapshot(), work_dir="/run/agent")
    assert body["jobs"] == []


def test_without_a_work_dir_nothing_is_filtered():
    p = _jobs_poller([sources.Job("9", "x", "RUNNING", "1:00", "main", "n1",
                                  workdir="/elsewhere")])
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    assert [j["job_id"] for j in body["jobs"]] == ["9"]
    assert body["other_jobs"] == []


def test_jobs_known_only_from_their_logs_still_appear():
    """sacct returns zero rows on this cluster, so without the logs/ listing
    this run's 58 finished jobs would be invisible and unreachable."""
    p = _jobs_poller(
        [],
        logs=[{"job_id": "160251", "name": "warp_m_refine", "streams": ["err", "out"]},
              {"job_id": "160280", "name": "warp_m_refine", "streams": ["out"]}],
        hist=[sources.Job("160251", "warp_m_refine", "COMPLETED", "1:00", "m", "n1")])
    p.refresh_due()
    jobs = {j["job_id"]: j for j in
            status_server.build_status(p.snapshot(), work_dir="/run/agent")["jobs"]}
    # sacct knew this one, so its real state wins over the log-derived entry.
    assert jobs["160251"]["state"] == "COMPLETED"
    assert not jobs["160251"].get("from_log")
    # This one exists only as a log file: no invented outcome.
    assert jobs["160280"]["state"] is None
    assert jobs["160280"]["name"] == "warp_m_refine"
    assert jobs["160280"]["from_log"] is True


def test_page_offers_the_jobs_run_elsewhere():
    page = status_server.PAGE
    assert 'id="showother"' in page and 'id="otherlab"' in page
    js = page.split("<script>", 1)[1]
    assert "LASTOTHER" in js
    assert "no record" in js          # log-only jobs state no outcome


def test_config_get_includes_current_values_and_resolved_filename(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/api/config") as r:
        body = json.loads(r.read())
    assert body["values"]["ZDIM"] == "8"
    assert body["file"].endswith("species.conf")


def test_logs_endpoint_returns_the_tail(server_with_logs):
    base = server_with_logs
    with urllib.request.urlopen(f"{base}/api/logs?job=159961&stream=err&lines=5") as r:
        body = json.loads(r.read())
    assert "Traceback" in body["text"]


def test_page_preserves_javascript_escapes():
    """A raw PAGE keeps \\n as the two characters JS needs."""
    assert "\\n" in status_server.PAGE


def test_config_get_includes_dataset_block_and_both_filenames(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/api/config") as r:
        body = json.loads(r.read())
    assert body["dataset"]["ANGPIX"] == "3.37"
    assert body["files"]["species"].endswith("species.conf")
    assert body["files"]["pipeline"].endswith("pipeline.conf")
    assert body["keys"]["BINNING_FACTOR"]["file"] == "pipeline"
    assert body["keys"]["BINNING_FACTOR"]["warning"]
    assert body["keys"]["ZDIM"]["file"] == "species"


def test_config_lists_available_species_and_switches_on_request(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/api/config") as r:
        body = json.loads(r.read())
    assert body["species_available"] == ["species.conf", "species_fas.conf"]
    assert body["values"]["ZDIM"] == "8"

    with urllib.request.urlopen(f"{base}/api/config?species=species_fas.conf") as r:
        body = json.loads(r.read())
    assert body["species_current"] == "species_fas.conf"
    assert body["values"]["ZDIM"] == "24"


def test_create_species_endpoint_requires_a_csrf_token(server):
    base, editor = server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{base}/api/species", {"name": "26s"}, {"Content-Type": "application/json"})
    assert exc.value.code == 403
    assert editor.created == []


def test_create_species_endpoint_creates_with_a_valid_token(server):
    base, editor = server
    resp = _post(f"{base}/api/species", {"name": "26s", "template": "species.conf"},
                 {"Content-Type": "application/json", "X-CSRF-Token": "tok123"})
    assert json.loads(resp.read())["created"] == "species_26s.conf"
    assert editor.created[0][0] == "26s"


def test_creating_a_species_is_allowed_while_jobs_run():
    """Unlike an edit, a brand-new conf is sourced by nothing, so the
    in-flight 409 guard must NOT block it."""
    p = poller_mod.Poller(
        {"squeue": lambda: [sources.Job("1", "train", "RUNNING", "1:00", "normal", "n1")]},
        {"squeue": 10.0})
    p.refresh_due()
    editor = StubEditor()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                status_server.make_handler(p, editor, csrf_token="tok123"))
    Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        resp = _post(f"{base}/api/species", {"name": "26s"},
                     {"Content-Type": "application/json", "X-CSRF-Token": "tok123"})
        assert resp.status == 200
    finally:
        httpd.shutdown()


def test_page_separates_species_and_pipeline_config_sections():
    """pipeline.conf is shared by every species while the species conf is not,
    so mixing them in one table invites editing a dataset-wide value while a
    single species is selected."""
    page = status_server.PAGE
    assert 'id="cfgspecies"' in page
    assert 'id="cfgpipeline"' in page
    assert "Shared by every species" in page


def test_processing_config_is_rendered_before_species_config():
    """pipeline.conf drives the earlier phases (CTF, motion, alignment,
    reconstruction) and the species conf the later ones, so the page should
    read top-to-bottom in pipeline order."""
    page = status_server.PAGE
    assert page.index('id="cfgpipeline"') < page.index('id="cfgspecies"')
    # the species selector must stay with the species section it controls
    assert page.index('id="cfgpipeline"') < page.index('id="spec"')


def test_build_status_marks_tilt_series_excluded_at_gate_1():
    """Gate 1 exclusions live in run_state['excluded_tomostar']; the tilt
    series table should show which series were dropped."""
    p = poller_mod.Poller({
        "tilt_series": lambda: [
            sources.TiltSeries("TS_026", 44, -42.0, 44.0, 101.5),
            sources.TiltSeries("TS_041", 36, -26.0, 44.0, 82.6)],
        "run_state": lambda: {"excluded_tomostar": ["TS_041"]},
    }, {"tilt_series": 10.0, "run_state": 10.0})
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    by = {t["name"]: t for t in body["tilt_series"]}
    assert by["TS_026"]["excluded"] is False
    assert by["TS_041"]["excluded"] is True
    assert by["TS_026"]["min_tilt"] == -42.0 and by["TS_026"]["n_tilts"] == 44


def test_page_has_three_tabs():
    page = status_server.PAGE
    for t in ("tab-run", "tab-dataset", "tab-config"):
        assert f'id="{t}"' in page


def test_tilt_series_are_listed_once_under_frames():
    """The Dataset tab used to repeat the name, tilt count and tilt range that
    the Frames per-series table already shows. Dataset keeps the acquisition
    facts; the per-series numbers live in one table."""
    page = status_server.PAGE
    assert 'id="ts"' not in page
    js = page.split("<script>", 1)[1]
    assert "function mergeSeries(" in js
    assert "mergeSeries(st.tilt_series,series)" in js


def test_tab_order_puts_frames_beside_run():
    import re
    buttons = re.findall(r'data-t="(\w+)"', status_server.PAGE)
    assert buttons == ["run", "frames", "dataset", "qc", "training",
                       "inventory", "refine", "config", "runs"]


def test_every_onclick_handler_has_a_definition():
    """Regression: a splice deleted pickSpecies and createSpecies while leaving
    their onclick attributes in place. That is valid JavaScript -- the buttons
    simply threw ReferenceError on click -- so the quote-parity syntax check
    could not see it. Species switching and 'Create species conf' were both
    dead in the browser while every test passed."""
    import re
    page = status_server.PAGE
    js = page.split("<script>", 1)[1].split("</script>", 1)[0]
    called = set(re.findall(r'onclick="(\w+)\(', page))
    defined = set(re.findall(r"function\s+(\w+)\s*\(", js))
    assert called, "expected the page to wire up onclick handlers"
    assert not (called - defined), f"onclick with no definition: {sorted(called - defined)}"


def test_functions_referenced_by_the_tab_bar_exist():
    js = status_server.PAGE.split("<script>", 1)[1]
    for fn in ("showTab", "loadCfg", "tick"):
        assert f"function {fn}(" in js or f"async function {fn}(" in js


def test_config_api_exposes_stage_and_stage_order(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/api/config") as r:
        body = json.loads(r.read())
    assert body["keys"]["DIAMETER"]["stage"] == "Template matching"
    assert body["keys"]["ZDIM"]["stage"] == "Training"
    # stages must be listed in the order the pipeline runs them
    order = body["stage_order"]
    assert order.index("Template matching") < order.index("Extraction")
    assert order.index("Extraction") < order.index("Export")
    assert order.index("Export") < order.index("Training")


@pytest.fixture
def qc_server():
    p = poller_mod.Poller(
        {"qc_images": lambda: ["qc/TS_026_xy.png", "gate2_qc/fas_TS028_slab142_all.png"]},
        {"qc_images": 10.0})
    p.refresh_due()
    fetched = []

    def image_fetch(rel):
        fetched.append(rel)
        return b"\x89PNG\r\n\x1a\nfake"

    handler = status_server.make_handler(p, StubEditor(), "tok123", None, image_fetch)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", fetched
    httpd.shutdown()


def test_qc_endpoint_lists_available_images(qc_server):
    base, _ = qc_server
    with urllib.request.urlopen(f"{base}/api/qc") as r:
        body = json.loads(r.read())
    assert "qc/TS_026_xy.png" in body["images"]


def test_qc_image_is_served_as_png(qc_server):
    base, fetched = qc_server
    with urllib.request.urlopen(f"{base}/api/qc/image?path=qc/TS_026_xy.png") as r:
        assert r.headers["Content-Type"] == "image/png"
        assert r.read().startswith(b"\x89PNG")
    assert fetched == ["qc/TS_026_xy.png"]


@pytest.mark.parametrize("evil", [
    "../../etc/passwd", "/etc/passwd", "qc/../../secret.png", "qc/not_listed.png",
])
def test_qc_image_refuses_anything_not_in_the_discovered_listing(qc_server, evil):
    """The listing is the allowlist; a client path is matched against it and
    never used to build a filesystem path."""
    base, fetched = qc_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/api/qc/image?path={urllib.parse.quote(evil)}")
    assert exc.value.code == 403
    assert fetched == []


@pytest.fixture
def render_server():
    p = poller_mod.Poller(
        {"tilt_series": lambda: [sources.TiltSeries("TS_026", 44, -42.0, 44.0, 101.5)],
         "qc_images": lambda: []},
        {"tilt_series": 10.0, "qc_images": 10.0})
    p.refresh_due()
    calls = []

    def render_qc(kind, tomo, species=None, opts=None):
        calls.append((kind, tomo, species, opts))
        return [f"qc_ondemand/{tomo}_xy.png"]

    handler = status_server.make_handler(p, StubEditor(), "tok123",
                                         None, None, render_qc)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", calls
    httpd.shutdown()


def test_render_requires_a_csrf_token(render_server):
    base, calls = render_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{base}/api/qc/render", {"kind": "slices", "tomo": "TS_026"},
              {"Content-Type": "application/json"})
    assert exc.value.code == 403
    assert calls == []


def test_render_runs_for_a_known_tilt_series(render_server):
    base, calls = render_server
    resp = _post(f"{base}/api/qc/render", {"kind": "slices", "tomo": "TS_026"},
                 {"Content-Type": "application/json", "X-CSRF-Token": "tok123"})
    assert json.loads(resp.read())["rendered"] == ["qc_ondemand/TS_026_xy.png"]
    assert calls[0][:3] == ("slices", "TS_026", None)


@pytest.mark.parametrize("evil", [
    "TS_999", "../../etc/passwd", "TS_026; rm -rf /", "$(id)", "",
])
def test_render_refuses_a_tilt_series_not_in_the_discovered_list(render_server, evil):
    base, calls = render_server
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{base}/api/qc/render", {"kind": "slices", "tomo": evil},
              {"Content-Type": "application/json", "X-CSRF-Token": "tok123"})
    assert exc.value.code == 400
    assert calls == []


def test_opening_a_tab_reloads_its_data():
    """Poller-backed sources need an ssh round trip; a tab opened before the
    first fetch completes would otherwise stay empty for the whole session."""
    js = status_server.PAGE.split("<script>", 1)[1]
    body = js[js.index("function showTab("):js.index("let QC_READY")]
    assert "loadQc()" in body and "loadCfg()" in body


def test_page_distinguishes_loading_from_empty():
    """'no QC images found' is wrong while the first fetch is still in flight."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "function srcState(" in js
    assert "loading from the cluster" in js


def test_qc_endpoint_returns_structured_entries(qc_server):
    base, _ = qc_server
    with urllib.request.urlopen(f"{base}/api/qc") as r:
        body = json.loads(r.read())
    by = {e["path"]: e for e in body["entries"]}
    assert by["qc/TS_026_xy.png"]["label"] == "XY slice"
    assert by["gate2_qc/fas_TS028_slab142_all.png"]["kind"] == "overlay"


def test_qc_list_is_grouped_and_filterable():
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "function renderQcList(" in js
    assert "QC_FILTER" in js
    assert 'id="fTomo"' in js and 'id="fSpecies"' in js


def test_qc_filters_on_two_axes_not_three():
    """`kind` and `dir` say almost the same thing -- qc holds the slices,
    gate2_qc holds the overlays -- so filtering on both narrowed nothing while
    costing two dropdowns. The sections restate `dir` already; species is the
    axis that actually splits the 132 overlays."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert 'id="fKind"' not in js and 'id="fSrc"' not in js
    assert "QC_FILTER.species" in js


def test_qc_defaults_to_every_tilt_series():
    """Defaulting to one arbitrary series hid the comparison the page exists
    to make."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "QC_FILTER={tomo:'all',species:'all'}" in js


def test_responses_are_not_cacheable(server):
    """A cached page survives a server restart with new code, and a
    re-rendered QC image reuses its path."""
    base, _ = server
    for path in ("/", "/api/status", "/api/config"):
        with urllib.request.urlopen(f"{base}{path}") as r:
            assert "no-store" in (r.headers.get("Cache-Control") or ""), path


def test_no_javascript_function_is_defined_twice():
    """Regression: an edit spliced with a start marker that appeared AFTER its
    end marker, so it duplicated renderQc/loadQc instead of replacing them.
    The later, stale definition silently won and the page rendered old markup
    while every test passed -- duplicate definitions are valid JavaScript."""
    import collections
    import re
    js = status_server.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
    names = re.findall(r"(?:async\s+)?function\s+(\w+)\s*\(", js)
    dupes = sorted(n for n, c in collections.Counter(names).items() if c > 1)
    assert not dupes, f"duplicate JS function definitions: {dupes}"


def test_render_options_are_validated_and_passed_through():
    """Only known numeric options within range may reach the command line."""
    p = poller_mod.Poller(
        {"tilt_series": lambda: [sources.TiltSeries("TS_026", 44, -42.0, 44.0, 101.5)]},
        {"tilt_series": 10.0})
    p.refresh_due()
    got = []

    def render_qc(kind, tomo, species=None, opts=None):
        got.append(opts)
        return []

    def render_opts(payload):
        out = {}
        for k, lo, hi in (("n_slabs", 1, 50), ("top_n", 1, 100000)):
            if payload.get(k) not in (None, ""):
                v = int(payload[k])
                if not (lo <= v <= hi):
                    raise config_edit.ValidationError(f"{k} out of range")
                out[k] = v
        return out

    handler = status_server.make_handler(p, StubEditor(), "tok123", None, None,
                                         render_qc, render_opts)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    hdrs = {"Content-Type": "application/json", "X-CSRF-Token": "tok123"}
    try:
        _post(f"{base}/api/qc/render",
              {"kind": "overlay", "tomo": "TS_026", "n_slabs": "12"}, hdrs)
        assert got == [{"n_slabs": 12}]

        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(f"{base}/api/qc/render",
                  {"kind": "overlay", "tomo": "TS_026", "n_slabs": "999"}, hdrs)
        assert exc.value.code == 400
    finally:
        httpd.shutdown()


def test_overlay_render_form_has_its_own_species_selector():
    """The overlay used to silently take whatever species the Config tab had
    selected, which is neither visible nor discoverable from Visual QC."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert 'id="rSpecies"' in js
    assert "SPECIES_LIST" in js
    # and the chosen value must be what gets sent
    assert "getElementById('rSpecies')" in js.split("async function renderQc(")[1]


def test_qc_list_separates_sources():
    """Pipeline output and on-demand renders are different provenance and were
    confusing shown side by side in one row."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "bySrc" in js              # one section per pipeline step
    assert "ONDEMAND" in js           # renders made here stay under the controls


def test_slice_render_offers_a_depth_choice():
    js = status_server.PAGE.split("<script>", 1)[1]
    assert 'id="rZ"' in js and 'id="rY"' in js


def test_page_javascript_actually_parses():
    """The strongest guard available: hand the page's JS to a real parser.

    The hand-rolled checks each caught one bug class and missed others -- a
    stray ternary arm (`a ? b : c : ''`) sailed past all of them and killed
    the whole script at load. This replaced the quote-parity heuristic, which
    could not model multi-line template literals and rejected valid SVG markup
    whose attributes span lines. Skipped where node is unavailable, so it
    never blocks the suite.
    """
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    js = status_server.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        path = fh.name
    proc = subprocess.run([node, "--check", path], capture_output=True, text=True)
    assert proc.returncode == 0, f"page JS does not parse:\n{proc.stderr}"


def test_image_opens_in_an_overlay_not_at_a_fixed_page_position():
    """A viewer pinned to one spot meant the image appeared far from whichever
    thumbnail was clicked, and scrolling to it moved you away from the grid.
    An overlay is always in view wherever you clicked."""
    page = status_server.PAGE
    assert 'id="qclight"' in page
    assert "position:fixed" in page
    assert "function closeQc(" in page
    assert "Escape" in page                 # dismissable from the keyboard
    assert 'id="qcview"' not in page        # the fixed viewer is gone


def test_qc_is_two_levels_deep_section_then_tilt_series():
    """Section (the pipeline step) then one row per tilt series. The old
    source-heading-inside-kind-heading nesting restated itself, and no level
    let you compare the same view across series."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "function seriesRows(" in js and "function qcCard(" in js
    assert "function thumbGrid(" not in js                  # old renderer gone
    assert "chipsByKind" not in js and "chipRows" not in js
    assert "function pickQcTomo(" in js   # a row label narrows to its series


def test_every_qc_section_says_what_it_answers():
    """A directory name is provenance, not a reason to look at the images."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "QC_SRC_HINT" in js
    for d in ("qc", "gate1_qc", "gate2_qc", "gate3_qc", "gate4_qc"):
        assert f"{d}:" in js


def test_config_api_returns_derived_recommendations(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/api/config") as r:
        body = json.loads(r.read())
    assert "~102" in body["recommended"]["SUBTOMO_BOX_SIZE"]


def test_form_shows_the_recommendation_above_the_raw_range():
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "c.recommended" in js
    assert "range ${esc(allowed(sp))}" in js


def test_thumbnail_loader_only_writes_off_the_batch_it_requested():
    """It requests want.slice(0,60) but used to mark ALL of `want` attempted,
    so with 192 images 115 became permanent grey placeholders that never
    retried. Only the requested batch may be marked."""
    js = status_server.PAGE.split("<script>", 1)[1]
    body = js[js.index("async function loadThumbs("):js.index("function renderQcList(")]
    assert "const batch=want.slice(0,60)" in body
    assert "batch.forEach" in body
    assert "want.forEach" not in body


def _attention_of(fetchers, ttl=10.0):
    p = poller_mod.Poller(fetchers, {k: ttl for k in fetchers})
    p.refresh_due()
    return status_server.build_status(p.snapshot())["attention"]


def test_attention_flags_a_failed_job():
    items = _attention_of({
        "squeue": lambda: [sources.Job("9", "recon", "FAILED", "0:10", "normal", "n1")]})
    assert any(a["level"] == "error" and "9" in a["text"] for a in items)


def test_attention_names_the_parked_phase_and_how_to_clear_a_gate():
    """The separate waiting banner is gone -- the attention strip is the one
    place that reports a parked checkpoint, so it must carry the human-readable
    phase name, and a blocking gate must say what clears it."""
    items = _attention_of({
        "run_state": lambda: {"phases": {"5a": {"status": "checkpoint"}},
                              "checkpoints": []}})
    joined = "; ".join(a["text"] for a in items)
    assert any(a["level"] == "action" and
               "5a (Tilt-series CTF estimation) parked at a checkpoint" in a["text"]
               for a in items)
    assert "record the checkpoint" in joined
    # the duplicate banner is gone (the Runs tab's column header of the same
    # name is a different thing and stays)
    assert 'id="waiting"' not in status_server.PAGE


def test_attention_flags_a_full_filesystem():
    items = _attention_of({"disk": lambda: {"pct_used": 95, "avail_gb": 20.0,
                                            "total_gb": 100.0, "used_gb": 80.0}})
    assert any(a["level"] == "error" and "95%" in a["text"] for a in items)
    warn = _attention_of({"disk": lambda: {"pct_used": 85, "avail_gb": 200.0,
                                           "total_gb": 100.0, "used_gb": 80.0}})
    assert any(a["level"] == "warn" for a in warn)


def test_attention_flags_a_phase_running_with_nothing_queued():
    """The job died without updating the run state -- otherwise invisible."""
    items = _attention_of({
        "squeue": lambda: [],
        "run_state": lambda: {"phases": {"6": {"status": "running", "jobs": [123]}}}})
    assert any("no job" in a["text"] for a in items)


def test_attention_is_quiet_when_nothing_is_wrong():
    items = _attention_of({
        "squeue": lambda: [sources.Job("1", "t", "RUNNING", "1:00", "normal", "n1")],
        "disk": lambda: {"pct_used": 40, "avail_gb": 900.0,
                         "total_gb": 1000.0, "used_gb": 100.0},
        "run_state": lambda: {"phases": {"5": {"status": "done"}}}})
    assert items == []


def test_status_exposes_the_particle_funnel():
    p = poller_mod.Poller(
        {"funnel": lambda: {"ribo": {"picks": 58000, "exported": 58000,
                                     "selected": [{"name": "z8_expanded/sel_ribo.star",
                                                   "count": 14797}]}}},
        {"funnel": 10.0})
    p.refresh_due()
    f = status_server.build_status(p.snapshot())["funnel"]
    assert f["ribo"]["picks"] == 58000
    assert f["ribo"]["selected"][0]["count"] == 14797


def test_funnel_is_an_empty_object_when_unavailable():
    p = poller_mod.Poller({"squeue": lambda: []}, {"squeue": 10.0})
    p.refresh_due()
    assert status_server.build_status(p.snapshot())["funnel"] == {}


def test_non_finite_floats_never_reach_the_browser():
    """json.dumps emits bare Infinity, which JSON.parse rejects -- one such
    value stopped the WHOLE dashboard updating, not just its panel."""
    import json as _json
    out = status_server.jdump({"a": float("inf"), "b": float("nan"),
                               "c": [1.5, float("-inf")], "d": {"e": float("nan")}})
    assert "Infinity" not in out and "NaN" not in out
    back = _json.loads(out)          # must be parseable as strict JSON
    assert back["a"] is None and back["b"] is None
    assert back["c"] == [1.5, None] and back["d"]["e"] is None


def test_status_exposes_frame_quality():
    p = poller_mod.Poller(
        {"frames": lambda: {"n_frames": 396, "n_series": 10,
                            "metrics": [{"key": "resolution", "label": "CTF resolution",
                                         "unit": "A", "n": 396, "min": 7.0, "max": 10.6,
                                         "median": 7.6, "bins": [{"lo": 7.0, "hi": 10.6,
                                                                  "n": 396}]}],
                            "skipped": [], "worst": [], "series": []}},
        {"frames": 10.0})
    p.refresh_due()
    f = status_server.build_status(p.snapshot())["frames"]
    assert f["n_frames"] == 396 and f["n_series"] == 10
    assert f["metrics"][0]["median"] == 7.6


def test_frames_is_an_empty_object_when_unavailable():
    p = poller_mod.Poller({"squeue": lambda: []}, {"squeue": 10.0})
    p.refresh_due()
    assert status_server.build_status(p.snapshot())["frames"] == {}


def test_page_has_a_frames_tab_wired_to_its_containers():
    page = status_server.PAGE
    assert 'data-t="frames"' in page and 'id="tab-frames"' in page
    for el in ("frsum", "frhist", "frskip", "frseries", "frworst"):
        assert f'id="{el}"' in page
    js = page.split("<script>", 1)[1]
    assert "function renderFrames(" in js
    assert "renderFrames(s)" in js          # actually called from the status render
    assert "'frames'" in js                 # and reachable from the tab list


def test_every_tab_button_has_a_panel_and_a_keyboard_shortcut():
    """A tab added to the bar but missed in TABS or the digit map is invisible
    to showTab and to the keyboard, which is how a panel silently never opens.
    """
    import re
    page = status_server.PAGE
    js = page.split("<script>", 1)[1].split("</script>", 1)[0]
    buttons = re.findall(r'data-t="(\w+)"', page)
    tabs = re.search(r"TABS=\[([^\]]*)\]", js).group(1)
    listed = re.findall(r"'(\w+)'", tabs)
    mapped = re.findall(r"\d+:'(\w+)'", js)
    assert buttons == listed, f"tab bar {buttons} != TABS {listed}"
    assert sorted(buttons) == sorted(mapped), f"digit map misses {set(buttons) - set(mapped)}"
    for t in buttons:
        assert f'id="tab-{t}"' in page


def test_status_joins_dose_onto_frame_resolution():
    """The damage curve needs both halves: acquisition order from the mdocs
    and CTF resolution from WARP's frame cache."""
    p = poller_mod.Poller(
        {"frames": lambda: {"n_frames": 2, "n_series": 0, "metrics": [],
                            "skipped": [], "worst": [], "series": [],
                            "by_name": {"TS_1_02.mrc": 7.0, "TS_1_01.mrc": 9.0}},
         "acquisition": lambda: {"TS_1": {
             "n": 2, "total_dose": 6.0, "scheme": "dose-symmetric",
             "stage_drift": 0.05,
             "tilts": [{"order": 0, "tilt": 0.0, "dose": 3.0, "cum_dose": 3.0,
                        "movie": "TS_1_02.mrc"},
                       {"order": 1, "tilt": -40.0, "dose": 3.0, "cum_dose": 6.0,
                        "movie": "TS_1_01.mrc"}]}}},
        {"frames": 10.0, "acquisition": 10.0})
    p.refresh_due()
    frames = status_server.build_status(p.snapshot())["frames"]
    pts = frames["dose"]["TS_1"]["points"]
    assert [(x["cum_dose"], x["resolution"]) for x in pts] == [(3.0, 7.0), (6.0, 9.0)]
    # by_name is a join key, not something the browser needs.
    assert "by_name" not in frames


def test_status_does_not_mutate_the_pollers_cached_frames():
    """snapshot() hands out the poller's own dicts; popping the join key from
    one would delete it for every later request."""
    data = {"n_frames": 1, "n_series": 0, "metrics": [], "skipped": [],
            "worst": [], "series": [], "by_name": {"a.mrc": 7.0}}
    p = poller_mod.Poller({"frames": lambda: data}, {"frames": 10.0})
    p.refresh_due()
    status_server.build_status(p.snapshot())
    status_server.build_status(p.snapshot())
    assert "by_name" in data


def test_status_exposes_tm_scores_and_alignment():
    p = poller_mod.Poller(
        {"tm_scores": lambda: {"ribo": {"n": 5000, "min": 0.17, "max": 0.51,
                                        "bins": [0, 5000], "series": [],
                                        "score_type": "FLCFScore",
                                        "n_capped": 1, "n_unknown_cap": 0}},
         "alignment": lambda: {"TS_1": {"axis": 84.9, "max_shift_px": 52.7,
                                        "dark": 0, "inverted": True,
                                        "track": [], "n_tilts": 44}}},
        {"tm_scores": 10.0, "alignment": 10.0})
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    assert body["tm_scores"]["ribo"]["n"] == 5000
    assert body["alignment"]["TS_1"]["inverted"] is True


def test_new_panels_are_empty_objects_when_unavailable():
    p = poller_mod.Poller({"squeue": lambda: []}, {"squeue": 10.0})
    p.refresh_due()
    body = status_server.build_status(p.snapshot())
    assert body["tm_scores"] == {} and body["alignment"] == {}
    assert body["frames"] == {}


def test_page_wires_the_score_dose_and_alignment_panels():
    page = status_server.PAGE
    for el in ("tmscores", "frdose", "fralign"):
        assert f'id="{el}"' in page
    js = page.split("<script>", 1)[1]
    for fn in ("renderTmScores", "scoreSvg", "renderDose", "doseSvg",
               "renderAlign", "shiftSvg"):
        assert f"function {fn}(" in js
    assert "renderTmScores(s)" in js
    assert "renderDose(f)" in js and "renderAlign(st,series)" in js


def test_dataset_tab_reloads_like_the_config_tab():
    """Both panels are filled by loadCfg, so both need the tab-open reload:
    opening #dataset from the URL otherwise raced the first ssh round trip and
    left the panel blank with nothing to explain it."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "if(name==='config'||name==='dataset') loadCfg();" in js
    assert 'id="ds"><span class="muted">loading' in status_server.PAGE


def test_config_survives_one_unreadable_conf(monkeypatch, server):
    """Regression: a single try/except around four independent reads meant a
    missing species conf blanked the dataset panel too -- it reported "no
    dataset config available" for a pipeline.conf that read perfectly well,
    and said nothing about why."""
    base, editor = server

    def boom(*a, **k):
        raise FileNotFoundError("/run/opus-et-warp/species_gone.conf: "
                                "cat: No such file or directory")

    monkeypatch.setattr(editor, "current_values", boom)
    with urllib.request.urlopen(f"{base}/api/config") as r:
        body = json.loads(r.read())
    assert body["values"] == {}                    # the one that failed
    assert body["dataset"], "dataset must survive a species-conf failure"
    assert body["species_available"], "species listing must survive too"
    assert "species_gone.conf" in (body["problem"] or "")


def test_config_reports_no_problem_when_every_read_works(server):
    base, _ = server
    with urllib.request.urlopen(f"{base}/api/config") as r:
        assert json.loads(r.read())["problem"] is None


def test_qc_sections_follow_pipeline_order():
    """Alphabetical put "Gate 2 · pick overlays" above the reconstruction the
    picks are drawn on top of."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "QC_SRC_ORDER=['qc','gate1_qc','gate2_qc','gate3_qc','gate4_qc']" in js
    assert "qcSrcRank(a)-qcSrcRank(b)" in js


def test_species_filter_does_not_claim_unlabelled_overlays():
    """120 overlays in this run are named `TS_026_slab142_all.png` -- no
    species anywhere in the path. Passing them through every species filter
    asserted they were ribo when nothing on disk says so."""
    js = status_server.PAGE.split("<script>", 1)[1]
    assert "function keepSpecies(" in js
    assert "want==='unlabelled' ? !e.species : e.species===want" in js
    # A slice has no species to begin with, so it is never filtered out by one.
    assert "if(e.kind!=='overlay') return true;" in js
    assert "name no species in the filename" in js
