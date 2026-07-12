import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import particle_gallery as pg

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "particle_gallery.py"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def _write_star(path, xyz, angpix, tomo="TS_028"):
    import starfile
    d = {
        "rlnCoordinateX": [c[0] for c in xyz],
        "rlnCoordinateY": [c[1] for c in xyz],
        "rlnCoordinateZ": [c[2] for c in xyz],
        "rlnMicrographName": [f"{tomo}.tomostar"] * len(xyz),
        "rlnPixelSize": [angpix] * len(xyz),
    }
    starfile.write(pd.DataFrame(d), str(path), overwrite=True)


def _write_mrc(path, vol, angpix):
    import mrcfile
    with mrcfile.new(str(path), overwrite=True) as m:
        m.set_data(np.asarray(vol, dtype=np.float32))
        m.voxel_size = float(angpix)


def _coded_vol(nz, ny, nx):
    """vol[z,y,x] = 1000*z + 10*y + x  (every voxel encodes its own index)."""
    z, y, x = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    return (1000 * z + 10 * y + x).astype(np.float32)


# --------------------------------------------------------------------------- #
# in_bounds
# --------------------------------------------------------------------------- #

def test_in_bounds_center_true():
    assert pg.in_bounds((30, 30, 20), half=8, vol_shape=(40, 60, 60))


@pytest.mark.parametrize("coord", [(4, 30, 20), (56, 30, 20), (30, 4, 20), (30, 56, 20)])
def test_in_bounds_xy_edge_false(coord):
    assert not pg.in_bounds(coord, half=8, vol_shape=(40, 60, 60))


def test_in_bounds_z_out_of_range_false():
    assert not pg.in_bounds((30, 30, 39), half=8, vol_shape=(40, 60, 60), zthick=5)
    assert not pg.in_bounds((30, 30, 0), half=8, vol_shape=(40, 60, 60), zthick=5)


# --------------------------------------------------------------------------- #
# select_gallery_picks
# --------------------------------------------------------------------------- #

def test_select_only_in_bounds():
    coords = np.array([[30, 30, 20],   # in
                       [2, 2, 20],      # xy edge -> out
                       [40, 40, 20],    # in
                       [30, 30, 39]])   # z edge with zthick default 1 -> in (z<=nz-1)
    idx = pg.select_gallery_picks(coords, half=8, vol_shape=(40, 60, 60), n=10)
    assert 1 not in idx.tolist()
    assert set(idx.tolist()) <= {0, 2, 3}


def test_select_caps_at_n_and_spreads():
    coords = np.array([[20 + i, 30, 20] for i in range(20)], float)  # all in-bounds
    idx = pg.select_gallery_picks(coords, half=8, vol_shape=(40, 60, 80), n=5)
    assert len(idx) == 5
    assert idx[0] == 0 and idx[-1] == 19          # spans the full list
    assert list(idx) == sorted(idx)               # order preserved


def test_select_empty_when_none_fit():
    coords = np.array([[1, 1, 1]], float)
    idx = pg.select_gallery_picks(coords, half=8, vol_shape=(40, 60, 60), n=5)
    assert idx.tolist() == []


# --------------------------------------------------------------------------- #
# crop_at_pick
# --------------------------------------------------------------------------- #

def test_crop_shape_and_center_single_plane():
    vol = _coded_vol(40, 60, 60)
    crop = pg.crop_at_pick(vol, (30, 25, 18), half=6, zthick=1)
    assert crop.shape == (12, 12)
    # center pixel [half, half] is the pick voxel: 1000*z + 10*y + x
    assert crop[6, 6] == pytest.approx(1000 * 18 + 10 * 25 + 30)


def test_crop_zthick_mean_matches_center_plane():
    vol = _coded_vol(40, 60, 60)
    # mean over z-1,z,z+1 of 1000*z(+...) == 1000*z0(+...) since z0 is the mean of the band
    crop = pg.crop_at_pick(vol, (30, 25, 18), half=4, zthick=3)
    assert crop.shape == (8, 8)
    assert crop[4, 4] == pytest.approx(1000 * 18 + 10 * 25 + 30)


