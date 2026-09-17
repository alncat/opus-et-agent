import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sources


SQUEUE_OUT = (
    "159967|train_opuset|RUNNING|2:14:03|normal|node07\n"
    "159968|warp_export_parts|PENDING|0:00|normal|(Resources)\n"
)

# Tilt-series names are NOT always TS_*; these must survive verbatim.
SACCT_OUT = (
    "159960|recon_Position_1|COMPLETED|0:31:02|normal|node02\n"
    "159961|recon_L1G1_ts_001|FAILED|0:00:12|normal|node03\n"
)


def test_parse_squeue_reads_all_fields():
    jobs = sources.parse_squeue(SQUEUE_OUT)
    assert [j.job_id for j in jobs] == ["159967", "159968"]
    assert jobs[0].state == "RUNNING"
    assert jobs[0].elapsed == "2:14:03"
    assert jobs[0].nodelist == "node07"
    assert jobs[1].state == "PENDING"


def test_parse_squeue_ignores_blank_lines():
    assert sources.parse_squeue("\n\n") == []


def test_parse_sacct_preserves_non_ts_names():
    jobs = sources.parse_sacct(SACCT_OUT)
    assert jobs[0].name == "recon_Position_1"
    assert jobs[1].name == "recon_L1G1_ts_001"
    assert jobs[1].state == "FAILED"


def test_merge_jobs_prefers_live_squeue_entry():
    """squeue forgets finished jobs, so sacct fills history -- but when both
    have a job, the live squeue row wins."""
    live = [sources.Job("1", "a", "RUNNING", "1:00", "normal", "n1")]
    hist = [
        sources.Job("1", "a", "COMPLETED", "1:00", "normal", "n1"),
        sources.Job("2", "b", "FAILED", "0:10", "normal", "n2"),
    ]
    merged = {j.job_id: j for j in sources.merge_jobs(live, hist)}
    assert merged["1"].state == "RUNNING"
    assert merged["2"].state == "FAILED"


def test_parse_config_keeps_value_and_inline_comment():
    text = (
        '# header comment\n'
        'OUTPUT_ANGPIX="4.2"                     # exported subtomogram pixel size\n'
        'ZDIM=8\n'
        '\n'
        '# COMMENTED_OUT="x"\n'
    )
    cfg = sources.parse_config(text)
    assert cfg["OUTPUT_ANGPIX"].value == "4.2"
    assert cfg["OUTPUT_ANGPIX"].comment == "# exported subtomogram pixel size"
    assert cfg["OUTPUT_ANGPIX"].lineno == 1
    assert cfg["ZDIM"].value == "8"
    assert cfg["ZDIM"].comment == ""
    # A commented-out assignment is not a live key.
    assert "COMMENTED_OUT" not in cfg


def test_phase_completion_delegates_to_conductor():
    vj = {"checks": [
        {"status": "phase_completion", "phase": "5", "done": 2, "total": 5},
    ]}
    assert sources.phase_completion(vj)["5"]["completion"] == "partial"


def test_read_run_state_parses_phases():
    st = sources.read_run_state('{"phases": {"5": {"status": "running"}}}')
    assert st["phases"]["5"]["status"] == "running"


def test_squeue_command_avoids_the_non_portable_me_flag():
    """`squeue --me` needs SLURM >= 20.02; an older cluster rejects it with
    "unrecognized option '--me'" and the jobs panel stays permanently empty."""
    joined = " ".join(sources.SQUEUE_ARGV)
    assert "--me" not in joined
    # resolves the user at run time without hardcoding a username
    assert "whoami" in joined


TS_OUT = (
    "TS_026|44|-42.00|44.00|101.5\n"
    "TS_029|43|-50.00|34.00|99.1\n"
    "Position_1|31|-30.00|30.00|70.8\n"     # non-TS_ naming must survive
    "\n"
)


def test_parse_tilt_series_reads_counts_ranges_and_dose():
    ts = sources.parse_tilt_series(TS_OUT)
    assert [t.name for t in ts] == ["TS_026", "TS_029", "Position_1"]
    assert ts[0].n_tilts == 44
    assert (ts[0].min_tilt, ts[0].max_tilt) == (-42.0, 44.0)
    assert ts[0].max_dose == 101.5


def test_parse_tilt_series_keeps_asymmetric_ranges_unsorted():
    """A tilt range like -50/+34 is genuinely asymmetric; it must not be
    normalised, since the missing-wedge angles are directional."""
    ts = {t.name: t for t in sources.parse_tilt_series(TS_OUT)}
    assert ts["TS_029"].min_tilt == -50.0
    assert ts["TS_029"].max_tilt == 34.0


def test_parse_tilt_series_ignores_blank_and_malformed_rows():
    assert sources.parse_tilt_series("\n\ngarbage\n") == []


def test_tilt_series_cmd_reads_the_angle_column_from_the_star_header():
    """The tilt column index is declared by `_wrpAngleTilt #N`; hardcoding
    column 2 would break on a tomostar with a different field order."""
    cmd = " ".join(sources.tilt_series_cmd("/run/tomostar"))
    assert "_wrpAngleTilt" in cmd
    assert "/run/tomostar" in cmd


