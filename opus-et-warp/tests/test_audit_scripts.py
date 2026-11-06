"""Run the real submission scripts against lightweight tool stand-ins."""
import os
import pickle
import shlex
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import starfile

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def executable(path, body):
    path.write_text(body)
    path.chmod(0o755)


def run_script(tmp_path, name, values, extra=None):
    conf = tmp_path / 'skill'
    conf.mkdir()
    (conf / 'pipeline.conf').write_text('\n'.join(
        f'{key}={shlex.quote(str(value))}' for key, value in values.items()) + '\n')
    (conf / 'species.conf').write_text('# test species\n')
    bindir = tmp_path / 'bin'
    bindir.mkdir(exist_ok=True)
    (bindir / 'python').symlink_to(sys.executable)
    # Skip only interactive shell setup; read the actual config and execute
    # the actual script. No real cluster tools or user environment are changed.
    wrapper = '''source() { case "$1" in */.bashrc) return 0;; *) builtin source "$@";; esac; }
conda() { :; }
export -f source conda
exec bash "$1"
'''
    return subprocess.run(['bash', '-c', wrapper, '_', str(SCRIPTS / name)],
                          env={**os.environ, 'SKILL_DIR': str(conf),
                               'PATH': str(bindir) + os.pathsep + os.environ['PATH'],
                               **(extra or {})}, text=True, capture_output=True)


@pytest.mark.parametrize('overwrite', [None, '0'])
def test_export_refuses_existing_particles_unless_explicitly_overridden(tmp_path, overwrite):
    processing = tmp_path / 'processing'
    subtomo = processing / 'subtomo' / 'TS_1'
    subtomo.mkdir(parents=True)
    particle = subtomo / 'TS_1_0_4.2A.mrc'
    particle.write_bytes(b'existing scientific data')
    picks = tmp_path / 'picks'
    picks.mkdir()
    (picks / 'TS_1_warp.star').write_text('data_\n')
    settings = tmp_path / 'tilt.settings'
    settings.touch()
    tools = tmp_path / 'tools'
    tools.mkdir()
    values = dict(WORK_DIR=tmp_path, PROCESSING_DIR=processing, TM_WARP_DIR=picks,
                  TILTSERIES_SETTINGS=settings, WARP_DIR=tools, OUTPUT_ANGPIX=4.2,
                  COORDS_ANGPIX=4.2, EXPORT_STAR=tmp_path / 'out.star',
                  SUBTOMO_BOX_SIZE=128, DIAMETER=320)
    # WORK_DIR is a shell config value, not exported; marker path is literal.
    executable(tools / 'WarpTools', '#!/bin/bash\ntouch ' + shlex.quote(str(tmp_path / 'export_called')) + '\n')
    extra = {} if overwrite is None else {'ABORT_ON_EXISTING_SUBTOMO': overwrite}
    proc = run_script(tmp_path, 'warp_export_particles.slurm', values, extra)
    assert (tmp_path / 'export_called').exists() == (overwrite == '0'), proc.stdout + proc.stderr
    assert (proc.returncode == 0) == (overwrite == '0'), proc.stdout + proc.stderr
    assert particle.read_bytes() == b'existing scientific data'


