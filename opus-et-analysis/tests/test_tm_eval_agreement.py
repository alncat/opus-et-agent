import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import starfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tm_eval_agreement as te


def _write_star(path, coords, tomo="TS_034", angpix=3.37, scores=None):
    n = len(coords)
    d = {
        "rlnCoordinateX": [c[0] for c in coords],
        "rlnCoordinateY": [c[1] for c in coords],
        "rlnCoordinateZ": [c[2] for c in coords],
        "rlnMicrographName": [f"{tomo}.tomostar"] * n,
        "rlnPixelSize": [angpix] * n,
    }
    if scores is not None:
        d["rlnScore"] = list(scores)
    starfile.write(pd.DataFrame(d), str(path), overwrite=True)


def test_read_star_coords_reconciles_pixel_size(tmp_path):
    # the same physical point at two pixel sizes -> identical Angstrom coords
    a = tmp_path / "a.star"; _write_star(a, [(100, 100, 100)], angpix=3.37)
    b = tmp_path / "b.star"; _write_star(b, [(25, 25, 25)], angpix=13.48)
    ca = te.read_star_coords(str(a)); cb = te.read_star_coords(str(b))
    assert ca["x_A"].iloc[0] == pytest.approx(337.0)
    assert cb["x_A"].iloc[0] == pytest.approx(337.0)  # 25 * 13.48


def test_read_star_coords_filters_and_normalizes_tomo(tmp_path):
    p = tmp_path / "m.star"
    df = pd.DataFrame({
        "rlnCoordinateX": [1.0, 2.0], "rlnCoordinateY": [1.0, 2.0], "rlnCoordinateZ": [1.0, 2.0],
        "rlnMicrographName": ["TS_034.tomostar", "TS_028.tomostar"], "rlnPixelSize": [3.37, 3.37],
    })
    starfile.write(df, str(p), overwrite=True)
    out = te.read_star_coords(str(p), tomo="TS_034_13.48Apx.mrc")  # odd name normalizes to TS_034
    assert set(out["tomo"]) == {"TS_034"} and len(out) == 1


def test_read_star_coords_score_detection_and_absence(tmp_path):
    p = tmp_path / "s.star"; _write_star(p, [(1, 1, 1)], scores=[0.7])
    assert te.read_star_coords(str(p))["score"].iloc[0] == pytest.approx(0.7)
    p2 = tmp_path / "ns.star"; _write_star(p2, [(1, 1, 1)])  # no score column
    assert np.isnan(te.read_star_coords(str(p2))["score"].iloc[0])


def test_read_star_coords_requires_pixel_size(tmp_path):
    p = tmp_path / "np.star"
    df = pd.DataFrame({"rlnCoordinateX": [1.0], "rlnCoordinateY": [1.0], "rlnCoordinateZ": [1.0],
                       "rlnMicrographName": ["TS_034.tomostar"]})
    starfile.write(df, str(p), overwrite=True)
    with pytest.raises(ValueError):
        te.read_star_coords(str(p))               # no rlnPixelSize, no angpix
    out = te.read_star_coords(str(p), angpix=3.37)  # fallback works
    assert out["x_A"].iloc[0] == pytest.approx(3.37)


def test_greedy_match_one_to_one_within_radius():
    cand = np.array([[0, 0, 0], [100, 0, 0], [10, 0, 0]], float)  # 3rd near ref0 but ref0 taken
    ref = np.array([[0, 0, 0], [100, 0, 0]], float)
    n, mc, mr = te.greedy_match(cand, ref, radius_A=15)
    assert n == 2
    assert set(zip(mc.tolist(), mr.tolist())) == {(0, 0), (1, 1)}


def test_greedy_match_respects_radius():
    cand = np.array([[0, 0, 0]], float); ref = np.array([[200, 0, 0]], float)
    assert te.greedy_match(cand, ref, 136)[0] == 0


def test_greedy_match_order_priority():
    # two candidates near one reference; the FIRST in row order claims it
    cand = np.array([[1, 0, 0], [2, 0, 0]], float); ref = np.array([[0, 0, 0]], float)
    n, mc, mr = te.greedy_match(cand, ref, 10)
    assert n == 1 and mc[0] == 0


def test_greedy_match_empty_sets():
    assert te.greedy_match(np.zeros((0, 3)), np.array([[0, 0, 0]], float), 10)[0] == 0
    assert te.greedy_match(np.array([[0, 0, 0]], float), np.zeros((0, 3)), 10)[0] == 0


def _pts(xs, scores=None):
    d = {"x_A": list(xs), "y_A": [0.0] * len(xs), "z_A": [0.0] * len(xs)}
    if scores is not None:
        d["score"] = list(scores)
    return pd.DataFrame(d)


def test_agreement_perfect_self():
    ref = _pts([0, 100, 200])
    cand = _pts([0, 100, 200], scores=[0.9, 0.8, 0.7])
    m = te.agreement_at_threshold(cand, ref, 50, thr=0.0)
    assert (m["tp"], m["fp"], m["fn"]) == (3, 0, 0)
    assert m["precision"] == 1.0 and m["recall"] == 1.0 and m["f1"] == 1.0


def test_agreement_counts_fp_and_fn():
    ref = _pts([0, 100])
    cand = _pts([5, 900], scores=[0.9, 0.8])   # one matches ref0, one spurious; ref1 unmatched
    m = te.agreement_at_threshold(cand, ref, 50, thr=0.0)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1)
    assert m["precision"] == pytest.approx(0.5) and m["recall"] == pytest.approx(0.5)