def test_parse_qc_list_returns_relative_paths():
    out = sources.parse_qc_list(
        "qc/TS_026_xy.png\nqc/TS_026_xz.png\ngate2_qc/fas_TS028_slab142_all.png\n\n")
    assert out == ["qc/TS_026_xy.png", "qc/TS_026_xz.png",
                   "gate2_qc/fas_TS028_slab142_all.png"]


def test_parse_qc_list_drops_anything_that_escapes_the_run_dir():
    """The listing is the allowlist for serving images, so a traversal entry
    must never reach it."""
    out = sources.parse_qc_list("qc/ok.png\n../../etc/passwd\n/abs/path.png\nqc/../x.png\n")
    assert out == ["qc/ok.png"]


def test_parse_qc_list_keeps_nested_pngs_under_qc_dirs():
    out = sources.parse_qc_list(
        "qc/gate2_j360/tomo1_overlay.png\nother/secret.png\n")
    assert out == ["qc/gate2_j360/tomo1_overlay.png"]


def test_qc_list_cmd_scans_the_known_qc_directories():
    cmd = " ".join(sources.qc_list_cmd("/run"))
    assert "/run" in cmd
    for d in ("qc", "gate2_qc"):
        assert d in cmd


def test_qc_list_cmd_walks_nested_pngs():
    cmd = " ".join(sources.qc_list_cmd("/run"))
    assert "find" in cmd
    assert "*.png" in cmd


KNOWN = ["TS_026", "TS_028", "TS_030", "TS_034"]


def test_parse_qc_name_reads_slice_previews():
    e = sources.parse_qc_name("qc/TS_026_xy.png", KNOWN)
    assert (e["tomo"], e["kind"], e["label"]) == ("TS_026", "slice", "XY slice")
    assert sources.parse_qc_name("qc/TS_026_xz.png", KNOWN)["label"] == "XZ slice"


def test_parse_qc_name_reads_handedness():
    e = sources.parse_qc_name("qc/handedness_TS_026.png", KNOWN)
    assert e["tomo"] == "TS_026" and e["kind"] == "handedness"


def test_parse_qc_name_reads_pick_overlays_with_species_prefix():
    e = sources.parse_qc_name("gate2_qc/fas_TS028_slab142_all.png", KNOWN)
    assert e["kind"] == "overlay"
    assert e["species"] == "fas"
    assert e["slab"] == 142
    assert e["variant"] == "all"
    # gate2_qc writes TS028 while the tilt series is TS_028; they must unify
    assert e["tomo"] == "TS_028"


def test_parse_qc_name_reads_overlays_without_a_species_prefix():
    e = sources.parse_qc_name("gate2_qc/TS_026_slab142_topN.png", KNOWN)
    assert (e["tomo"], e["species"], e["slab"], e["variant"]) == ("TS_026", None, 142, "topN")


def test_parse_qc_name_reads_on_demand_overlays():
    e = sources.parse_qc_name("qc_ondemand/ribo_TS_034_slab143_all.png", KNOWN)
    assert (e["tomo"], e["species"], e["slab"]) == ("TS_034", "ribo", 143)


def test_parse_qc_name_falls_back_gracefully():
    e = sources.parse_qc_name("qc/something_unexpected.png", KNOWN)
    assert e["kind"] == "other" and e["tomo"] is None
    assert e["label"]           # still shows something usable


def test_parse_qc_name_reads_a_depth_suffixed_slice():
    """A slice rendered at a chosen z carries it in the filename, or a second
    render at another depth would overwrite the first."""
    e = sources.parse_qc_name("qc_ondemand/TS_026_z100_xy.png", KNOWN)
    assert e["kind"] == "slice"
    assert e["tomo"] == "TS_026"
    assert e["slab"] == 100
    assert e["label"] == "XY slice · z=100"


def test_parse_qc_name_still_reads_an_unsuffixed_slice():
    e = sources.parse_qc_name("qc/TS_026_xy.png", KNOWN)
    assert e["tomo"] == "TS_026" and e["slab"] is None and e["label"] == "XY slice"


MITO = ["MITO-260908-3-3-tomo1", "MITO-260908-3-3-tomo10", "MITO-260908-3-3-tomo7"]


def test_parse_qc_name_reads_nested_gate_overlays():
    """Gate 2 overlays live in qc/gate2_<species>/, not only in gate2_qc/."""
    e = sources.parse_qc_name(
        "qc/gate2_j360/MITO-260908-3-3-tomo10_overlay_slab110_all.png", MITO)
    assert e["section"] == "gate2_qc"
    assert e["tomo"] == "MITO-260908-3-3-tomo10"
    assert e["species"] == "j360"
    assert (e["kind"], e["slab"], e["variant"]) == ("overlay", 110, "all")


def test_parse_qc_name_reads_a_bare_overlay_in_a_species_folder():
    e = sources.parse_qc_name(
        "qc/gate2_s8ugi_min/MITO-260908-3-3-tomo1_overlay.png", MITO)
    assert e["section"] == "gate2_qc"
    assert e["species"] == "s8ugi_min"
    assert e["tomo"] == "MITO-260908-3-3-tomo1"
    assert e["label"] == "picks"


