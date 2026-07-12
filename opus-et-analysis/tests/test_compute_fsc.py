import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compute_fsc as cf


# ------------------------------------------------------------------- fsc_curve
def test_fsc_curve_identical_volumes_is_one():
    rng = np.random.RandomState(0)
    v = rng.rand(16, 16, 16).astype(np.float32)
    freq, fsc = cf.fsc_curve(v, v)
    valid = ~np.isnan(fsc)
    assert valid.sum() > 0
    assert fsc[valid] == pytest.approx(1.0, abs=1e-6)


def test_fsc_curve_independent_noise_is_near_zero():
    rng = np.random.RandomState(1)
    a = rng.rand(24, 24, 24).astype(np.float32)
    b = rng.rand(24, 24, 24).astype(np.float32)
    freq, fsc = cf.fsc_curve(a, b)
    valid = ~np.isnan(fsc)
    # ignore the DC shell (index 0): unrelated positive-mean noise is not
    # expected to be uncorrelated there
    assert np.abs(fsc[valid][1:]).mean() < 0.25


def test_fsc_curve_shape_matches_frequency_axis():
    v = np.random.RandomState(2).rand(20, 20, 20).astype(np.float32)
    freq, fsc = cf.fsc_curve(v, v)
    assert freq.shape == fsc.shape
    assert freq[0] == pytest.approx(0.0)
    assert np.all(np.diff(freq) > 0)


def test_fsc_curve_mask_restricts_correlation_region():
    box = 16
    rng = np.random.RandomState(3)
    a = rng.rand(box, box, box).astype(np.float32)
    b = a.copy()
    mask = np.zeros((box, box, box), np.float32)
    mask[4:12, 4:12, 4:12] = 1.0
    outside = mask == 0
    b[outside] = rng.rand(int(outside.sum())).astype(np.float32) * 100

    freq, fsc_masked = cf.fsc_curve(a, b, mask=mask)
    _, fsc_unmasked = cf.fsc_curve(a, b, mask=None)

    valid = ~np.isnan(fsc_masked)
    assert fsc_masked[valid] == pytest.approx(1.0, abs=1e-3)
    valid_u = ~np.isnan(fsc_unmasked)
    assert np.mean(fsc_unmasked[valid_u]) < np.mean(fsc_masked[valid])


# ---------------------------------------------------------------- resolution_at
def test_resolution_at_picks_first_crossing_not_later_rise():
    freq = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    # dips below 0.143 between 0.2 and 0.3, then rises again (mask artifact)
    fsc = np.array([1.0, 0.9, 0.5, 0.1, 0.05, 0.5])
    apix = 1.0
    res = cf.resolution_at(freq, fsc, 0.143, apix)

    t = (0.143 - 0.5) / (0.1 - 0.5)
    f_cross = 0.2 + t * (0.3 - 0.2)
    expected = apix / f_cross
    assert res == pytest.approx(expected, rel=1e-6)


def test_resolution_at_interpolates_linearly():
    freq = np.array([0.0, 0.1, 0.2])
    fsc = np.array([1.0, 0.6, 0.2])
    apix = 2.0
    res = cf.resolution_at(freq, fsc, 0.4, apix)
    # crossing 0.4 between (0.1, 0.6) and (0.2, 0.2): t=(0.4-0.6)/(0.2-0.6)=0.5
    f_cross = 0.1 + 0.5 * 0.1
    assert res == pytest.approx(apix / f_cross, rel=1e-6)


def test_resolution_at_never_crossing_returns_finite_or_inf():
    freq = np.array([0.0, 0.1, 0.2])
    fsc = np.array([1.0, 0.9, 0.8])  # never drops below 0.143
    res = cf.resolution_at(freq, fsc, 0.143, apix=1.0)
    assert res == pytest.approx(1.0 / 0.2, rel=1e-6) or np.isinf(res)


# ------------------------------------------------------------- phase_randomize
def _rfft_radii(N):
    fz = np.fft.fftfreq(N)[:, None, None]
    fy = np.fft.fftfreq(N)[None, :, None]
    fx = np.fft.rfftfreq(N)[None, None, :]
    return np.sqrt(fz ** 2 + fy ** 2 + fx ** 2)


def test_phase_randomize_is_real_and_same_shape():
    v = np.random.RandomState(10).rand(16, 16, 16)
    out = cf.phase_randomize(v, 0.2, seed=0)
    assert out.shape == v.shape
    assert np.isrealobj(out)


def test_phase_randomize_preserves_low_frequency_coefficients():
    N = 16
    v = np.random.RandomState(11).rand(N, N, N)
    rand_freq = 0.2
    out = cf.phase_randomize(v, rand_freq, seed=0)
    F_in = np.fft.rfftn(v)
    F_out = np.fft.rfftn(out)
    low = _rfft_radii(N) <= rand_freq
    # shells at/below the cutoff keep their exact complex coefficients
    assert np.allclose(F_out[low], F_in[low], atol=1e-8)


