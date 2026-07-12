import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import slice_preview as sp


def test_central_slices_picks_center_planes():
    # volume shaped (Z=2, Y=3, X=4)
    vol = np.arange(2 * 3 * 4).reshape(2, 3, 4).astype(float)
    xy, xz = sp.central_slices(vol)
    assert xy.shape == (3, 4)            # central-Z plane is Y×X
    assert xz.shape == (2, 4)            # central-Y plane is Z×X
    assert np.array_equal(xy, vol[1])    # central Z index = 2 // 2 = 1
    assert np.array_equal(xz, vol[:, 1, :])  # central Y index = 3 // 2 = 1


def test_central_slices_rejects_non_3d():
    with pytest.raises(ValueError):
        sp.central_slices(np.zeros((4, 4)))


def test_normalize_clips_to_unit_range():
    n = sp.normalize(np.array([0.0, 50.0, 100.0]), low=0.0, high=100.0)
    assert n.min() == pytest.approx(0.0)
    assert n.max() == pytest.approx(1.0)


def test_normalize_handles_flat_image():
    # all-equal input must not divide by zero
    n = sp.normalize(np.full((4, 4), 7.0))
    assert np.all(np.isfinite(n))


def test_preview_mrc_writes_two_pngs(tmp_path):
    mrcfile = pytest.importorskip("mrcfile")
    pytest.importorskip("matplotlib")
    p = tmp_path / "vol.mrc"
    with mrcfile.new(str(p)) as m:
        m.set_data(np.random.RandomState(0).rand(6, 8, 10).astype(np.float32))
    outs = sp.preview_mrc(str(p), str(tmp_path / "prev"))
    assert len(outs) == 2
    assert all(Path(o).exists() and Path(o).stat().st_size > 0 for o in outs)