def test_parse_qc_name_treats_picks_folder_as_gate_2():
    e = sources.parse_qc_name(
        "qc/j360_picks/MITO-260908-3-3-tomo1_top200_slabs.png", MITO)
    assert e["section"] == "gate2_qc"
    assert e["species"] == "j360"
    assert e["tomo"] == "MITO-260908-3-3-tomo1"
    assert e["variant"] == "topN"


def test_parse_qc_name_keeps_dataset_overviews_untomoed():
    e = sources.parse_qc_name("qc/j360_template_check.png", MITO)
    assert e["section"] == "qc"
    assert e["tomo"] is None
    assert "template" in e["label"]


def test_parse_inventory_flags_each_stage():
    rows = sources.parse_inventory("TS_01|1|1|1|1|0\nTS_02|1|1|0|0|0\n")
    assert rows[0] == {"name": "TS_01", "stack": True, "aligned": True,
                       "recon": True, "tm": True, "export": False}
    assert rows[1]["recon"] is False and rows[1]["tm"] is False


def test_inventory_cmd_probes_the_expected_paths():
    cmd = sources.inventory_cmd("/run")
    script = cmd[2]
    assert cmd[:2] == ["sh", "-c"]
    for needle in ("tomostar/*.tomostar", "$ts.st", "_ali.mrc",
                   "*Apx.mrc", "warp_star", "_matching.star"):
        assert needle in script


def test_parse_elapsed_reads_every_slurm_format():
    assert sources.parse_elapsed("12:34") == 754
    assert sources.parse_elapsed("1:12:03") == 4323
    assert sources.parse_elapsed("2-03:04:05") == 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert sources.parse_elapsed("INVALID") is None
    assert sources.parse_elapsed("") is None


def test_parse_sacct_extracts_gpu_count_and_reads_legacy_rows():
    jobs = sources.parse_sacct(
        "1041|import|COMPLETED|1:00:00|main|n3|billing=1,cpu=8,gres/gpu=4,mem=16G\n")
    assert jobs[0].gpu == 4
    legacy = sources.parse_sacct("1041|import|COMPLETED|1:00:00|main|n3\n")
    assert legacy[0].gpu is None


def test_parse_training_reads_checkpoints_and_loss_curves():
    raw = ("RUN opuset/ribo/z8\n"
           "COUNT opuset/ribo/z8 12\n"
           "RAW opuset/ribo/z8 [Epoch 1 (5.2s)] loss=-1.23\n"
           "RAW opuset/ribo/z8 [Epoch 2 (5.1s)] loss=-1.45\n"
           "RUN opuset/fas/z8\n"
           "COUNT opuset/fas/z8 3\n")
    runs = sources.parse_training(raw)
    assert runs[0]["dir"] == "opuset/ribo/z8"
    assert runs[0]["weights"] == 12
    assert runs[0]["points"] == [{"epoch": 1, "loss": -1.23},
                                 {"epoch": 2, "loss": -1.45}]
    assert runs[1]["weights"] == 3 and runs[1]["points"] == []


def test_parse_training_reads_plain_epoch_loss_pairs():
    raw = ("RUN d\n"
           "RAW d 1 -2.5\n"
           "RAW d epoch 2 loss 0.000e+00\n")
    runs = sources.parse_training(raw)
    assert runs[0]["points"] == [{"epoch": 1, "loss": -2.5},
                                 {"epoch": 2, "loss": 0.0}]


def test_training_cmd_scans_output_dirs_and_logs():
    script = sources.training_cmd("/run")[2]
    assert "weights.*.pkl" in script and "loss.txt" in script
    assert "train_opuset_" in script


def test_parse_runs_summarizes_sibling_run_state():
    raw = ('RUNJSON agent\n'
           '{"phases": {"1": {"status": "done"}, "2": {"status": "checkpoint"}}}\n'
           'ENDRUN\n'
           'RUNJSON other\n{"phases": {}}\nENDRUN\n')
    runs = sources.parse_runs(raw)
    assert runs[0]["dir"] == "agent"
    assert runs[0]["done"] == 1 and runs[0]["total"] == 2
    assert runs[0]["waiting"] == ["2"]
    assert runs[1]["total"] == 0 and runs[1]["waiting"] == []


def test_parse_runs_survives_unparseable_state():
    raw = "RUNJSON broken\nnot json at all\nENDRUN\n"
    runs = sources.parse_runs(raw)
    assert runs[0]["dir"] == "broken" and runs[0]["total"] == 0


