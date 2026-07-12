import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compare_to_template as ct


def _sphere(box, radius, center=None, val=1.0):
    c = center if center is not None else (box - 1) / 2.0
    zz, yy, xx = np.ogrid[:box, :box, :box]
    r = np.sqrt((zz - c) ** 2 + (yy - c) ** 2 + (xx - c) ** 2)
    return np.where(r <= radius, val, 0.0).astype(np.float32)


# ---------------------------------------------------------------- masked_cc
def test_masked_cc_identical_is_one():
    a = np.random.RandomState(0).rand(8, 8, 8).astype(np.float32)
    assert ct.masked_cc(a, a) == pytest.approx(1.0, abs=1e-6)


def test_masked_cc_anticorrelated_is_minus_one():
    a = np.random.RandomState(1).rand(8, 8, 8).astype(np.float32)
    assert ct.masked_cc(a, -a) == pytest.approx(-1.0, abs=1e-6)


def test_masked_cc_ignores_voxels_outside_mask():
    rng = np.random.RandomState(2)
    box = 8
    a = rng.rand(box, box, box).astype(np.float32)
    b = a.copy()
    mask = np.zeros((box, box, box), np.float32)
    mask[2:6, 2:6, 2:6] = 1.0
    # corrupt everything OUTSIDE the mask; within-mask correlation must stay 1
    outside = mask == 0
    b[outside] = rng.rand(int(outside.sum())).astype(np.float32) * 100
    assert ct.masked_cc(a, b, mask) == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------- soft_sphere_mask
def test_soft_sphere_mask_center_one_corner_zero():
    m = ct.soft_sphere_mask(16, radius=4, edge=2)
    assert m.shape == (16, 16, 16)
    assert m[8, 8, 8] == pytest.approx(1.0)
    assert m[0, 0, 0] == pytest.approx(0.0)
    assert m.max() <= 1.0 and m.min() >= 0.0


def test_soft_sphere_mask_has_soft_edge():
    m = ct.soft_sphere_mask(32, radius=8, edge=4)
    # a shell just past the hard radius must be partially valued (not binary)
    vals = m[(m > 0) & (m < 1)]
    assert vals.size > 0


# --------------------------------------------------------- fourier_resize
def test_fourier_resize_upsample_shape():
    v = _sphere(32, 6)
    assert ct.fourier_resize(v, 64).shape == (64, 64, 64)


def test_fourier_resize_downsample_shape():
    v = _sphere(92, 20)
    assert ct.fourier_resize(v, 32).shape == (32, 32, 32)


def test_fourier_resize_preserves_constant():
    v = np.full((32, 32, 32), 3.5, np.float32)
    up = ct.fourier_resize(v, 128)
    down = ct.fourier_resize(v, 20)
    assert up.mean() == pytest.approx(3.5, abs=1e-3)
    assert down.mean() == pytest.approx(3.5, abs=1e-3)
    # interior stays flat (ignore FFT ringing at the very border)
    assert up[32:96, 32:96, 32:96].std() < 1e-2


# --------------------------------------------------------- resample_to_apix
def test_resample_to_apix_halving_doubles_box():
    v = _sphere(32, 6)
    out, apix = ct.resample_to_apix(v, apix_in=2.0, apix_out=1.0)
    assert out.shape == (64, 64, 64)
    assert apix == pytest.approx(1.0)


# --------------------------------------------------------------- center_fit
def test_center_fit_crop_and_pad_shapes():
    v = _sphere(64, 10)
    assert ct.center_fit(v, 32).shape == (32, 32, 32)
    assert ct.center_fit(v, 100).shape == (100, 100, 100)


def test_center_fit_keeps_blob_centered_on_pad():
    v = _sphere(32, 5)  # centered blob (uniform value -> use center-of-mass, not argmax)
    out = ct.center_fit(v, 64)
    total = out.sum()
    com = [float((out.sum(axis=tuple(j for j in range(3) if j != ax)) *
                  np.arange(64)).sum() / total) for ax in range(3)]
    assert all(abs(c - 31.5) <= 0.5 for c in com)  # still at the new center


# ------------------------------------------------------------------ lowpass
def test_lowpass_preserves_mean():
    rng = np.random.RandomState(3)
    v = rng.rand(24, 24, 24).astype(np.float32)
    lp = ct.lowpass(v, apix=3.0, res_A=15.0)
    assert lp.mean() == pytest.approx(v.mean(), abs=1e-4)


def test_lowpass_reduces_high_frequency_variance():
    # a high-frequency checkerboard should lose most of its variance
    idx = np.indices((24, 24, 24)).sum(axis=0)
    checker = ((idx % 2) * 2 - 1).astype(np.float32)  # +/-1 alternating
    lp = ct.lowpass(checker, apix=3.0, res_A=20.0)
    assert lp.var() < 0.05 * checker.var()


# --------------------------------------------------------- prepare_reference
def test_prepare_reference_matches_native_grid_blob():
    # a coarse template blob, resampled onto a finer/bigger grid, should
    # correlate strongly with a blob built natively on that grid
    tmpl = _sphere(32, radius=6)            # apix 4 -> extent 128 A, blob radius 24 A
    ref = ct.prepare_reference(tmpl, tmpl_apix=4.0, dst_apix=2.0, dst_box=64)
    assert ref.shape == (64, 64, 64)
    native = _sphere(64, radius=12)         # same 24 A radius at apix 2
    assert ct.masked_cc(ref, native) > 0.9


# ------------------------------------------------------- set-level helpers
def test_consensus_map_is_mean():
    maps = [np.full((6, 6, 6), float(i), np.float32) for i in range(5)]
    assert ct.consensus_map(maps) == pytest.approx(np.full((6, 6, 6), 2.0))


def test_compare_maps_scores_reference_copy_highest():
    ref = _sphere(16, 5)
    maps = [_sphere(16, 5), _sphere(16, 5) + 0.3 * np.random.RandomState(4).rand(16, 16, 16).astype(np.float32),
            np.random.RandomState(5).rand(16, 16, 16).astype(np.float32)]
    ccs = ct.compare_maps(maps, ref)
    assert np.argmax(ccs) == 0
    assert ccs[0] == pytest.approx(1.0, abs=1e-6)