def test_phase_randomize_preserves_amplitudes_but_scrambles_phases():
    N = 16
    v = np.random.RandomState(12).rand(N, N, N)
    rand_freq = 0.2
    out = cf.phase_randomize(v, rand_freq, seed=3)
    F_in = np.fft.rfftn(v)
    F_out = np.fft.rfftn(out)
    high = _rfft_radii(N) > rand_freq
    # the Hermitian construction preserves every amplitude exactly beyond the
    # cutoff (only phases scramble), and the volume actually changed
    assert np.allclose(np.abs(F_out[high]), np.abs(F_in[high]), atol=1e-6)
    assert not np.allclose(out, v)


def test_phase_randomize_preserves_total_power():
    N = 16
    v = np.random.RandomState(14).rand(N, N, N)
    out = cf.phase_randomize(v, 0.2, seed=1)
    # Parseval: a Hermitian phase scramble conserves total power exactly
    assert np.sum(out ** 2) == pytest.approx(np.sum(v ** 2), rel=1e-9)


def test_phase_randomize_is_deterministic():
    v = np.random.RandomState(13).rand(12, 12, 12)
    a = cf.phase_randomize(v, 0.15, seed=7)
    b = cf.phase_randomize(v, 0.15, seed=7)
    assert np.array_equal(a, b)


# --------------------------------------------------------------- fsc_corrected
def _soft_sphere(box, radius, edge=3.0):
    c = box // 2
    zz, yy, xx = np.indices((box, box, box))
    r = np.sqrt((zz - c) ** 2 + (yy - c) ** 2 + (xx - c) ** 2)
    return np.clip((radius - r) / edge, 0.0, 1.0)


def test_fsc_corrected_returns_expected_keys():
    v = np.random.RandomState(20).rand(16, 16, 16)
    mask = np.ones_like(v)
    out = cf.fsc_corrected(v, v, mask, apix=4.0)
    for key in ("freq", "fsc_masked", "fsc_random", "fsc_corrected", "rand_freq"):
        assert key in out
    assert out["fsc_corrected"].shape == out["freq"].shape


def test_fsc_corrected_identical_volumes_stays_high():
    v = np.random.RandomState(21).rand(20, 20, 20)
    mask = _soft_sphere(20, 8.0)
    out = cf.fsc_corrected(v * mask, v * mask, mask, apix=4.0)
    corr = out["fsc_corrected"]
    valid = ~np.isnan(corr)
    # identical signal: differently-seeded randomization decorrelates the noise
    # floor, so the correction leaves the real correlation ~1
    assert np.mean(corr[valid][1:]) > 0.8


def test_fsc_corrected_suppresses_mask_induced_correlation():
    box = 24
    rng = np.random.RandomState(22)
    a = rng.rand(box, box, box)
    b = rng.rand(box, box, box)  # independent noise: true FSC ~ 0
    mask = _soft_sphere(box, box * 0.4)

    out = cf.fsc_corrected(a, b, mask, apix=4.0)
    freq, masked, corr, rf = (out["freq"], out["fsc_masked"],
                              out["fsc_corrected"], out["rand_freq"])
    beyond = (freq > rf) & ~np.isnan(masked) & ~np.isnan(corr)
    # the shared mask correlates the two independent maps at high frequency;
    # the correction removes it, pulling the corrected curve toward zero
    assert np.mean(masked[beyond]) > 0.0
    assert np.mean(corr[beyond]) < np.mean(masked[beyond])


def test_fsc_corrected_respects_manual_cutoff():
    v = np.random.RandomState(23).rand(16, 16, 16)
    mask = np.ones_like(v)
    apix, rand_res = 4.0, 12.0
    out = cf.fsc_corrected(v, v, mask, apix=apix, rand_res=rand_res)
    assert out["rand_freq"] == pytest.approx(apix / rand_res, rel=1e-6)


def test_fsc_corrected_leaves_low_res_shells_unchanged():
    box = 20
    rng = np.random.RandomState(24)
    a = rng.rand(box, box, box)
    b = a + 0.05 * rng.rand(box, box, box)  # correlated at low res
    mask = _soft_sphere(box, box * 0.4)
    out = cf.fsc_corrected(a, b, mask, apix=4.0, rand_res=8.0)
    freq, masked, corr = out["freq"], out["fsc_masked"], out["fsc_corrected"]
    at_or_below = freq <= out["rand_freq"]
    # below the cutoff the corrected curve equals the raw masked curve exactly
    assert np.allclose(corr[at_or_below], masked[at_or_below], equal_nan=True)