def test_remote_scripts_never_contain_dollar_hash():
    """Regression: the training scan once built `sed "s#/[^/]*$##"`, where the
    shell expands $# to the positional-argument count -- corrupting the sed
    program on the cluster, killing the pipeline silently, and leaving the
    Training tab empty while every test passed (the parsers never saw the
    real script). None of the remote scripts has a use for $#, so its
    presence is always this bug."""
    scripts = [
        sources.inventory_cmd("/run")[2],
        sources.training_cmd("/run")[2],
        sources.runs_cmd("/parent")[2],
        sources.qc_list_cmd("/run"),
        sources.tilt_series_cmd("/run")[2],
        sources.thumb_script("/run"),
        # panels added later must stay covered too
        sources.funnel_cmd("/run")[2],
        sources.m_cmd("/run")[2],
        sources.frames_cmd("/run")[2],
        sources.tm_scores_cmd("/run")[2],
        sources.mdoc_cmd("/run")[2],
        sources.align_cmd("/run")[2],
        sources.recon_cmd("/run")[2],
    ]
    for script in scripts:
        assert "$#" not in script, f"fragile $# in: {script[:120]}"


def test_training_cmd_reduces_tqdm_logs_per_epoch():
    """Slurm training logs are tqdm spam: thousands of CR-separated updates
    per epoch, each carrying batch-level metrics. The scan must reduce them
    remotely (CR split, last value per epoch per metric) or a curve is only
    epoch-1 batch noise."""
    script = sources.training_cmd("/run")[2]
    assert "tr '\\r' '\\n'" in script
    # epoch MEAN of the batch losses, not the last batch: one batch is a
    # noisy draw out of thousands and made a falling run look flat
    assert "sum_l[ep] += lo; n_l[ep]++" in script
    assert "sum_l[e]/n_l[e]" in script
    assert "sort -n -k3" in script


def test_parse_df_reads_the_posix_columns():
    out = ("Filesystem 1024-blocks       Used  Available Capacity Mounted on\n"
           "work       5690831933440 4189163716608 1501668216832      75% /work\n")
    d = sources.parse_df(out)
    assert d["pct_used"] == 75
    assert d["avail_gb"] == round(1501668216832 / 1048576.0, 1)


def test_parse_df_survives_junk():
    assert sources.parse_df("") is None
    assert sources.parse_df("Filesystem\n") is None
    assert sources.parse_df("a b\n") is None


FUNNEL_OUT = (
    "ribo|picks||58000\n"
    "ribo|exported||58000\n"
    "ribo|selected|z8_expanded/sel_ribo.star|14797\n"
    "ribo|selected|z8_expanded/sel_ribo_TS028.star|3387\n"
    "fas|picks||4100\n"
    "junk line\n"
)


def test_parse_funnel_groups_counts_by_species():
    f = sources.parse_funnel(FUNNEL_OUT)
    assert f["ribo"]["picks"] == 58000
    assert f["ribo"]["exported"] == 58000
    assert f["fas"]["picks"] == 4100
    assert f["fas"]["exported"] is None      # not exported yet


def test_parse_funnel_lists_every_selection_largest_first():
    sel = sources.parse_funnel(FUNNEL_OUT)["ribo"]["selected"]
    assert [s["count"] for s in sel] == [14797, 3387]
    assert sel[0]["name"] == "z8_expanded/sel_ribo.star"


def test_funnel_cmd_counts_star_data_rows_not_lines():
    """A STAR file has ~10 header lines; counting them would inflate every
    stage."""
    cmd = " ".join(sources.funnel_cmd("/run"))
    assert "loop_" in cmd and "data_" in cmd
    assert "/run" in cmd


def test_parse_m_drops_the_infinite_dc_row():
    text = ("ribo|fsc|Infinity|1|1|1|1\n"
            "ribo|fsc|640.3|0.99|0.99|0.99|0.99\n"
            "ribo|fsc|8.0|0.5|0.5|0.5|0.5\n"
            "ribo|fsc|7.0|0.1|0.1|0.1|0.1\n")
    m = sources.parse_m(text)
    assert all(r["resolution"] != float("inf") for r in m["ribo"]["fsc"])
    assert len(m["ribo"]["fsc"]) == 3


def test_fsc_resolution_interpolates_the_0143_crossing():
    rows = [{"resolution": 10.0, "corrected": 0.9},
            {"resolution": 8.0, "corrected": 0.5},
            {"resolution": 7.0, "corrected": 0.1}]
    r = sources.fsc_resolution(rows, "corrected")
    assert 7.0 < r < 8.0          # between the bracketing points


def test_resolve_derived_computes_align_angpix():
    """ALIGN_ANGPIX is written as an awk product; showing the expression is
    useless and sourcing the conf to expand it is not acceptable."""
    vals = {"ANGPIX": "3.37", "BINNING_FACTOR": "4"}
    raw = '$(awk "BEGIN {printf \\"%.6f\\", $ANGPIX * $BINNING_FACTOR}")'
    assert sources.resolve_derived(raw, vals) == "13.48"


def test_resolve_derived_follows_a_plain_variable_reference():
    assert sources.resolve_derived("$ANGPIX", {"ANGPIX": "3.37"}) == "3.37"
    assert sources.resolve_derived("${ANGPIX}", {"ANGPIX": "3.37"}) == "3.37"


def test_resolve_derived_refuses_anything_it_does_not_understand():
    """It must stay arithmetic, never a shell evaluator."""
    vals = {"ANGPIX": "3.37"}
    for raw in ['$(rm -rf ~)', '$(cat /etc/passwd)', '$(date +%s)', 'plain']:
        assert sources.resolve_derived(raw, vals) is None


