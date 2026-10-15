"""Phase 3c AreTomo alignment QC on synthetic tilt series.

On the 20260907 data AreTomo reported no residual (global alignment only) and
its shift track jumped by up to 860 px between neighbouring tilts, which were
nevertheless aligned correctly. The check must pass real stage jumps that the
alignment corrects, tolerate a specimen off the central plane, and name a tilt
the alignment got wrong.
"""
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from scipy import ndimage

mrcfile = pytest.importorskip('mrcfile')

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'alignment_qc.py'
spec = importlib.util.spec_from_file_location('alignment_qc', SCRIPT)
qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qc)

PX = 9.5                                   # A; block 1, so the working pixel too
SHAPE = (200, 280)
TILTS = np.arange(-45.0, 45.1, 3.0)


def specimen(height_px=0.0, seed=0, x_range=(-220, 220)):
    rng = np.random.default_rng(seed)
    n = 700
    return np.stack([rng.uniform(*x_range, n), rng.uniform(-130, 130, n),
                     height_px + rng.uniform(-15, 15, n), rng.uniform(0.5, 2.0, n)], axis=1)


def project(points, tilt, shift=(0.0, 0.0), seed=0, noise=0.15):
    """Tilt axis along y. `shift` (x, y) moves the content, as a stage error would."""
    t = math.radians(tilt)
    x = points[:, 0] * math.cos(t) + points[:, 2] * math.sin(t) + shift[0]
    y = points[:, 1] + shift[1]
    img = np.zeros(SHAPE, np.float64)
    cy, cx = (SHAPE[0] - 1) / 2.0, (SHAPE[1] - 1) / 2.0
    for xi, yi, a in zip(x + cx, y + cy, points[:, 3]):
        i, j = int(math.floor(yi)), int(math.floor(xi))
        fy, fx = yi - i, xi - j
        for di, dj, w in ((0, 0, (1 - fy) * (1 - fx)), (0, 1, (1 - fy) * fx),
                          (1, 0, fy * (1 - fx)), (1, 1, fy * fx)):
            if 0 <= i + di < SHAPE[0] and 0 <= j + dj < SHAPE[1]:
                img[i + di, j + dj] += a * w
    img = ndimage.gaussian_filter(img, 1.5)
    rng = np.random.default_rng(seed)
    return img + rng.normal(0, noise * img.std(), SHAPE)


def series(height_px=0.0, stage=None, xf_error=None, blank=(), x_range=(-220, 220),
           noise=lambda t: 0.15):
    """Raw sections and an xf that undoes each tilt's stage shift.

    stage: {tilt index: (dx, dy)} real shifts of the content in the raw image;
    xf_error: {tilt index: (ex, ey)} mistakes in the alignment for that tilt."""
    pts = specimen(height_px, x_range=x_range)
    stage, xf_error = stage or {}, xf_error or {}
    sections, xf = [], []
    for k, t in enumerate(TILTS):
        sx, sy = stage.get(k, (0.0, 0.0))
        ex, ey = xf_error.get(k, (0.0, 0.0))
        img = project(pts, t, (sx, sy), seed=k, noise=noise(t))
        if k in blank:
            img = np.random.default_rng(100 + k).normal(0, img.std(), SHAPE)
        sections.append(img.astype(np.float32))
        xf.append([1.0, 0.0, 0.0, 1.0, -sx + ex, -sy + ey])
    return sections, np.array(xf)


def analyse(**kw):
    sections, xf = series(**kw)
    return qc.analyse(sections, xf, TILTS, PX)


def test_apply_xf_follows_the_newstack_convention():
    img = np.zeros((101, 101))
    img[50, 70] = 1.0                                  # (x, y) = (+20, 0) from the centre
    shifted = qc.apply_xf(img, [1, 0, 0, 1, 5, -3], order=1)
    assert np.unravel_index(np.argmax(shifted), img.shape) == (47, 75)
    # A = [[0, -1], [1, 0]] maps (x, y) -> (-y, x): (+20, 0) -> (0, +20).
    rotated = qc.apply_xf(img, [0, -1, 1, 0, 0, 0], order=1)
    assert np.unravel_index(np.argmax(rotated), img.shape) == (70, 50)


def test_local_error_blames_the_bad_tilt_not_its_neighbours():
    t = np.arange(-21.0, 22.0, 3.0)
    assert np.allclose(qc.local_errors(0.2 * t + 1, t), 0)
    for bad in ([7], [0], [len(t) - 1], [5, 6]):          # middle, both ends, a pair
        v = 0.2 * t + 1
        v[bad] += 6
        err = np.abs(qc.local_errors(v, t))
        assert np.allclose(err[bad], 6, atol=0.3), (bad, err)
        assert np.delete(err, bad).max() < 0.5, (bad, err)


