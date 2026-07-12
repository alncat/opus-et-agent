import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tm_picks_overlay as tpo

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tm_picks_overlay.py"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def _write_star(path, xyz, angpix=3.37, scores=None, tomo="TS_034", pixcol="rlnPixelSize"):
    import starfile
    d = {
        "rlnCoordinateX": [c[0] for c in xyz],
        "rlnCoordinateY": [c[1] for c in xyz],
        "rlnCoordinateZ": [c[2] for c in xyz],
        "rlnMicrographName": [f"{tomo}.tomostar"] * len(xyz),
        pixcol: [angpix] * len(xyz),
    }
    if scores is not None:
        d["rlnLCCmax"] = list(scores)
    starfile.write(pd.DataFrame(d), str(path), overwrite=True)


def _write_xml(path, xyz, scores):
    """scores entries may be None to omit that particle's <Score> (partial-missing)."""
    lines = ["<ParticleList>"]
    for (x, y, z), s in zip(xyz, scores):
        lines.append('  <Particle Filename="p">')
        lines.append(f'    <PickPosition X="{x}" Y="{y}" Z="{z}" Origin="0 0 0"/>')
        if s is not None:
            lines.append(f'    <Score Type="FLCFScore" Value="{s}"/>')
        lines.append("  </Particle>")
    lines.append("</ParticleList>")
    Path(path).write_text("\n".join(lines))


def _write_mrc(path, vol, angpix=13.48):
    import mrcfile
    with mrcfile.new(str(path), overwrite=True) as m:
        m.set_data(np.asarray(vol, dtype=np.float32))
        m.voxel_size = float(angpix)


# --------------------------------------------------------------------------- #
# reading picks
# --------------------------------------------------------------------------- #

def test_read_picks_star_coords_scores_angpix(tmp_path):
    p = tmp_path / "picks.star"
    _write_star(p, [(10, 20, 30), (40, 50, 60)], angpix=3.37, scores=[0.5, 0.9])
    coords, scores, src = tpo.read_picks(str(p))
    assert coords.shape == (2, 3)
    np.testing.assert_allclose(coords[0], [10, 20, 30])
    np.testing.assert_allclose(scores, [0.5, 0.9])
    assert src == pytest.approx(3.37)


def test_read_picks_star_no_score_returns_none(tmp_path):
    p = tmp_path / "p.star"
    _write_star(p, [(1, 2, 3)], scores=None)
    coords, scores, src = tpo.read_picks(str(p))
    assert scores is None
    assert coords.shape == (1, 3)


def test_read_picks_star_blank_lines_after_loop(tmp_path):
    p = tmp_path / "p.star"
    p.write_text(
        "data_\n\nloop_\n\n"
        "_rlnCoordinateX #1\n_rlnCoordinateY #2\n_rlnCoordinateZ #3\n"
        "_rlnDetectorPixelSize #4\n\n"
        "10 20 30 3.37\n40 50 60 3.37\n"
    )
    coords, scores, src = tpo.read_picks(str(p))
    assert coords.shape == (2, 3)
    np.testing.assert_allclose(coords[1], [40, 50, 60])
    assert src == pytest.approx(3.37)  # from rlnDetectorPixelSize


def test_read_picks_xml_pytom(tmp_path):
    p = tmp_path / "parts.xml"
    _write_xml(p, [(11, 22, 33), (44, 55, 66)], [0.7, 0.3])
    coords, scores, src = tpo.read_picks(str(p))
    assert coords.shape == (2, 3)
    np.testing.assert_allclose(coords[1], [44, 55, 66])
    np.testing.assert_allclose(scores, [0.7, 0.3])
    assert src is None  # PyTOM coords already on the tomogram grid


def _write_mixed_star(path):
    import starfile
    df = pd.DataFrame({
        "rlnCoordinateX": [1.0, 2.0], "rlnCoordinateY": [1.0, 2.0],
        "rlnCoordinateZ": [1.0, 2.0],
        "rlnMicrographName": ["TS_034.tomostar", "TS_041.tomostar"],
        "rlnPixelSize": [3.37, 3.37],
    })
    starfile.write(df, str(path), overwrite=True)


def test_star_tomo_filter(tmp_path):
    p = tmp_path / "all.star"
    _write_mixed_star(p)
    coords, _, _ = tpo.read_picks(str(p), tomo="TS_034")
    assert coords.shape == (1, 3)
    np.testing.assert_allclose(coords[0], [1, 1, 1])