def test_resolve_derived_gives_up_rather_than_looping():
    assert sources.resolve_derived("$A", {"A": "$B", "B": "$A"}) is None


# WARP's own quality cache, trimmed from a real run. Names are deliberately not
# TS_*: frames are grouped by the Tlts array, never by splitting the name.
FRAMES_JSON = """[
  {"Path":"Position_1_01.mrc","Stat":1,"Def":2.2438,"Phs":0,"Rsn":8.5,
   "AsX":0.0023,"AsY":0.0161,"Mtn":null,"Jnk":null,"Ptc":null},
  {"Path":"Position_1_02.mrc","Stat":1,"Def":2.2199,"Phs":0,"Rsn":7.0,
   "AsX":-0.0041,"AsY":0.0293,"Mtn":null,"Jnk":null,"Ptc":null},
  {"Path":"Position_1_03.mrc","Stat":1,"Def":1.9,"Phs":0,"Rsn":10.6,
   "AsX":0.06,"AsY":0.02,"Mtn":null,"Jnk":null,"Ptc":null},
  {"Path":"orphan_01.mrc","Stat":1,"Def":3.0,"Phs":0,"Rsn":9.0,
   "AsX":0.0,"AsY":0.0,"Mtn":null,"Jnk":null,"Ptc":null}
]"""

TS_JSON = """[
  {"Path":"Position_1.tomostar","Stat":1,
   "Tlts":["../warp_frameseries/Position_1_01.mrc",
           "../warp_frameseries/Position_1_missing.mrc",
           "../warp_frameseries/Position_1_02.mrc",
           "../warp_frameseries/Position_1_03.mrc"],
   "MinTilt":-50,"MaxTilt":34,"MeanAxis":84.6,
   "MinShiftX":0,"MeanShiftX":26.6,"MaxShiftX":101.0,
   "MinShiftY":0,"MeanShiftY":208.1,"MaxShiftY":1037.5,
   "MinDefocus":2.13,"MeanDefocus":2.83,"MaxDefocus":3.15,
   "Astigmatism":0.035,"MinPhase":0,"MeanPhase":0,"MaxPhase":0,
   "CtfResolution":7,"CtfInclination":6.83,"Ptc":null}
]"""

FRAMES_OUT = ("@@warp_frameseries\n" + FRAMES_JSON
              + "\n@@warp_tiltseries\n" + TS_JSON + "\n")


def test_frames_cmd_reads_warps_cache_not_the_xml():
    argv = sources.frames_cmd("/run")
    assert argv[0] == "sh" and argv[-1] == "/run"
    assert "processed_items.json" in argv[2]
    # 200 MB of per-movie XML holds the same numbers; never walk it.
    assert ".xml" not in argv[2]


def test_parse_frames_counts_movies_and_series():
    out = sources.parse_frames(FRAMES_OUT)
    assert out["n_frames"] == 4
    assert out["n_series"] == 1


def test_parse_frames_groups_by_tlts_not_by_name_split():
    out = sources.parse_frames(FRAMES_OUT)
    s = out["series"][0]
    assert s["name"] == "Position_1"
    # The orphan shares no series; a name-splitting grouper would invent one.
    assert s["n_tilts"] == 4
    assert len([r for r in s["track"] if r is not None]) == 3


def test_parse_frames_track_keeps_a_missing_tilt_as_a_gap():
    s = sources.parse_frames(FRAMES_OUT)["series"][0]
    # Position order must survive: 01, (missing), 02, 03.
    assert s["track"] == [8.5, None, 7.0, 10.6]


def test_parse_frames_astigmatism_is_the_component_magnitude():
    out = sources.parse_frames(FRAMES_OUT)
    astig = next(m for m in out["metrics"] if m["key"] == "astigmatism")
    # hypot(0.0023, 0.0161) is the DefocusDelta the per-movie XML records.
    assert abs(astig["min"] - 0.0) < 1e-9
    assert abs(astig["max"] - 0.06324555) < 1e-6


def test_parse_frames_skips_constant_and_unrecorded_metrics():
    out = sources.parse_frames(FRAMES_OUT)
    skipped = {s["key"]: s["note"] for s in out["skipped"]}
    assert skipped["phase"] == "every frame: 0"
    assert skipped["motion"] == "not recorded for this dataset"
    assert [m["key"] for m in out["metrics"]] == [
        "defocus", "astigmatism", "resolution"]


def test_parse_frames_names_the_worst_resolutions():
    worst = sources.parse_frames(FRAMES_OUT)["worst"]
    assert [w["name"] for w in worst[:2]] == ["Position_1_03.mrc", "orphan_01.mrc"]
    assert worst[0]["series"] == "Position_1"
    # A frame no tilt series claims still appears, with no series.
    assert worst[1]["series"] is None


def test_parse_frames_carries_series_alignment_stats():
    s = sources.parse_frames(FRAMES_OUT)["series"][0]
    assert (s["tilt_min"], s["tilt_max"]) == (-50.0, 34.0)
    assert s["max_shift"] == 1037.5
    assert s["resolution"] == 7.0
    assert s["inclination"] == 6.83


