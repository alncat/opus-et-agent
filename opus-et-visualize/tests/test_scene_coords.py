import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import starfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_artiax_scene as gs


def _make_star(tmp_path, n=3):
    df = pd.DataFrame({
        "rlnMicrographName": [f"TS_026.tomostar"] * n,
        "rlnCoordinateX": [10.0, 20.0, 30.0][:n],
        "rlnCoordinateY": [40.0, 50.0, 60.0][:n],
        "rlnCoordinateZ": [70.0, 80.0, 90.0][:n],
        "rlnAngleRot": [0.0, 90.0, 45.0][:n],
        "rlnAngleTilt": [0.0, 30.0, 60.0][:n],
        "rlnAnglePsi": [0.0, 10.0, 20.0][:n],
    })
    p = tmp_path / "picks.star"
    starfile.write(df, p, overwrite=True)
    return p, df


def test_read_particles_returns_block_with_coords(tmp_path):
    p, _ = _make_star(tmp_path)
    df = gs.read_particles(p)
    assert "rlnCoordinateX" in df.columns
    assert len(df) == 3


def test_reconcile_coords_scales_by_pixel_ratio(tmp_path):
    p, _ = _make_star(tmp_path)
    df = gs.reconcile_coords(gs.read_particles(p), coords_angpix=1.0, tomo_angpix=2.0)
    # coord 10 px @1.0 A -> 5 voxels @2.0 A ; 10 A physical
    assert df["voxelX"].iloc[0] == pytest.approx(5.0)
    assert df["angstromX"].iloc[0] == pytest.approx(10.0)


def test_attach_labels_length_mismatch_raises(tmp_path):
    p, _ = _make_star(tmp_path)
    df = gs.read_particles(p)
    with pytest.raises(ValueError):
        gs.attach_labels(df, np.array([0, 1]))  # only 2 for 3 particles


def test_write_relion_star_filters_by_state(tmp_path):
    p, _ = _make_star(tmp_path)
    df = gs.attach_labels(gs.read_particles(p), np.array([0, 1, 0]))
    out = tmp_path / "state0.star"
    gs.write_relion_star(df, out, state=0)
    back = starfile.read(out)
    back = back if isinstance(back, pd.DataFrame) else list(back.values())[0]
    assert len(back) == 2  # two particles with state 0