def test_correct_alignment_passes_and_recovers_the_specimen_height():
    r = analyse(height_px=25.0)
    assert r['pass'], r['problems']
    assert r['max_local_error_A'] < 5
    # The points that fall inside the window average ~22 px high, not 25.
    assert abs(abs(r['specimen_height_A']) - 25 * PX) < 0.2 * 25 * PX
    # The height is geometry, not error: nothing of it is left as drift.
    assert r['max_drift_A'] < 10 and not any('drift' in w for w in r['warnings'])


def test_content_off_the_axis_is_not_mistaken_for_drift():
    # Mass on one side of the tilt axis foreshortens by cos(tilt); without the
    # cosine stretch that reads as a growing shift.
    r = analyse(x_range=(20, 220))
    assert r['pass'], r['problems']
    assert r['max_drift_A'] < 10, r['max_drift_A']


def test_large_stage_jumps_that_the_xf_corrects_pass():
    # The 20260907 pattern: neighbouring tilts 60+ px apart, correctly aligned.
    r = analyse(stage={10: (60.0, -8.0), 11: (-45.0, 20.0), 25: (70.0, 0.0)})
    assert r['pass'], r['problems']
    assert r['max_local_error_A'] < 5


def test_one_misaligned_tilt_fails_and_is_named():
    k = int(np.flatnonzero(TILTS == 30.0)[0])
    r = analyse(stage={k: (40.0, 0.0)}, xf_error={k: (6.0, -2.0)})      # ~60 A wrong
    assert not r['pass']
    assert any('tilt +30.0' in p for p in r['problems']), r['problems']
    others = [row['local_error_A'] for row in r['tilts'] if row['tilt'] != 30.0]
    assert max(others) < qc.LOCAL_FAIL_A


def test_a_tilt_the_alignment_lost_fails_rather_than_looking_empty():
    k = int(np.flatnonzero(TILTS == 6.0)[0])
    r = analyse(xf_error={k: (-40.0, 25.0)})                  # ~450 A off
    assert not r['pass']
    assert any('tilt +6.0' in p for p in r['problems']), (r['problems'], r['warnings'])
    assert not any(row['low_signal'] for row in r['tilts'])


def test_error_along_the_tilt_axis_is_caught():
    k = int(np.flatnonzero(TILTS == -12.0)[0])
    r = analyse(xf_error={k: (0.0, 5.0)})
    assert not r['pass']
    assert any('tilt -12.0' in p for p in r['problems']), r['problems']


def test_an_empty_tilt_is_set_aside_without_failing_its_neighbours():
    k = int(np.flatnonzero(TILTS == 9.0)[0])
    r = analyse(blank=(k,))
    assert r['pass'], r['problems']
    row = next(row for row in r['tilts'] if row['tilt'] == 9.0)
    assert row['low_signal'] and row['local_error_A'] is None
    assert any('tilt +9.0' in w and 'no reliable match' in w for w in r['warnings'])


def test_faint_high_tilts_are_still_judged():
    # Correlation falls steadily with tilt on real data (0.38 at 0 deg, 0.10 at
    # 51 deg on tomo25); those tilts must not be set aside as empty.
    k = int(np.flatnonzero(TILTS == -42.0)[0])
    r = analyse(noise=lambda t: 0.15 + 4 * abs(t) / 45, xf_error={k: (5.0, 0.0)})
    ccs = [row['cc'] for row in r['tilts']]
    assert min(ccs) < 0.5 * np.median(ccs)                 # as faint as the real ones
    assert not any(row['low_signal'] for row in r['tilts'])
    assert any('tilt -42.0' in p for p in r['problems']), r['problems']


def test_tilts_too_noisy_to_measure_are_set_aside_not_failed():
    # Correct alignment, but the highest tilts are so noisy that their
    # correlation peaks are random: they must not fail a good alignment.
    r = analyse(noise=lambda t: 0.15 + 8 * abs(t) / 45)
    assert r['pass'], r['problems']
    low = [row['tilt'] for row in r['tilts'] if row['low_signal']]
    assert low and min(abs(t) for t in low) > 20, low
    assert r['max_local_error_A'] < qc.LOCAL_FAIL_A


def test_noisy_series_with_disconnected_measurements_requires_review():
    # This previously passed because least squares assigned arbitrary relative
    # positions to two disconnected groups. Ground-truth alignment is correct,
    # but these measurements cannot establish that, so QC must not certify it.
    with pytest.raises(ValueError, match='disconnected'):
        analyse(noise=lambda t: 0.15 + 12 * abs(t) / 45)