def test_parse_frames_survives_missing_or_broken_files():
    assert sources.parse_frames("")["n_frames"] == 0
    assert sources.parse_frames("@@warp_frameseries\nnot json\n")["n_frames"] == 0
    only_frames = sources.parse_frames("@@warp_frameseries\n" + FRAMES_JSON)
    assert only_frames["n_frames"] == 4 and only_frames["n_series"] == 0


def test_parse_frames_treats_null_as_unmeasured_not_zero():
    out = sources.parse_frames(FRAMES_OUT)
    assert all(m["key"] != "motion" for m in out["metrics"])


def test_histogram_bins_span_min_to_max_and_keep_every_value():
    h = sources.histogram([1.0, 2.0, 3.0, 4.0], bins=4)
    assert h["n"] == 4 and h["min"] == 1.0 and h["max"] == 4.0
    assert h["median"] == 2.5
    assert sum(b["n"] for b in h["bins"]) == 4
    # The maximum lands on the top edge and must not index past the last bin.
    assert h["bins"][-1]["n"] == 1


def test_histogram_handles_a_single_repeated_value():
    h = sources.histogram([5.0, 5.0, 5.0])
    assert h["bins"] == [{"lo": 5.0, "hi": 5.0, "n": 3}]


def test_histogram_ignores_none_and_non_finite():
    assert sources.histogram([None, "x", float("inf"), float("nan")]) is None
    h = sources.histogram([1.0, None, 3.0])
    assert h["n"] == 2


def _tm_line(label, ts, n, mn, mx, mean, cap, occupied):
    """occupied: {bin index: count}, the rest zero."""
    bins = [0] * sources.TM_BINS
    for i, c in occupied.items():
        bins[i] = c
    return (f"{label}|{ts}|{n}|{mn:.6f}|{mx:.6f}|{mean:.6f}|FLCFScore|{cap}|"
            + ",".join(str(b) for b in bins))


TM_OUT = "\n".join([
    _tm_line("ribo", "TS_026", 5000, 0.173143, 0.505790, 0.220212, 5000,
             {17: 900, 18: 3000, 21: 900, 45: 200}),
    # 8000 particles against a log that says 5000: the log is stale.
    _tm_line("ribo", "Position_1", 8000, 0.202907, 0.451490, 0.236405, 5000,
             {20: 5000, 22: 2800, 44: 200}),
    _tm_line("ribo", "TS_037", 300, 0.208455, 0.487942, 0.235329, 5000,
             {20: 250, 40: 50}),
    _tm_line("fas", "TS_026", 600, 0.179072, 0.289723, 0.192081, 600,
             {17: 400, 18: 200}),
]) + "\n"


def test_tm_scores_cmd_reads_the_only_file_that_has_them():
    argv = sources.tm_scores_cmd("/run")
    assert argv[0] == "sh" and argv[-1] == "/run"
    # Neither STAR flavour carries the score; the particle XML is the source.
    assert "_particles.xml" in argv[2] and "extraction.log" in argv[2]
    assert "warp_star" not in argv[2]


def test_parse_tm_scores_aggregates_series_per_species():
    out = sources.parse_tm_scores(TM_OUT)
    assert set(out) == {"ribo", "fas"}
    assert out["ribo"]["n"] == 13300
    assert out["ribo"]["min"] == 0.173143 and out["ribo"]["max"] == 0.505790
    assert [s["name"] for s in out["ribo"]["series"]] == [
        "Position_1", "TS_026", "TS_037"]
    # The species histogram is the sum of its series.
    assert out["ribo"]["bins"][20] == 5250
    assert sum(out["ribo"]["bins"]) == 13300


def test_parse_tm_scores_flags_a_capped_distribution():
    ribo = sources.parse_tm_scores(TM_OUT)["ribo"]
    caps = {s["name"]: s["capped"] for s in ribo["series"]}
    assert caps["TS_026"] is True          # n == the logged cap
    assert caps["TS_037"] is False         # found fewer than the cap
    assert ribo["n_capped"] == 1


def test_parse_tm_scores_distrusts_a_stale_extraction_log():
    """A real series here holds 8000 particles against a log saying 5000, and
    the XML is a day newer. Reporting cap 5000 would be simply wrong."""
    ribo = sources.parse_tm_scores(TM_OUT)["ribo"]
    stale = next(s for s in ribo["series"] if s["name"] == "Position_1")
    assert stale["cap"] is None and stale["capped"] is None
    assert ribo["n_unknown_cap"] == 1


def test_parse_tm_scores_ignores_malformed_lines():
    assert sources.parse_tm_scores("") == {}
    assert sources.parse_tm_scores("ribo|TS_026|notanumber\n") == {}