def test_star_multi_tomo_warns_without_filter(tmp_path):
    p = tmp_path / "all.star"
    _write_mixed_star(p)
    msgs = []
    coords, _, _ = tpo.read_picks(str(p), warn=msgs.append)
    assert coords.shape == (2, 3)  # no --tomo -> all rows kept
    assert any("tomogram" in m.lower() for m in msgs)


def test_read_picks_xml_coords_angpix_override(tmp_path):
    p = tmp_path / "p.xml"
    _write_xml(p, [(4, 4, 4)], [0.5])
    coords, scores, src = tpo.read_picks(str(p), coords_angpix=6.74)
    assert src == pytest.approx(6.74)            # was silently dropped before the fix
    out = tpo.to_tomo_pixels(coords, src, tomo_angpix=13.48)
    np.testing.assert_allclose(out[0], [2, 2, 2])  # 4 * 6.74 / 13.48 = 2


def test_read_picks_xml_partial_scores_keeps_others(tmp_path):
    p = tmp_path / "p.xml"
    _write_xml(p, [(1, 1, 1), (2, 2, 2), (3, 3, 3)], [0.9, None, 0.5])
    coords, scores, src = tpo.read_picks(str(p))
    assert scores is not None                    # one missing must not null the rest
    assert scores[0] == pytest.approx(0.9)
    assert np.isnan(scores[1])
    assert tpo.select_top_n(scores, 1).tolist() == [0]  # NaN never wins


# --------------------------------------------------------------------------- #
# coordinate reconciliation
# --------------------------------------------------------------------------- #

def test_to_tomo_pixels_scaling():
    c = np.array([[8, 8, 8]], float)
    out = tpo.to_tomo_pixels(c, source_angpix=3.37, tomo_angpix=13.48)  # ratio 1/4
    np.testing.assert_allclose(out, [[2, 2, 2]])


def test_to_tomo_pixels_xml_identity():
    c = np.array([[5, 6, 7]], float)
    out = tpo.to_tomo_pixels(c, source_angpix=None, tomo_angpix=13.48)
    np.testing.assert_allclose(out, [[5, 6, 7]])


# --------------------------------------------------------------------------- #
# slab geometry
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("z0,thick,nz,expected", [
    (5, 4, 100, (3, 7)),
    (1, 4, 100, (0, 4)),
    (99, 4, 100, (96, 100)),
    (5, 2, 10, (4, 6)),
])
def test_slab_band(z0, thick, nz, expected):
    assert tpo.slab_band(z0, thick, nz) == expected


@pytest.mark.parametrize("thick", [0, -2])
def test_slab_band_nonpositive_thickness_raises(thick):
    with pytest.raises(ValueError):
        tpo.slab_band(5, thick, 100)


def test_slab_project_clipped_edge_min_max():
    vol = np.zeros((10, 3, 3), np.float32)
    vol[0] = 5.0
    vol[1] = 1.0
    # z0=0, thickness=4 -> band (0,4); slices 0..3 = [5,1,0,0]
    np.testing.assert_allclose(tpo.slab_project(vol, 0, 4, "max"), np.full((3, 3), 5.0))
    np.testing.assert_allclose(tpo.slab_project(vol, 0, 4, "min"), np.zeros((3, 3)))


def test_slab_project_modes():
    vol = np.zeros((10, 4, 4), np.float32)
    vol[4] = 1.0
    vol[5] = 3.0
    assert tpo.slab_project(vol, 5, 2, "mean").shape == (4, 4)
    np.testing.assert_allclose(tpo.slab_project(vol, 5, 2, "mean"), np.full((4, 4), 2.0))
    np.testing.assert_allclose(tpo.slab_project(vol, 5, 2, "min"), np.full((4, 4), 1.0))
    np.testing.assert_allclose(tpo.slab_project(vol, 5, 2, "max"), np.full((4, 4), 3.0))


def test_picks_in_slab_band():
    coords = np.array([[0, 0, 4.0], [0, 0, 5.0], [0, 0, 6.0], [0, 0, 7.0]])
    mask = tpo.picks_in_slab(coords, z0=5, thickness=2, nz=10)  # band [4,6)
    # z==6 is the exclusive hi boundary -> must be excluded (half-open invariant)
    assert mask.tolist() == [True, True, False, False]