@pytest.mark.parametrize('usable', [(), (14, 15)])
def test_insufficient_reliable_tilts_cannot_pass(usable):
    with pytest.raises(ValueError, match='insufficient reliable tilts'):
        analyse(blank=[k for k in range(len(TILTS)) if k not in usable])


def test_no_overlap_cannot_pass():
    with pytest.raises(ValueError, match='insufficient reliable tilts'):
        analyse(xf_error={k: (1000, 1000) for k in range(len(TILTS))})


def test_disconnected_reliable_pairs_cannot_pass(monkeypatch):
    measure = qc.measure_pairs

    def broken_link(*args, **kwargs):
        pairs, maxshift = measure(*args, **kwargs)
        # Each tilt still has a good neighbour, but nothing measures the
        # displacement between the two halves of the series.
        for p in pairs:
            if {p['lo'], p['hi']} == {14, 15}:
                p['significance'] = 0.0
        return pairs, maxshift

    monkeypatch.setattr(qc, 'measure_pairs', broken_link)
    with pytest.raises(ValueError, match='disconnected'):
        analyse()


def write(path, data, voxel=PX):
    with mrcfile.new(path, overwrite=True) as m:
        m.set_data(np.ascontiguousarray(np.stack(data), dtype=np.float32))
        m.voxel_size = voxel


def run(tmp_path, xf_rows, tilts=TILTS, header=PX, blank=()):
    sections, xf = series(blank=blank)
    write(tmp_path / 'TS_1.st', sections, header)
    (tmp_path / 'TS_1.xf').write_text(''.join(
        '{:.3f} {:.3f} {:.3f} {:.3f} {:.2f} {:.2f}\n'.format(*r) for r in xf[:xf_rows]))
    (tmp_path / 'TS_1.rawtlt').write_text(''.join('{:.2f}\n'.format(t) for t in tilts))
    (tmp_path / 'TS_1.st.aln').write_text('# AreTomo Alignment\n# NumPatches = 0\n# SEC ROT\n'
                                          '    0    -1.5    1.0    0.0    0.0    1.0    1.0'
                                          '    1.0    0.0    -45.00\n')
    return subprocess.run([sys.executable, str(SCRIPT), '--stack', str(tmp_path / 'TS_1.st'),
                           '--xf', str(tmp_path / 'TS_1.xf'), '--tilts', str(tmp_path / 'TS_1.rawtlt'),
                           '--angpix', str(PX), '--aln', str(tmp_path / 'TS_1.st.aln'),
                           '--out-prefix', str(tmp_path / 'TS_1')], capture_output=True, text=True)


def test_cli_writes_json_and_png_and_warns_on_a_wrong_header(tmp_path):
    proc = run(tmp_path, len(TILTS), header=2 * PX)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    r = json.loads((tmp_path / 'TS_1_alignment.json').read_text())
    assert r['pass'] and r['tilt_series'] == 'TS_1' and r['tilt_axis'] == -1.5
    assert r['tilt_file'] == str(tmp_path / 'TS_1.rawtlt')
    assert any('header says 19.000' in w for w in r['warnings'])
    assert any('NumPatches = 0' in w for w in r['warnings'])
    assert (tmp_path / 'TS_1_alignment.png').read_bytes()[:4] == b'\x89PNG'


def test_cli_reports_a_single_tilt_series_as_too_short(tmp_path):
    sections, _ = series()
    write(tmp_path / 'TS_1.st', sections[:1])
    (tmp_path / 'TS_1.xf').write_text('1 0 0 1 0 0\n')
    (tmp_path / 'TS_1.rawtlt').write_text('0.0\n')
    proc = subprocess.run([sys.executable, str(SCRIPT), '--stack', str(tmp_path / 'TS_1.st'),
                           '--xf', str(tmp_path / 'TS_1.xf'), '--tilts', str(tmp_path / 'TS_1.rawtlt'),
                           '--angpix', str(PX), '--out-prefix', str(tmp_path / 'TS_1')],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and '1 tilt(s): too few' in proc.stdout, proc.stdout


def test_cli_refuses_mismatched_inputs(tmp_path):
    proc = run(tmp_path, len(TILTS) - 1)
    assert proc.returncode == 2
    assert 'xf 30 lines' in proc.stdout
    assert not (tmp_path / 'TS_1_alignment.json').exists()


def test_cli_refuses_to_certify_noise_only_series(tmp_path):
    proc = run(tmp_path, len(TILTS), blank=range(len(TILTS)))
    assert proc.returncode == 2
    assert 'insufficient reliable tilts' in proc.stdout
    assert not (tmp_path / 'TS_1_alignment.json').exists()