MDOC_OUT = "\n".join([
    # Dose-symmetric: acquisition order alternates sign, so ZValue order and
    # tilt order are different sequences.
    "TS_026|0|TiltAngle|-0.00499939",
    "TS_026|0|StagePosition|181.096 446.916",
    "TS_026|0|ExposureDose|2.35936",
    "TS_026|0|SubFramePath|TS_026_23.mrc",
    "TS_026|0|DateTime|05-Nov-15  15:21:00",
    "TS_026|1|TiltAngle|1.99476",
    "TS_026|1|StagePosition|181.196 446.916",
    "TS_026|1|ExposureDose|2.35936",
    # A microscope-PC path must reduce to the movie name.
    "TS_026|1|SubFramePath|X:\\data\\frames\\TS_026_24.mrc",
    "TS_026|2|TiltAngle|-2.00425",
    "TS_026|2|ExposureDose|2.35936",
    "TS_026|2|SubFramePath|TS_026_22.mrc",
    "TS_026|3|TiltAngle|3.99",
    "TS_026|3|ExposureDose|2.35936",
    "TS_026|3|SubFramePath|TS_026_25.mrc",
]) + "\n"


def test_parse_mdoc_accumulates_dose_in_acquisition_order():
    out = sources.parse_mdoc(MDOC_OUT)["TS_026"]
    assert out["n"] == 4
    assert [t["movie"] for t in out["tilts"]] == [
        "TS_026_23.mrc", "TS_026_24.mrc", "TS_026_22.mrc", "TS_026_25.mrc"]
    assert out["tilts"][0]["cum_dose"] == 2.3594
    assert out["tilts"][3]["cum_dose"] == 9.4374
    assert out["total_dose"] == 9.437


def test_parse_mdoc_detects_the_dose_symmetric_scheme():
    assert sources.parse_mdoc(MDOC_OUT)["TS_026"]["scheme"] == "dose-symmetric"
    continuous = "\n".join(
        f"TS_1|{i}|TiltAngle|{-40 + 2 * i}\nTS_1|{i}|ExposureDose|3.0"
        f"\nTS_1|{i}|SubFramePath|TS_1_{i:02d}.mrc" for i in range(40))
    assert sources.parse_mdoc(continuous)["TS_1"]["scheme"] == "continuous"


def test_parse_mdoc_reduces_a_windows_subframe_path():
    tilts = sources.parse_mdoc(MDOC_OUT)["TS_026"]["tilts"]
    assert tilts[1]["movie"] == "TS_026_24.mrc"


def test_parse_mdoc_measures_stage_drift_from_the_first_position():
    out = sources.parse_mdoc(MDOC_OUT)["TS_026"]
    assert abs(out["stage_drift"] - 0.1) < 1e-6


def test_join_dose_pairs_accumulated_dose_with_ctf_resolution():
    acq = sources.parse_mdoc(MDOC_OUT)
    joined = sources.join_dose(acq, {"TS_026_23.mrc": 7.0, "TS_026_22.mrc": 8.4})
    pts = joined["TS_026"]["points"]
    # Only movies the frame cache knows about; order follows acquisition.
    assert [p["order"] for p in pts] == [0, 2]
    assert pts[0]["cum_dose"] == 2.3594 and pts[0]["resolution"] == 7.0
    assert joined["TS_026"]["scheme"] == "dose-symmetric"


def test_join_dose_is_empty_without_either_side():
    assert sources.join_dose({}, {"a": 1.0}) == {}
    assert sources.join_dose(sources.parse_mdoc(MDOC_OUT), {}) == {}


ALIGN_OUT = "\n".join([
    "TS_026|aln|0|84.8990|3.707|52.593|-44.00",
    "TS_026|aln|1|84.8990|4.686|47.131|-42.00",
    "TS_026|aln|2|84.8990|0.000|0.000|0.00",
    "TS_026|dark|2",
    'TS_026|xml|<TiltSeries DataDirectory="/some/where" AreAnglesInverted="True" '
    'PlaneNormal="-0.005, 0.107, 0.994" CTFResolutionEstimate="7.0"',
    "Position_1|aln|0|85.1000|1.000|1.000|-40.00",
]) + "\n"


def test_parse_align_reads_shift_track_and_tilt_axis():
    out = sources.parse_align(ALIGN_OUT)["TS_026"]
    assert out["axis"] == 84.8990
    assert out["n_tilts"] == 3
    # hypot(3.707, 52.593) is the largest of the three.
    assert abs(out["max_shift_px"] - 52.723) < 0.01
    assert out["track"][0]["tilt"] == -44.0


def test_parse_align_counts_dark_frames_and_reads_handedness():
    out = sources.parse_align(ALIGN_OUT)["TS_026"]
    assert out["dark"] == 2
    assert out["inverted"] is True
    # PlaneNormal's inclination duplicates CtfInclination, so it is not shown.
    assert "plane_tilt" not in out


def test_parse_align_handles_a_series_with_no_xml():
    out = sources.parse_align(ALIGN_OUT)["Position_1"]
    assert out["inverted"] is None and out["dark"] == 0
    assert out["n_tilts"] == 1


def test_parse_align_ignores_junk():
    assert sources.parse_align("") == {}
    assert sources.parse_align("TS_1|aln|0|nan|x|y|z\n")["TS_1"]["track"] == []


def test_inventory_export_match_is_boundary_aware():
    """TS_01 must not claim STAR rows belonging to TS_010 or TS_011: the grep
    anchors on a non-digit (or line end) after the name."""
    script = sources.inventory_cmd("/run")[2]
    assert 'grep -qE "${ts}([^0-9]|\\$)"' in script


