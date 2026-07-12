import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_mask_from_map as gm


def _sphere(box, radius, center=None, val=1.0):
    c = center if center is not None else (box - 1) / 2.0
    zz, yy, xx = np.ogrid[:box, :box, :box]
    r = np.sqrt((zz - c) ** 2 + (yy - c) ** 2 + (xx - c) ** 2)
    return np.where(r <= radius, val, 0.0).astype(np.float32)


# --------------------------------------------------------------- molecule_mask
def test_molecule_mask_covers_sphere_with_soft_edge():
    box = 32
    v = _sphere(box, 10, val=5.0)
    mask = gm.molecule_mask(v, dilate_px=0, soft_edge_px=3)
    c = box // 2
    assert mask.shape == v.shape
    assert mask[c, c, c] == pytest.approx(1.0, abs=1e-6)
    # a corner far from the sphere must be fully excluded
    assert mask[0, 0, 0] == pytest.approx(0.0, abs=1e-6)
    # the soft-edge ramp shell must contain strictly-fractional values
    vals = mask[(mask > 0) & (mask < 1)]
    assert vals.size > 0
    assert mask.min() >= 0.0 and mask.max() <= 1.0


def test_molecule_mask_keeps_only_largest_component():
    box = 32
    big = _sphere(box, 6, center=10.0, val=5.0)
    tiny = _sphere(box, 1, center=25.0, val=5.0)
    v = np.maximum(big, tiny)
    mask = gm.molecule_mask(v, dilate_px=0, soft_edge_px=0)
    assert mask[10, 10, 10] == pytest.approx(1.0)
    assert mask[25, 25, 25] == pytest.approx(0.0)


def test_molecule_mask_constant_volume_is_empty():
    v = np.full((16, 16, 16), 5.0, np.float32)
    mask = gm.molecule_mask(v)
    assert mask.shape == v.shape
    assert mask.sum() == pytest.approx(0.0)


def test_molecule_mask_near_zero_noise_handled_gracefully():
    rng = np.random.RandomState(0)
    v = ((rng.rand(16, 16, 16).astype(np.float32) - 0.5) * 1e-6)
    mask = gm.molecule_mask(v)  # must not raise
    assert mask.shape == v.shape
    assert mask.min() >= 0.0 and mask.max() <= 1.0


def test_molecule_mask_explicit_threshold_overrides_sigma():
    box = 16
    v = _sphere(box, 4, val=2.0)
    # a very high explicit threshold should exclude everything
    mask = gm.molecule_mask(v, threshold=10.0, dilate_px=0, soft_edge_px=0)
    assert mask.sum() == pytest.approx(0.0)


def test_molecule_mask_dilate_grows_mask():
    box = 32
    v = _sphere(box, 6, val=5.0)
    small = gm.molecule_mask(v, dilate_px=0, soft_edge_px=0)
    big = gm.molecule_mask(v, dilate_px=3, soft_edge_px=0)
    assert big.sum() > small.sum()
    # dilation only grows: every voxel in `small` must still be set in `big`
    assert np.all(big[small.astype(bool)] == 1.0)


# ----------------------------------------------------------------- overlay QC
def test_overlay_slices_returns_three_central_panels():
    box = 24
    v = _sphere(box, 8, val=5.0)
    mask = gm.molecule_mask(v, dilate_px=0, soft_edge_px=2)
    panels = gm.overlay_slices(v, mask)
    assert len(panels) == 3                        # XY, XZ, YZ central slices
    c = box // 2
    dens_xy, mask_xy = panels[0]
    assert dens_xy.shape == (box, box) and mask_xy.shape == (box, box)
    # the XY panel is the central-Z slice of each volume
    assert np.allclose(dens_xy, v[c])
    assert np.allclose(mask_xy, mask[c])


def test_overlay_slices_mismatched_shapes_raise():
    v = _sphere(16, 4)
    with pytest.raises(ValueError):
        gm.overlay_slices(v, np.zeros((8, 8, 8), np.float32))


def test_write_overlay_png_creates_file(tmp_path):
    box = 20
    v = _sphere(box, 7, val=5.0)
    mask = gm.molecule_mask(v, dilate_px=1, soft_edge_px=2)
    out = tmp_path / "mask_qc.png"
    gm.write_overlay_png(v, mask, str(out), apix=4.2)
    assert out.exists() and out.stat().st_size > 0
