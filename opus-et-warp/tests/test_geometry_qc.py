"""Gate 1 image-based geometry QC on synthetic data.

The 2026-09 failure: WARP reconstructed the central half of every tomogram,
magnified 2x, while all dimension/header/sampling checks passed.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from scipy import ndimage

mrcfile = pytest.importorskip('mrcfile')

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'geometry_qc.py'
spec = importlib.util.spec_from_file_location('geometry_qc', SCRIPT)
qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qc)

VOXEL = 9.476


def specimen(seed=0, shape=(48, 200, 280)):
    """Blobs of mixed size in a slab, so XY content and Z order are both distinctive."""
    rng = np.random.default_rng(seed)
    v = np.zeros(shape, np.float32)
    nz, ny, nx = shape
    for _ in range(900):
        z, y, x = rng.integers(nz // 4, 3 * nz // 4), rng.integers(ny), rng.integers(nx)
        v[z, y, x] += rng.uniform(0.5, 2.0)
    v = ndimage.gaussian_filter(v, rng.uniform(1.2, 1.8)) + ndimage.gaussian_filter(v, 4.0)
    return v + rng.normal(0, 0.02, shape).astype(np.float32)


def write(path, data, voxel):
    with mrcfile.new(path, overwrite=True) as m:
        m.set_data(np.ascontiguousarray(data, dtype=np.float32))
        m.voxel_size = voxel


def zoom_centre(v, factor):
    """Central 1/factor of the field, magnified back to full size (Z untouched)."""
    ny, nx = v.shape[1:]
    cy, cx = ny // 2, nx // 2
    hy, hx = int(ny / factor / 2), int(nx / factor / 2)
    crop = v[:, cy - hy:cy + hy, cx - hx:cx + hx]
    return ndimage.zoom(crop, (1, ny / crop.shape[1], nx / crop.shape[2]), order=1)


def run(tmp_path, warp, extra=()):
    ali = specimen()
    write(tmp_path / 'TS_1_ali.mrc', ali, VOXEL)
    write(tmp_path / 'TS_1_9.48Apx.mrc', warp(ali), VOXEL)
    proc = subprocess.run([sys.executable, str(SCRIPT), '--ali', str(tmp_path / 'TS_1_ali.mrc'),
                           '--warp', str(tmp_path / 'TS_1_9.48Apx.mrc'),
                           '--out-prefix', str(tmp_path / 'qc' / 'TS_1'), *extra],
                          capture_output=True, text=True)
    report = json.loads((tmp_path / 'qc' / 'TS_1_geometry.json').read_text()) \
        if (tmp_path / 'qc' / 'TS_1_geometry.json').exists() else None
    return proc, report


def test_matching_reconstruction_passes(tmp_path):
    rng = np.random.default_rng(1)
    proc, report = run(tmp_path, lambda v: v + rng.normal(0, 0.05, v.shape).astype(np.float32))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / 'qc' / 'TS_1_geometry.png').stat().st_size > 0
    assert report['warp_vs_aretomo']['scale'] == pytest.approx(1.0, abs=0.03)
    assert report['z_mirror']['r_direct'] > report['z_mirror']['r_z_mirrored'] + 0.3


def test_central_half_magnified_2x_fails(tmp_path):
    # The 2026-09 incident: same dimensions and headers, 2x zoom of the centre.
    proc, report = run(tmp_path, lambda v: zoom_centre(v, 2.0))
    assert proc.returncode == 1
    assert report['warp_vs_aretomo']['scale'] == pytest.approx(2.0, rel=0.05)
    assert 'magnified 2.0' in proc.stdout


def test_z_mirrored_reconstruction_fails(tmp_path):
    proc, report = run(tmp_path, lambda v: v[::-1].copy())
    assert proc.returncode == 1
    assert report['warp_vs_aretomo']['scale'] == pytest.approx(1.0, abs=0.03)
    assert 'Z-mirrored' in proc.stdout


def test_different_voxel_sizes_compare_in_physical_units(tmp_path):
    # WARP at half the voxel size (twice the voxels) covering the same field: a pass.
    ali = specimen()
    write(tmp_path / 'TS_1_ali.mrc', ali, VOXEL)
    write(tmp_path / 'TS_1_4.74Apx.mrc', ndimage.zoom(ali, 2, order=1), VOXEL / 2)
    proc = subprocess.run([sys.executable, str(SCRIPT), '--ali', str(tmp_path / 'TS_1_ali.mrc'),
                           '--warp', str(tmp_path / 'TS_1_4.74Apx.mrc'),
                           '--out-prefix', str(tmp_path / 'qc' / 'TS_1')],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def frame_tree(tmp_path, tilt_image, frame_pixel, binning=1):
    """Frame settings + average + tomostar whose 0-degree tilt is `tilt_image`."""
    frames = tmp_path / 'warp_frameseries'
    (frames / 'average').mkdir(parents=True)
    raw = frame_pixel / 2 ** binning
    (tmp_path / 'warp_frameseries.settings').write_text(
        '<Settings><Import><Param Name="PixelSize" Value="{}" /><Param Name="BinTimes" Value="{}" />'
        '<Param Name="ProcessingFolder" Value="{}" /></Import></Settings>'.format(raw, binning, frames))
    write(frames / 'average' / 'mov_0002.mrc', tilt_image[None], frame_pixel)
    (tmp_path / 'TS_1.tomostar').write_text(
        'data_\n\nloop_\n_wrpMovieName #1\n_wrpAngleTilt #2\n_wrpAxisAngle #3\n'
        '  ../warp_frameseries/mov_0001.tif   3.00  -1.2\n'
        '  ../warp_frameseries/mov_0002.tif  -0.02  -1.2\n'
        '  ../warp_frameseries/mov_0003.tif  -3.00  -1.2\n')
    return ['--tomostar', str(tmp_path / 'TS_1.tomostar'),
            '--frame-settings', str(tmp_path / 'warp_frameseries.settings')]


def test_tomostar_picks_the_tilt_nearest_zero(tmp_path):
    frame_tree(tmp_path, np.zeros((8, 8), np.float32), 2.369)
    path, pixel, angle = qc.tilt0_from_tomostar(tmp_path / 'TS_1.tomostar',
                                                tmp_path / 'warp_frameseries.settings')
    assert path.name == 'mov_0002.mrc' and angle == pytest.approx(-0.02)
    assert pixel == pytest.approx(2.369)  # raw 1.1845 A x 2^1


@pytest.mark.parametrize('declared_pixel,expect_pass', [(2.369, True), (VOXEL / 2, False)])
def test_raw_tilt_scale_is_checked(tmp_path, declared_pixel, expect_pass):
    # The 0-degree tilt: the specimen projection rotated ~7 deg (tilt axis), sampled
    # 4x finer (2.369 A). Declaring the wrong frame pixel makes it look 2x off.
    proj = specimen().sum(0)
    # Inverted contrast, as in real data: dense material is dark in the raw tilt.
    tilt = -ndimage.rotate(ndimage.zoom(proj, 4, order=1), 7.0, reshape=False, order=1)
    tilt += np.random.default_rng(3).normal(0, tilt.std() * 0.5, tilt.shape).astype(np.float32)
    extra = frame_tree(tmp_path, tilt, declared_pixel)
    proc, report = run(tmp_path, lambda v: v, extra)
    t = report['tilt0_vs_aretomo']
    assert (proc.returncode == 0) == expect_pass, proc.stdout + proc.stderr
    expected_scale = declared_pixel / 2.369  # too-large pixel -> content looks magnified
    assert t['scale'] == pytest.approx(expected_scale, rel=0.05)


def test_aretomo_edge_rows_do_not_drive_the_verdict(tmp_path):
    # Real AreTomo volumes had extreme values in their first/last Y rows. They
    # dominated the correlation: r fell to 0.01 at exactly 1x and recovered at
    # 1.009x, where the rows left the grid. A correct WARP volume must pass at 1x.
    def with_edges(v):
        v = v.copy()
        v[:, :2, :] = 50 * v.std()
        v[:, -2:, :] = -50 * v.std()
        return v
    ali = specimen()
    write(tmp_path / 'TS_1_ali.mrc', with_edges(ali), VOXEL)
    write(tmp_path / 'TS_1_9.48Apx.mrc', ali, VOXEL)
    proc = subprocess.run([sys.executable, str(SCRIPT), '--ali', str(tmp_path / 'TS_1_ali.mrc'),
                           '--warp', str(tmp_path / 'TS_1_9.48Apx.mrc'),
                           '--out-prefix', str(tmp_path / 'qc' / 'TS_1')],
                          capture_output=True, text=True)
    report = json.loads((tmp_path / 'qc' / 'TS_1_geometry.json').read_text())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    v = report['warp_vs_aretomo']
    assert v['scale'] == pytest.approx(1.0, abs=0.02)
    assert v['r_at_scale_1'] > 0.8


def test_truncated_mrc_is_reported_not_a_traceback(tmp_path):
    ali = specimen()
    write(tmp_path / 'TS_1_ali.mrc', ali, VOXEL)
    write(tmp_path / 'TS_1_9.48Apx.mrc', ali, VOXEL)
    rec = tmp_path / 'TS_1_9.48Apx.mrc'
    rec.write_bytes(rec.read_bytes()[:len(rec.read_bytes()) // 2])
    proc = subprocess.run([sys.executable, str(SCRIPT), '--ali', str(tmp_path / 'TS_1_ali.mrc'),
                           '--warp', str(rec), '--out-prefix', str(tmp_path / 'qc' / 'TS_1')],
                          capture_output=True, text=True)
    assert proc.returncode == 2
    assert 'unreadable or truncated' in proc.stderr and 'Traceback' not in proc.stderr


def test_slurm_wrapper_checks_every_series_and_blocks_on_failure(tmp_path):
    import shlex
    work = tmp_path / 'work'
    proj = specimen().sum(0)
    tilt = ndimage.zoom(proj, 4, order=1)
    (work / 'tomostar').mkdir(parents=True)
    frame_tree(work, tilt, 2.369)  # frame settings, average and a template tomostar
    template = (work / 'TS_1.tomostar').read_text()
    for ts, warp in [('TS_good', lambda v: v), ('TS_zoom', lambda v: zoom_centre(v, 2.0))]:
        (work / 'tomostar' / (ts + '.tomostar')).write_text(template)
        (work / 'tiltstack' / ts).mkdir(parents=True)
        (work / 'warp_tiltseries' / 'reconstruction').mkdir(parents=True, exist_ok=True)
        ali = specimen()
        write(work / 'tiltstack' / ts / (ts + '_ali.mrc'), ali, VOXEL)
        write(work / 'warp_tiltseries' / 'reconstruction' / (ts + '_9.48Apx.mrc'), warp(ali), VOXEL)
    # A previous run's result for an excluded series must not enter this summary.
    old_qc = work / 'gate1_qc' / 'geometry'
    old_qc.mkdir(parents=True)
    (old_qc / 'TS_excluded_geometry.json').write_text(json.dumps({
        'tilt_series': 'TS_excluded', 'pass': True,
        'warp_vs_aretomo': {'scale': 1.0, 'r': 1.0}, 'problems': []}))
    skill = tmp_path / 'skill'
    skill.mkdir()
    (skill / 'scripts').symlink_to(SCRIPT.parent)
    conf = dict(WORK_DIR=work, TOMOSTAR_DIR=work / 'tomostar', TILTSTACK_DIR=work / 'tiltstack',
                PROCESSING_DIR=work / 'warp_tiltseries', FRAMESERIES_DIR=work / 'warp_frameseries',
                ALIGN_ANGPIX=9.476, OPUSET_ENV='qc')
    (skill / 'pipeline.conf').write_text('\n'.join('{}={}'.format(k, shlex.quote(str(v)))
                                                   for k, v in conf.items()))
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name, body in {'conda': 'exit 0', 'python': 'exec {} "$@"'.format(shlex.quote(sys.executable))}.items():
        (bindir / name).write_text('#!/bin/bash\n' + body + '\n')
        (bindir / name).chmod(0o755)
    source = (SCRIPT.parent / 'warp_geometry_qc.slurm').read_text().replace('source ~/.bashrc', '')
    import os
    proc = subprocess.run(['bash', '-c', source], capture_output=True, text=True,
                          env={**os.environ, 'SKILL_DIR': str(skill),
                               'PATH': str(bindir) + os.pathsep + os.environ['PATH']})
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert 'FAILED for 1 tilt series: TS_zoom' in proc.stdout
    rows = [l.split('\t') for l in (work / 'gate1_qc' / 'geometry_metrics.tsv').read_text().splitlines()]
    verdicts = {r[0]: r[1] for r in rows[1:]}
    assert verdicts == {'TS_good': 'pass', 'TS_zoom': 'FAIL'}
    assert float(rows[[r[0] for r in rows].index('TS_zoom')][2]) == pytest.approx(2.0, rel=0.05)


def test_validator_rejects_missing_and_stale_geometry_qc(tmp_path):
    import os
    work = tmp_path / 'work'
    ts = 'TS_1'
    ali_dir = work / 'tiltstack' / ts
    rec_dir = work / 'warp_tiltseries' / 'reconstruction'
    tomo_dir = work / 'tomostar'
    qc_dir = work / 'gate1_qc' / 'geometry'
    for folder in (ali_dir, rec_dir, tomo_dir, qc_dir):
        folder.mkdir(parents=True, exist_ok=True)
    ali = ali_dir / (ts + '_ali.mrc')
    rec = rec_dir / (ts + '_9.48Apx.mrc')
    tomo = tomo_dir / (ts + '.tomostar')
    for path in (ali, ali_dir / (ts + '.tlt'), ali_dir / (ts + '.xf'), rec, tomo):
        path.touch()
    frames = work / 'warp_frameseries'
    (work / 'warp_frameseries.settings').touch()
    config = tmp_path / 'pipeline.conf'
    config.write_text('\n'.join([
        'WORK_DIR=' + str(work), 'TOMOSTAR_DIR=' + str(tomo_dir),
        'TILTSTACK_DIR=' + str(work / 'tiltstack'),
        'PROCESSING_DIR=' + str(work / 'warp_tiltseries'),
        'FRAMESERIES_DIR=' + str(frames), 'ALIGN_ANGPIX=9.476',
        'ANGPIX=1.1845', 'BINNING_FACTOR=8', 'CTF_VOLTAGE=200',
        'OPUSET_ENV=qc']))

    def geometry_checks():
        proc = subprocess.run(['bash', str(SCRIPT.parents[1] / 'validate.sh'), '--json',
                               '--phase', '5c', '--assume-env'], capture_output=True, text=True,
                              env={**os.environ, 'PIPELINE_CONF': str(config)})
        assert proc.stdout, proc.stderr
        return [c for c in json.loads(proc.stdout)['checks']
                if 'Phase 5c geometry QC' in c['message']]

    assert any(c['status'] == 'fail' for c in geometry_checks())
    result = qc_dir / (ts + '_geometry.json')
    result.write_text(json.dumps({'pass': True, 'warp': str(rec), 'ali': str(ali)}))
    (qc_dir / (ts + '_geometry.png')).write_bytes(b'png')
    metrics = work / 'gate1_qc' / 'geometry_metrics.tsv'
    metrics.write_text('TS\tverdict\nTS_1\tpass\n')
    assert any(c['status'] == 'pass' for c in geometry_checks())
    os.utime(rec, (rec.stat().st_mtime + 5, rec.stat().st_mtime + 5))
    assert any(c['status'] == 'fail' and 'stale' in c['message'] for c in geometry_checks())