def test_training_log_match_is_anchored_to_the_runs_own_output_line():
    """opuset/ribo/z8 must not adopt logs of z8_angpix337_OLD or z8_expanded
    (substring match), nor a warm-start --load line naming another run. The
    scan greps the script's own 'Output: <dir>' echo, anchored at line end."""
    script = sources.training_cmd("/run")[2]
    assert 'grep -Eils "Output: .*${d}\\$"' in script


def test_merge_jobs_keeps_history_gpu_for_live_rows():
    """squeue has no GPU allocation; a live row must inherit the historic
    value or running jobs silently drop out of the GPU-hours total."""
    live = [sources.Job("7", "train", "RUNNING", "3:00", "gpu", "g01")]
    hist = [sources.Job("7", "train", "RUNNING", "3:00", "gpu", "g01", gpu=4)]
    merged = sources.merge_jobs(live, hist)
    assert merged[0].gpu == 4 and merged[0].state == "RUNNING"
    # a live row that does carry its own gpu is left alone
    live2 = [sources.Job("8", "align", "RUNNING", "1:00", "main", "n1", gpu=1)]
    hist2 = [sources.Job("8", "align", "RUNNING", "1:00", "main", "n1", gpu=9)]
    assert sources.merge_jobs(live2, hist2)[0].gpu == 1


def test_training_cmd_emits_params_checkpoints_and_multimetric_curves():
    """The drill-down needs the trainer's echoed header (config + inputs),
    checkpoint mtimes for an ETA, and all five per-epoch metrics the tqdm
    lines carry -- not just loss."""
    script = sources.training_cmd("/run")[2]
    for needle in ("PARAM", "MTIME $d $e $(stat -c %Y", "LOG $d $log",
                   "beta=[-+0-9.eE]+", "snr=[-+0-9.eE]+", "std=[-+0-9.eE]+"):
        assert needle in script, needle


def test_parse_training_reads_params_mtimes_log_and_multimetric_points():
    raw = ("RUN opuset/fas/z8\n"
           "COUNT opuset/fas/z8 2\n"
           "PARAM opuset/fas/z8 Epochs: 40\n"
           "PARAM opuset/fas/z8 Batch size: 12\n"
           "PARAM opuset/fas/z8 Learning rate: 4e-5\n"
           "PARAM opuset/fas/z8 Output: /run/opuset/fas/z8\n"
           "MTIME opuset/fas/z8 1 1700000000\n"
           "MTIME opuset/fas/z8 2 1700000480\n"
           "LOG opuset/fas/z8 logs/train_opuset_160153.out\n"
           "RAW opuset/fas/z8 1 -0.0011 0.102 0.0496 0.00011 1.33\n"
           "RAW opuset/fas/z8 2 -0.00113 - - 0.00009 -\n")
    runs = sources.parse_training(raw)
    r = runs[0]
    assert r["params"]["Epochs"] == "40"
    assert r["params"]["Batch size"] == "12"
    assert r["params"]["Learning rate"] == "4e-5"
    assert r["params"]["Output"] == "/run/opuset/fas/z8"
    assert r["mtimes"] == {1: 1700000000, 2: 1700000480}
    assert r["log"] == "logs/train_opuset_160153.out"
    assert r["points"][0] == {"epoch": 1, "loss": -0.0011, "beta": 0.102,
                              "mu": 0.0496, "snr": 0.00011, "std": 1.33}
    # a metric the epoch did not print is absent, never zero
    assert r["points"][1]["loss"] == -0.00113 and r["points"][1]["snr"] == 0.00009
    assert "beta" not in r["points"][1] and "std" not in r["points"][1]


def test_recon_cmd_reads_mrc_headers():
    script = sources.recon_cmd("/run")[2]
    assert "*Apx.mrc" in script and "od -An -t d4" in script and "od -An -t f4" in script
    # the voxel-size fields sit at byte 40, i.e. dd block 10 of 4 bytes
    assert "skip=10 count=3" in script


def test_parse_recon_flags_dimension_mismatch_across_series():
    # cella is the cell EDGE in angstrom: voxel = cella / grid count
    raw = ("RECON TS_026 960 928 460 12940.8 12509.439 6740\n"
           "RECON TS_027 960 928 460 12940.8 12509.439 6740\n"
           "RECON TS_028 480 464 230 6470.4 6254.72 3370\n")
    rows = sources.parse_recon(raw)
    by = {r["name"]: r for r in rows}
    assert (by["TS_026"]["nx"], by["TS_026"]["ny"], by["TS_026"]["nz"]) == (960, 928, 460)
    assert by["TS_026"]["voxel_a"] == 13.48
    assert by["TS_026"]["dims_consistent"] is False
    assert by["TS_027"]["dims_consistent"] is False


def test_parse_recon_all_consistent_when_dims_match():
    raw = ("RECON TS_026 960 928 460 12940.8 12509.439 6740\n"
           "RECON TS_027 960 928 460 12940.8 12509.439 6740\n")
    rows = sources.parse_recon(raw)
    assert all(r["dims_consistent"] for r in rows)