# --------------------------------------------------------------------------- #
# marker styles (pure)
# --------------------------------------------------------------------------- #

def test_marker_kw_styles():
    assert pg._marker_kw("ring", "gold")["facecolor"] == "none"
    assert pg._marker_kw("solid", "gold")["alpha"] == 1.0
    assert pg._marker_kw("transparent", "gold")["alpha"] < 1.0


def test_marker_kw_bad_style_raises():
    with pytest.raises(ValueError):
        pg._marker_kw("blob", "gold")


# --------------------------------------------------------------------------- #
# BILD markers (pure)
# --------------------------------------------------------------------------- #

def test_write_bild_places_spheres_in_angstroms(tmp_path):
    out = tmp_path / "m.bild"
    pg.write_bild([[10, 20, 30], [1, 2, 3]], tomo_angpix=26.96, out_path=out,
                  radius=70.0, color="gold")
    lines = out.read_text().splitlines()
    assert lines[0] == ".color gold"
    # px * voxel size -> Angstroms: 10*26.96 = 269.6
    assert lines[1] == ".sphere 269.60 539.20 808.80 70.0"
    assert len([ln for ln in lines if ln.startswith(".sphere")]) == 2


def test_cli_bild_written(tmp_path):
    _write_mrc(tmp_path / "t.mrc", _coded_vol(40, 60, 60), angpix=13.48)
    _write_star(tmp_path / "p.star", [(30, 30, 20), (25, 35, 18)], angpix=13.48)
    out = tmp_path / "g.png"
    bild = tmp_path / "m.bild"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--tomogram", str(tmp_path / "t.mrc"),
         "--picks", str(tmp_path / "p.star"), "-o", str(out),
         "--half", "8", "--bild", str(bild)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert bild.exists() and ".sphere" in bild.read_text()
    assert "bild" in r.stderr.lower()


# --------------------------------------------------------------------------- #
# end-to-end CLI
# --------------------------------------------------------------------------- #

def test_cli_writes_gallery(tmp_path):
    _write_mrc(tmp_path / "t.mrc", _coded_vol(40, 60, 60), angpix=13.48)
    _write_star(tmp_path / "p.star",
                [(30, 30, 20), (25, 35, 18), (40, 25, 22), (20, 40, 16)], angpix=13.48)
    out = tmp_path / "g" / "fas_gallery.png"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--tomogram", str(tmp_path / "t.mrc"),
         "--picks", str(tmp_path / "p.star"), "-o", str(out),
         "--half", "8", "--num", "4", "--cols", "2", "--style", "ring",
         "--species", "FAS"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert out.exists()
    assert "gallery" in r.stderr.lower() and "style=ring" in r.stderr


@pytest.mark.parametrize("style", ["ring", "transparent", "solid"])
def test_cli_all_styles(tmp_path, style):
    _write_mrc(tmp_path / "t.mrc", _coded_vol(30, 50, 50), angpix=13.48)
    _write_star(tmp_path / "p.star", [(25, 25, 15), (20, 30, 12)], angpix=13.48)
    out = tmp_path / f"{style}.png"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--tomogram", str(tmp_path / "t.mrc"),
         "--picks", str(tmp_path / "p.star"), "-o", str(out),
         "--half", "8", "--num", "2", "--style", style],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert out.exists()


def test_cli_errors_when_no_pick_fits(tmp_path):
    _write_mrc(tmp_path / "t.mrc", _coded_vol(20, 20, 20), angpix=13.48)
    _write_star(tmp_path / "p.star", [(2, 2, 2)], angpix=13.48)   # too close to the edge
    out = tmp_path / "none.png"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--tomogram", str(tmp_path / "t.mrc"),
         "--picks", str(tmp_path / "p.star"), "-o", str(out), "--half", "8"],
        capture_output=True, text=True)
    assert r.returncode != 0
    assert "in-bounds" in r.stderr.lower()