def test_plan_slab_centers_evenly_spaced():
    z = np.array([0.0, 300.0])
    centers = tpo.plan_slab_centers(z, n_slabs=3, nz=500)
    assert centers == [50, 150, 250]


def test_plan_slab_centers_no_picks_uses_volume():
    centers = tpo.plan_slab_centers(np.array([]), n_slabs=2, nz=400)
    assert centers == [100, 300]  # bin-midpoints over the full [0,400)


# --------------------------------------------------------------------------- #
# top-N selection
# --------------------------------------------------------------------------- #

def test_select_top_n_descending():
    scores = np.array([0.1, 0.9, 0.5, 0.7])
    idx = tpo.select_top_n(scores, 2)
    assert set(idx.tolist()) == {1, 3}


def test_select_top_n_none_safe():
    assert tpo.select_top_n(None, 5) is None


def test_select_top_n_more_than_len():
    idx = tpo.select_top_n(np.array([0.1, 0.2]), 5)
    assert sorted(idx.tolist()) == [0, 1]


def test_select_top_n_ignores_nan():
    scores = np.array([0.9, 0.8, np.nan, 0.1])
    idx = tpo.select_top_n(scores, 2)
    assert set(idx.tolist()) == {0, 1}  # NaN (idx 2) is never a top pick


def test_select_top_n_all_nan_returns_empty():
    idx = tpo.select_top_n(np.array([np.nan, np.nan]), 2)
    assert idx.tolist() == []


# --------------------------------------------------------------------------- #
# end-to-end CLI
# --------------------------------------------------------------------------- #

def test_cli_writes_all_and_topn_pngs(tmp_path):
    vol = np.random.RandomState(0).rand(40, 30, 30).astype(np.float32)
    _write_mrc(tmp_path / "t.mrc", vol, angpix=13.48)
    _write_xml(tmp_path / "p.xml", [(10, 10, 10), (15, 15, 20), (20, 20, 30)], [0.2, 0.9, 0.5])
    out = tmp_path / "qc" / "TS_x"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--tomogram", str(tmp_path / "t.mrc"),
         "--picks", str(tmp_path / "p.xml"), "-o", str(out),
         "--n-slabs", "2", "--slab-thickness", "10", "--top-n", "1"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    names = [p.name for p in (tmp_path / "qc").glob("*.png")]
    assert any(n.endswith("_all.png") for n in names)
    assert any(n.endswith("_topN.png") for n in names)


def test_cli_no_score_skips_topn(tmp_path):
    _write_mrc(tmp_path / "t.mrc", np.zeros((20, 20, 20), np.float32), angpix=13.48)
    _write_star(tmp_path / "p.star", [(5, 5, 5), (6, 6, 10)], angpix=13.48, scores=None)
    out = tmp_path / "q" / "TS"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--tomogram", str(tmp_path / "t.mrc"),
         "--picks", str(tmp_path / "p.star"), "-o", str(out),
         "--n-slabs", "1", "--slab-thickness", "8"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    names = [p.name for p in (tmp_path / "q").glob("*.png")]
    assert any(n.endswith("_all.png") for n in names)
    assert not any(n.endswith("_topN.png") for n in names)
    assert "no score" in r.stderr.lower()


def test_cli_warns_on_missing_voxel_size(tmp_path):
    import mrcfile
    with mrcfile.new(str(tmp_path / "t.mrc"), overwrite=True) as m:
        m.set_data(np.zeros((20, 20, 20), np.float32))  # voxel_size left 0
    _write_xml(tmp_path / "p.xml", [(5, 5, 5)], [0.5])
    out = tmp_path / "v" / "TS"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--tomogram", str(tmp_path / "t.mrc"),
         "--picks", str(tmp_path / "p.xml"), "-o", str(out), "--n-slabs", "1"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "voxel_size" in r.stderr.lower()


def test_cli_reports_coverage(tmp_path):
    _write_mrc(tmp_path / "t.mrc", np.zeros((30, 20, 20), np.float32), angpix=13.48)
    _write_xml(tmp_path / "p.xml", [(5, 5, 5), (6, 6, 15)], [0.5, 0.6])
    out = tmp_path / "c" / "TS"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--tomogram", str(tmp_path / "t.mrc"),
         "--picks", str(tmp_path / "p.xml"), "-o", str(out), "--n-slabs", "2"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "total" in r.stderr.lower() and "shown" in r.stderr.lower()