@pytest.mark.parametrize('failure', [False, True])
def test_selection_uses_refined_poses_or_stops(tmp_path, failure):
    result = tmp_path / 'result'
    result.mkdir()
    original = tmp_path / 'picks.star'
    starfile.write(pd.DataFrame({'rlnOriginX': [0.0]}), original)
    config = {'lattice_args': {'D': 65}, 'model_args': {'Apix': 4.2},
              'dataset_args': {'particles': str(original), 'downfrac': 1.0}}
    (result / 'config.pkl').write_bytes(pickle.dumps(config))
    (result / 'pose.0.pkl').touch()
    (result / 'picks_refined.star').write_text('stale output must not be reused')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    executable(bindir / 'dsd', f'#!{sys.executable}\n' + '''import argparse, os, sys, starfile
if os.environ.get('FAIL_POSE') == '1': sys.exit(2)
assert sys.argv[1] == 'parse_pose_star'
p = argparse.ArgumentParser()
p.add_argument('input'); p.add_argument('-D', type=int); p.add_argument('--Apix', type=float)
p.add_argument('--poses'); p.add_argument('--outstar', required=True)
a = p.parse_args(sys.argv[2:])
assert a.D == 64 and a.Apix == 4.2
d = starfile.read(a.input); d['rlnOriginX'] = 7.0
starfile.write(d, a.outstar)
''')
    executable(bindir / 'dsdsh', f'#!{sys.executable}\n' + '''import sys, shutil
from pathlib import Path
assert sys.argv[1] == 'parse_pose'
out = Path(sys.argv[5]) / ('analyze.' + sys.argv[6]) / ('kmeans' + sys.argv[7])
out.mkdir(parents=True, exist_ok=True)
shutil.copyfile(sys.argv[2], out / 'pre0.star')
''')
    proc = run_script(tmp_path, 'select_state.slurm',
                      dict(WORK_DIR=tmp_path, OUTPUT_DIR=result, TM_LABEL='ribo'),
                      {'SELECT_CLUSTERS': '0', 'ANALYZE_EPOCH': '0',
                       'FAIL_POSE': '1' if failure else '0'})
    out = result / 'sel_ribo.star'
    if failure:
        assert proc.returncode != 0 and not out.exists(), proc.stdout + proc.stderr
        assert not (result / 'analyze.0').exists()
    else:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert starfile.read(out)['rlnOriginX'].tolist() == [7.0]


@pytest.mark.parametrize('same_file', [False, True])
def test_halfmaps_use_explicit_pair_and_reject_identical_inputs(tmp_path, same_file):
    maps = tmp_path / 'maps'
    maps.mkdir()
    one, two = maps / 'one.mrc', maps / 'two.mrc'
    one.write_bytes(b'half one')
    two.write_bytes(b'half two')
    base = tmp_path / 'opuset' / 'ribo'
    base.mkdir(parents=True)
    # Unrelated old maps must not be auto-selected.
    old = base / 'fixed_old_subset1'
    old.mkdir()
    (old / 'tmp0.mrc').write_bytes(b'old map')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    executable(bindir / 'dsdsh', '#!/bin/bash\nexit 0\n')
    proc = run_script(tmp_path, 'prepare_m_halfmaps.slurm',
                      dict(WORK_DIR=tmp_path, TM_LABEL='ribo', M_INPUT_HALF1=one,
                           M_INPUT_HALF2=one if same_file else two))
    assert (proc.returncode == 0) == (not same_file), proc.stdout + proc.stderr
    if not same_file:
        assert (base / 'half1.mrc').resolve() == one
        assert (base / 'half2.mrc').resolve() == two
        assert not (base / 'halfmap_mask.mrc').exists()


@pytest.mark.parametrize('voltage', [200, 123])
def test_config_only_checks_values_without_requiring_pipeline_outputs(tmp_path, voltage):
    import json
    conf, species = tmp_path / 'pipeline.conf', tmp_path / 'species.conf'
    mdocs = tmp_path / 'mdoc'
    mdocs.mkdir()
    (mdocs / 'test.mdoc').write_text('TargetDefocus = -4.0\n')
    values = dict(WORK_DIR=tmp_path, MDOC_DIR=mdocs, ANGPIX=1.1845,
                  BINNING_FACTOR=4, ALIGN_ANGPIX=4.738, CTF_VOLTAGE=voltage,
                  CTF_RANGE_MAX=8, FRAMESERIES_DIR=tmp_path / 'missing_frames',
                  TILTSTACK_DIR=tmp_path / 'missing_stacks',
                  TILTSERIES_SETTINGS=tmp_path / 'missing.settings')
    conf.write_text('\n'.join(f'{k}={shlex.quote(str(v))}' for k, v in values.items()))
    species.write_text('DIAMETER=320\nTM_BOX_SIZE=96\nSUBTOMO_BOX_SIZE=176\nOUTPUT_ANGPIX=2.5\nTEMPLATERES=240\n')
    proc = subprocess.run(['bash', str(SCRIPTS.parent / 'validate.sh'), '--json',
                           '--config-only', '--species', str(species)],
                          env={**os.environ, 'PIPELINE_CONF': str(conf)},
                          capture_output=True, text=True)
    data = json.loads(proc.stdout)
    assert (data['errors'] == 0) == (voltage == 200), data
    assert (proc.returncode == 0) == (voltage == 200), proc.stderr
    assert not any(c['status'] == 'phase_completion' for c in data['checks'])