def test_agreement_curve_best_f1_picks_clean_threshold():
    ref = _pts([0, 100, 200])
    cand = _pts([0, 100, 200, 900, 950], scores=[0.9, 0.85, 0.8, 0.4, 0.3])  # 3 true hi, 2 false lo
    best = te.best_f1(te.agreement_curve(cand, ref, 50))
    assert best["f1"] == pytest.approx(1.0)
    assert best["recall"] == 1.0 and best["precision"] == 1.0
    assert best["threshold"] == pytest.approx(0.8)


def test_agreement_at_threshold_matches_curve_point():
    ref = _pts([0, 100, 200])
    cand = _pts([0, 100, 200, 900, 950], scores=[0.9, 0.85, 0.8, 0.4, 0.3])
    at = te.agreement_at_threshold(cand, ref, 50, thr=0.8)
    pt = [p for p in te.agreement_curve(cand, ref, 50) if p["threshold"] == pytest.approx(0.8)][0]
    assert (at["tp"], at["fp"], at["fn"]) == (pt["tp"], pt["fp"], pt["fn"])


def test_agreement_reconciles_pixel_sizes_end_to_end(tmp_path):
    # candidate at bin4 (13.48), reference at bin1 (3.37), same physical points -> both match
    _write_star(tmp_path / "ref.star", [(100, 100, 100), (200, 200, 200)], angpix=3.37)
    _write_star(tmp_path / "cand.star", [(25, 25, 25), (50, 50, 50)], angpix=13.48, scores=[0.9, 0.8])
    ref = te.read_star_coords(str(tmp_path / "ref.star"))
    cand = te.read_star_coords(str(tmp_path / "cand.star"))
    assert te.agreement_at_threshold(cand, ref, 20, thr=0.0)["tp"] == 2


def test_best_f1_empty_curve():
    assert te.best_f1([])["f1"] == 0.0


def test_agreement_tied_scores_prefix_consistent():
    ref = _pts([0, 100, 200])
    cand = _pts([0, 100, 200, 900], scores=[0.8, 0.8, 0.8, 0.8])  # all tied
    curve = te.agreement_curve(cand, ref, 50)
    assert len(curve) == 1
    assert (curve[0]["tp"], curve[0]["fp"], curve[0]["fn"]) == (3, 1, 0)
    at = te.agreement_at_threshold(cand, ref, 50, thr=0.8)
    assert (at["tp"], at["fp"], at["fn"]) == (3, 1, 0)


def test_agreement_empty_candidates_reports_all_missed():
    ref = _pts([0, 100, 200])
    cand = pd.DataFrame({"x_A": [], "y_A": [], "z_A": [], "score": []})
    best = te.best_f1(te.agreement_curve(cand, ref, 50))
    assert best["tp"] == 0 and best["fn"] == 3 and best["recall"] == 0.0


def test_agreement_groups_by_tomogram():
    # a candidate and a reference at identical local coords but DIFFERENT tomograms must NOT match
    ref = pd.DataFrame({"tomo": ["TS_028"], "x_A": [0.0], "y_A": [0.0], "z_A": [0.0]})
    cand = pd.DataFrame({"tomo": ["TS_034"], "x_A": [0.0], "y_A": [0.0], "z_A": [0.0], "score": [0.9]})
    m = te.agreement_at_threshold(cand, ref, 50, thr=0.0)
    assert (m["tp"], m["fp"], m["fn"]) == (0, 1, 1)
    cand2 = cand.copy(); cand2["tomo"] = ["TS_028"]  # same tomogram now -> matches
    assert te.agreement_at_threshold(cand2, ref, 50, thr=0.0)["tp"] == 1


def test_agreement_curve_recall_monotonic_as_threshold_drops():
    ref = _pts([0, 100, 200])
    cand = _pts([0, 100, 200, 900, 950], scores=[0.9, 0.85, 0.8, 0.4, 0.3])
    recalls = [p["recall"] for p in te.agreement_curve(cand, ref, 50)]  # thresholds descending
    assert all(recalls[i] <= recalls[i + 1] for i in range(len(recalls) - 1))


def test_read_star_coords_require_score_raises(tmp_path):
    p = tmp_path / "nos.star"; _write_star(p, [(1, 1, 1)])  # no score column
    with pytest.raises(ValueError):
        te.read_star_coords(str(p), require_score=True)


def test_cli_reports_best_f1(tmp_path):
    _write_star(tmp_path / "ref.star", [(0, 0, 0), (100, 100, 100)], tomo="TS_034", angpix=3.37)
    _write_star(tmp_path / "cand.star", [(0, 0, 0), (100, 100, 100)], tomo="TS_034",
                angpix=3.37, scores=[0.9, 0.8])
    script = Path(__file__).resolve().parents[1] / "scripts" / "tm_eval_agreement.py"
    out = subprocess.run(
        [sys.executable, str(script), "--candidate", str(tmp_path / "cand.star"),
         "--reference", str(tmp_path / "ref.star"), "--tomo", "TS_034", "--radius-A", "50",
         "--json", str(tmp_path / "o.json")],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    rec = json.loads((tmp_path / "o.json").read_text())
    assert rec["n_reference"] == 2
    assert rec["best"]["f1"] == pytest.approx(1.0)
