import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import tm_auto_mask as tam


def _solid_sphere(n, radius, value=1.0, center=None, background=0.0):
    """Cubic (n,n,n) volume: `background` everywhere, `background+value` inside the sphere."""
    r = tam._radius_grid((n, n, n), center=center)
    vol = np.full((n, n, n), background, dtype=np.float32)
    vol[r <= radius] = background + value
    return vol


def test_estimate_background_recovers_offset():
    vol = _solid_sphere(60, 15, value=1.0, background=0.5)
    assert tam.estimate_background(vol) == pytest.approx(0.5, abs=1e-6)


def test_radial_profile_monotonic_and_full():
    vol = _solid_sphere(80, 25)
    radii, cum = tam.radial_mass_profile(vol)
    assert np.all(np.diff(cum) >= -1e-9)          # non-decreasing
    assert cum[-1] == pytest.approx(1.0, abs=1e-6)  # all mass accounted for
    assert len(radii) == len(cum)


def test_enclosing_radius_matches_solid_sphere():
    vol = _solid_sphere(80, 25)
    radii, cum = tam.radial_mass_profile(vol)
    r95 = tam.enclosing_radius(radii, cum, 0.95)
    # uniform solid sphere: 95% of mass lies within 0.95**(1/3)=0.983 of R=25 (~24.58).
    # Tight tolerance guards against the ~1px lower-edge binning bias.
    assert r95 == pytest.approx(0.95 ** (1 / 3) * 25, abs=0.85)
    r_hi = tam.enclosing_radius(radii, cum, 0.999)
    assert r_hi >= r95                            # monotone in target


def test_center_of_mass_offset_centered_vs_shifted():
    centered = _solid_sphere(60, 12)
    assert tam.center_of_mass_offset(centered) == pytest.approx(0.0, abs=0.5)
    # sphere centered at x = 37.5 => +8 px from the geometric center (29.5)
    shifted = _solid_sphere(60, 12, center=(29.5, 29.5, 37.5))
    assert tam.center_of_mass_offset(shifted) == pytest.approx(8.0, abs=1.0)


def test_recommend_mask_solid_sphere():
    vol = _solid_sphere(80, 25)
    rec = tam.recommend_mask(vol, angpix=2.0, target=0.95)
    assert 22 <= rec["mask_radius_px"] <= 26
    assert rec["mask_sigma_px"] >= 2
    assert rec["box_dim"] == 80
    assert rec["box_fits"] is True
    assert rec["warnings"] == []
    assert 0.90 <= rec["enclosed_fraction"] <= 1.0
    assert rec["mask_radius_angstrom"] == pytest.approx(rec["mask_radius_px"] * 2.0)


def test_recommend_mask_omits_angstrom_without_angpix():
    rec = tam.recommend_mask(_solid_sphere(80, 25))
    assert "mask_radius_angstrom" not in rec


def test_recommend_mask_flags_small_box():
    # large soft edge (soft_frac=0.5) pushes radius+sigma past box/2; background stays clean
    vol = _solid_sphere(60, 25)
    rec = tam.recommend_mask(vol, soft_frac=0.5)
    assert rec["box_fits"] is False
    assert any("box" in w.lower() for w in rec["warnings"])


def test_recommend_mask_flags_offcenter():
    vol = _solid_sphere(60, 12, center=(29.5, 29.5, 37.5))
    rec = tam.recommend_mask(vol)
    assert any("center" in w.lower() for w in rec["warnings"])


def test_recommend_mask_rejects_noncubic():
    with pytest.raises(ValueError):
        tam.recommend_mask(np.zeros((10, 12, 14), dtype=np.float32))


def test_recommend_mask_handles_inverted_contrast():
    # dark-on-light: the structure is a negative-density sphere; orientation must recover it
    vol = _solid_sphere(80, 25, value=-1.0, background=0.0)
    rec = tam.recommend_mask(vol, target=0.95)
    assert 22 <= rec["mask_radius_px"] <= 26


def test_recommend_mask_target_one_returns_structure_radius():
    # target=1.0 must give the structure edge (~R=25), NOT the box corner (flat-tail bug)
    rec = tam.recommend_mask(_solid_sphere(80, 25), target=1.0)
    assert rec["mask_radius_px"] <= 28


def test_recommend_mask_subtracts_nonzero_background():
    # full pipeline (not just estimate_background) must recover R through a background offset
    rec = tam.recommend_mask(_solid_sphere(60, 15, value=1.0, background=0.5))
    assert 12 <= rec["mask_radius_px"] <= 18


def test_recommend_mask_raises_on_flat_or_empty_template():
    with pytest.raises(ValueError):
        tam.recommend_mask(np.zeros((40, 40, 40), dtype=np.float32))
    with pytest.raises(ValueError):
        tam.recommend_mask(np.full((40, 40, 40), 3.0, dtype=np.float32))


def test_returned_integers_never_exceed_box():
    # create_mask.py rejects radius+sigma >= box/2; the RETURNED integers must honor that
    for n, radius in [(60, 28), (80, 25), (60, 25), (100, 20)]:
        rec = tam.recommend_mask(_solid_sphere(n, radius), soft_frac=0.3)
        assert rec["mask_radius_px"] + rec["mask_sigma_px"] < n / 2


def test_noise_floor_thresholding_recovers_structure():
    # a compact sphere buried in a diffuse noise floor: default noise_k recovers the
    # sphere radius, while noise_k=0 lets the diffuse noise inflate the enclosing radius
    rng = np.random.default_rng(0)
    n, radius = 100, 20
    r = tam._radius_grid((n, n, n))
    vol = (r <= radius).astype(np.float64)
    vol = (vol + rng.normal(0.0, 0.05, size=vol.shape)).astype(np.float32)
    rec = tam.recommend_mask(vol, noise_k=3.0)
    assert 0.85 * radius <= rec["mask_radius_px"] <= 1.15 * radius
    rec0 = tam.recommend_mask(vol, noise_k=0.0)
    assert rec0["mask_radius_px"] > rec["mask_radius_px"] + 10


def test_noise_floor_inert_without_noise():
    # a clean sphere (zero-solvent, sigma=0) is unaffected by the noise floor
    vol = _solid_sphere(80, 25)
    assert tam.recommend_mask(vol, noise_k=3.0)["mask_radius_px"] == \
        tam.recommend_mask(vol, noise_k=0.0)["mask_radius_px"]


def test_cli_prints_and_writes_json(tmp_path):
    import mrcfile
    p = tmp_path / "t.mrc"
    with mrcfile.new(str(p)) as m:
        m.set_data(_solid_sphere(60, 18).astype(np.float32))
    script = Path(__file__).resolve().parents[1] / "scripts" / "tm_auto_mask.py"
    out = subprocess.run(
        [sys.executable, str(script), str(p), "--angpix", "2.0",
         "--json", str(tmp_path / "rec.json")],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    stdout_rec = json.loads(out.stdout)
    assert stdout_rec["box_dim"] == 60
    file_rec = json.loads((tmp_path / "rec.json").read_text())
    assert file_rec["mask_radius_px"] == stdout_rec["mask_radius_px"]
    assert "mask_radius_angstrom" in file_rec


def test_save_profile_png(tmp_path):
    pytest.importorskip("matplotlib")
    vol = _solid_sphere(60, 18)
    radii, cum = tam.radial_mass_profile(vol)
    out = tam.save_profile_png(radii, cum, 18, str(tmp_path / "p.png"))
    assert Path(out).exists() and Path(out).stat().st_size > 0
