"""Regression tests for sampling, settings preservation and failed hand checks.

WarpTools is mocked: these verify orchestration, not GPU reconstruction quality.
WARP_TEST_SCRIPTS can point at a staged remote script set.
"""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

SCRIPTS = Path(os.environ.get('WARP_TEST_SCRIPTS', Path(__file__).resolve().parents[1] / 'scripts'))
spec = importlib.util.spec_from_file_location('warp_settings', SCRIPTS / 'warp_settings.py')
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


def xml(pixel=1.1845, binning=1, folder='custom_v2'):
    return ('\ufeff<?xml version="1.0" encoding="utf-8"?>\n<Settings>\n'
            '<Import><Param Name="PixelSize" Value="%s" />'
            '<Param Name="BinTimes" Value="%s" />'
            '<Param Name="ProcessingFolder" Value="%s" /></Import>\n'
            '<CTF><Param Name="Voltage" Value="200" /></CTF>\n<Tomo>\n'
            '<Param Name="DimensionsX" Value="100" />\n'
            '<Param Name="DimensionsY" Value="100" />\n'
            '<Param Name="DimensionsZ" Value="100" />\n'
            '</Tomo></Settings>\n') % (pixel, binning, folder)


def item_xml(tag='TiltSeries', hand='False', kv='200', unselected=''):
    """Per-item WARP metadata: a movie or tilt series with its CTF voltage."""
    return ('<?xml version="1.0" encoding="utf-8"?>\n<%s AreAnglesInverted="%s" UnselectManual="%s">'
            '<CTF><Param Name="Voltage" Value="%s" /></CTF></%s>\n') % (tag, hand, unselected, kv, tag)


def test_dimension_update_preserves_sampling_and_all_other_bytes(tmp_path):
    fs, ts = tmp_path/'fs.settings', tmp_path/'ts.settings'
    fs.write_text(xml())
    ts.write_text(xml())
    original = ts.read_bytes()
    settings.update_dimensions(ts, fs, 9.476, [1440, 1024, 250])
    assert settings.sampling(ts) == (1.1845, 1)
    expected = original
    for axis, dim in zip('XYZ', [11520, 8192, 2000]):
        expected = expected.replace(('Dimensions%s" Value="100' % axis).encode(),
                                    ('Dimensions%s" Value="%d' % (axis, dim)).encode())
    assert ts.read_bytes() == expected
    assert Path(str(ts)+'.before_update').read_bytes() == original
    settings.update_dimensions(ts, fs, 9.476, [1440, 1024, 250])
    assert Path(str(ts)+'.before_update').read_bytes() == original
    assert Path(str(ts)+'.before_update.1').read_bytes() == expected


@pytest.mark.parametrize('pixel,binning', [(2.369, 0), (1.1845, 0), (float('nan'), 1)])
def test_bad_sampling_cannot_relabel_existing_tree(tmp_path, pixel, binning):
    fs, ts = tmp_path/'fs.settings', tmp_path/'ts.settings'
    fs.write_text(xml())
    ts.write_text(xml(pixel, binning))
    before = ts.read_bytes()
    with pytest.raises(ValueError):
        settings.update_dimensions(ts, fs, 4.738, [2880, 2046, 500])
    assert ts.read_bytes() == before
    assert not Path(str(ts)+'.before_update').exists()


def test_tm_coordinate_factor_uses_origin_mrc_voxel_size(tmp_path):
    import mrcfile
    import numpy as np
    volume = tmp_path/'tm_ali.mrc'
    with mrcfile.new(volume, overwrite=True) as m:
        m.set_data(np.zeros((2, 2, 2), dtype=np.float32))
        m.voxel_size = 9.476
    picks = tmp_path/'picks.xml'
    picks.write_text('<ParticleList><Particle><PickPosition X="858" Y="586" Z="71" '
                     'Origin="{}" Binning="8" /></Particle></ParticleList>'.format(volume))
    assert settings.tm_coordinate_factor(picks, 2.369) == pytest.approx(4)
    assert settings.tm_coordinate_factor(picks, 1.1845) == pytest.approx(8)
    with pytest.raises(ValueError, match='COORDS_ANGPIX'):
        settings.tm_coordinate_factor(picks, 0)


def test_convert_star_uses_tm_voxel_not_alignment_binning(pipeline):
    run, p = pipeline
    particles = p/'particles'
    particles.mkdir()
    volume = p/'tm_ali.mrc'
    volume.touch()
    (particles/'ts1_particles.xml').write_text(
        '<ParticleList><Particle><PickPosition X="858" Y="586" Z="71" '
        'Origin="{}" Binning="8" /></Particle></ParticleList>'.format(volume))
    with (p/'pipeline.conf').open('a') as f:
        f.write('\nTM_PARTICLES_DIR={}\nTM_STAR_DIR={}\nCOORDS_ANGPIX=2.369\nPYTOM_ENV=pytom_env\n'
                .format(shlex.quote(str(particles)), shlex.quote(str(p/'star_files'))))
    converter = p/'bin/convert.py'
    converter.write_text('#!'+sys.executable+'\n'+
        'import json, os, sys\n'
        'open(os.environ["CONVERT_CALLS"], "w").write(json.dumps(sys.argv[1:]))\n'
        'open(sys.argv[sys.argv.index("--outname")+1], "w").write("STAR")\n')
    converter.chmod(0o755)
    proc, _ = run('convert_to_star.slurm', CONVERT_CALLS=str(p/'convert_calls'))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    call = json.loads((p/'convert_calls').read_text())
    assert call[call.index('--pixelSize')+1] == '2.369'
    assert float(call[call.index('--binPyTom')+1]) == pytest.approx(4)


@pytest.fixture
def pipeline(tmp_path):
    (tmp_path/'scripts').symlink_to(SCRIPTS)
    fs = tmp_path/'frames'
    (fs/'average').mkdir(parents=True)
    (fs/'average/image.mrc').touch()
    Path(str(fs)+'.settings').write_text(xml(folder='frames'))
    (tmp_path/'ts.settings').write_text(xml())
    # Frame CTF done at 200 kV; tilt series not yet CTF-fitted (WARP default 300).
    # Deselected items carry contradictory values and must be ignored.
    (fs/'movie1.xml').write_text(item_xml('Movie', kv='200'))
    (fs/'movie2.xml').write_text(item_xml('Movie', kv='300.0', unselected='True'))
    ts_xml=tmp_path/'custom_v2'
    ts_xml.mkdir()
    (ts_xml/'ts1.xml').write_text(item_xml(kv='300'))
    (ts_xml/'ts2.xml').write_text(item_xml(hand='True', kv='300', unselected='True'))
    (tmp_path/'mdoc').mkdir()
    (tmp_path/'mdoc/image.mdoc').touch()
    (tmp_path/'tiltstack').mkdir()
    import mrcfile
    import numpy as np
    with mrcfile.new(tmp_path/'tiltstack/image_ali.mrc', overwrite=True) as m:
        m.set_data(np.zeros((5, 10, 12), dtype=np.float32))
        m.voxel_size = 18.952
    bindir=tmp_path/'bin'
    bindir.mkdir()
    for name, body in {
        'conda': 'exit 0', 'nvidia-smi': 'exit 0',
        'python': 'exec '+shlex.quote(sys.executable)+' "$@"',
        'headerPyTom': 'echo "Number of columns 1440 1024 250"',
    }.items():
        p=bindir/name
        p.write_text('#!/bin/bash\n'+body+'\n')
        p.chmod(0o755)
    mock=bindir/'WarpTools'
    mock.write_text('#!'+sys.executable+'\n'+'''import glob, json, os, re, sys
with open(os.environ['WARP_CALLS'], 'a') as f:
    f.write(json.dumps(sys.argv[1:])+'\\n')
def rewrite(pattern, value):
    # Like WARP, only selected tilt series are processed.
    for path in glob.glob(os.path.join(os.environ['WARP_TS_XML_DIR'], '*.xml')):
        text = open(path).read()
        if 'UnselectManual="True"' not in text:
            open(path, 'w').write(re.sub(pattern, lambda m: m.group(1) + value, text))
if '--check' in sys.argv:
    print(os.environ.get('HAND_OUTPUT', 'Average correlation: 0.471\\nThe average correlation is positive'))
    sys.exit(int(os.environ.get('HAND_STATUS', '0')))
if '--set_flip' in sys.argv or '--set_noflip' in sys.argv:
    status = int(os.environ.get('SET_STATUS', '0'))
    if status == 0 and not os.environ.get('SET_NOWRITE'):
        rewrite(r'(AreAnglesInverted=")[^"]*', 'True' if '--set_flip' in sys.argv else 'False')
    sys.exit(status)
if 'ts_ctf' in sys.argv:
    status = int(os.environ.get('CTF_STATUS', '0'))
    if status == 0 and not os.environ.get('CTF_NOWRITE'):
        rewrite(r'(Name="Voltage" Value=")[^"]*', sys.argv[sys.argv.index('--voltage') + 1])
    sys.exit(status)
''')
    mock.chmod(0o755)
    # Minimal MRC header interface so auto-detection exercises the real shell
    # conversion without needing mrcfile or real image arrays on the test host.
    (tmp_path/'mrcfile.py').write_text('''from types import SimpleNamespace
from contextlib import contextmanager
@contextmanager
def open(path, *args, **kwargs):
    if str(path).endswith('tm_ali.mrc'):
        h=SimpleNamespace(nx=1440,ny=1024,nz=250)
        v=SimpleNamespace(x=9.476,y=9.476,z=9.476)
    elif str(path).endswith('_ali.mrc'):
        h=SimpleNamespace(nx=12,ny=10,nz=5)
        v=SimpleNamespace(x=18.952,y=18.952,z=18.952)
    else:
        h=SimpleNamespace(nx=5760,ny=4092,nz=1)
        v=SimpleNamespace(x=2.369,y=2.369,z=2.369)
    yield SimpleNamespace(header=h,voxel_size=v)
''')
    config=dict(WORK_DIR=tmp_path,WARP_DIR=bindir,CONDA_LIB=bindir,
                FRAMESERIES_DIR=fs,TILTSERIES_SETTINGS=tmp_path/'ts.settings',
                PROCESSING_DIR=tmp_path/'new_ts',TOMOSTAR_DIR=tmp_path/'tomostar',
                MDOC_DIR=tmp_path/'mdoc',TILTSTACK_DIR=tmp_path/'tiltstack',
                CTF_VOLTAGE=200,CTF_CS=2.7,CTF_WINDOW=512,CTF_RANGE_MAX=5,
                CTF_DEFOCUS_MAX=4,ANGPIX=1.1845,EXPOSURE=3.2,TOMO_DIM_Z=2000,
                FRAME_MODE='movie',BINNING_FACTOR=8,ALIGN_ANGPIX=9.476,
                ARETOMO_OUTBIN=1)
    (tmp_path/'pipeline.conf').write_text('\n'.join(k+'='+shlex.quote(str(v)) for k,v in config.items()))
    env={**os.environ, 'PATH':str(bindir)+os.pathsep+os.environ['PATH'],
         'PYTHONPATH':str(tmp_path),'SKILL_DIR':str(tmp_path),'WARP_CALLS':str(tmp_path/'calls'),
         'WARP_TS_XML_DIR':str(tmp_path/'custom_v2'),'SPECIES_CONF':os.devnull}

    def run(script, **overrides):
        source=(SCRIPTS/script).read_text().replace('source ~/.bashrc', '# isolated test environment')
        proc=subprocess.run(['bash','-c',source],env={**env,**overrides},capture_output=True,text=True)
        calls=[json.loads(line) for line in (tmp_path/'calls').read_text().splitlines()] if (tmp_path/'calls').exists() else []
        return proc, [c for c in calls if c]
    return run,tmp_path


def test_setup_uses_frame_sampling_and_raw_dimensions(pipeline):
    run,_=pipeline
    proc,calls=run('warp_tiltseries_setup.slurm')
    assert proc.returncode == 0, proc.stdout+proc.stderr
    create=next(c for c in calls if c[0]=='create_settings')
    assert create[create.index('--angpix')+1]=='1.1845'
    assert create[create.index('--bin')+1]=='1'
    assert create[create.index('--tomo_dimensions')+1]=='11520x8184x2000'


def test_dimension_script_uses_physical_alignment_sampling(pipeline):
    run,p= pipeline
    proc,calls=run('warp_update_tomo_dims.slurm')
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert 'DimensionsX" Value="192"' in (p/'ts.settings').read_text()
    assert calls==[]  # no settings recreation with WARP defaults


@pytest.mark.parametrize('output,status',[
    ('Unhandled exception: NaN', '134'),
    ('The average correlation is positive', '1'),
    ('Average correlation: NaN\nThe average correlation is positive', '0'),
    ('Average correlation: 0', '0'),
    ('1 failed\nThe average correlation is positive','0'),
    ('The average correlation is positive\nThe average correlation is negative','0'),
])
def test_failed_hand_check_never_sets_hand_or_runs_ctf(pipeline,output,status):
    run,_=pipeline
    proc,calls=run('warp_ts_ctf.slurm',HAND_OUTPUT=output,HAND_STATUS=status)
    assert proc.returncode!=0
    assert len(calls)==1 and '--check' in calls[0]


@pytest.mark.parametrize('hand,output,flag',[
    ('auto','The average correlation is positive','--set_noflip'),
    ('auto','The average correlation is negative','--set_flip'),
    ('flip','','--set_flip'),('noflip','','--set_noflip'),('keep','',None),
])
def test_hand_choice_and_microscope_parameters(pipeline,hand,output,flag):
    run,_=pipeline
    proc,calls=run('warp_ts_ctf.slurm',CTF_HAND=hand,HAND_OUTPUT=output)
    assert proc.returncode==0,proc.stdout+proc.stderr
    if flag:
        assert flag in calls[-2]
    else:
        assert len(calls)==1
    ctf=calls[-1]
    assert ctf[0]=='ts_ctf'
    assert ctf[ctf.index('--voltage')+1]=='200'
    assert ctf[ctf.index('--cs')+1]=='2.7'


@pytest.mark.parametrize('failure', ['SET_STATUS','CTF_STATUS'])
def test_tool_failure_propagates(pipeline,failure):
    run,_=pipeline
    proc,calls=run('warp_ts_ctf.slurm',**{failure:'2'})
    assert proc.returncode!=0
    if failure=='SET_STATUS':
        assert all(c[0]!='ts_ctf' for c in calls)


def test_sampling_mismatch_blocks_ctf(pipeline):
    run,p=pipeline
    (p/'ts.settings').write_text(xml(2.369,0))
    proc,calls=run('warp_ts_ctf.slurm')
    assert proc.returncode!=0
    assert calls==[]


def test_missing_voltage_blocks_mutation(pipeline):
    run,p=pipeline
    conf=p/'pipeline.conf'
    conf.write_text(conf.read_text().replace('CTF_VOLTAGE=200','CTF_VOLTAGE='))
    proc,calls=run('warp_ts_ctf.slurm')
    assert proc.returncode!=0
    assert calls==[]


def test_setup_cannot_overwrite_incompatible_existing_settings(pipeline):
    run,p=pipeline
    old=p/'new_ts.settings'
    old.write_text(xml(2.369,0))
    original=old.read_bytes()
    proc,calls=run('warp_tiltseries_setup.slurm')
    assert proc.returncode!=0
    assert calls==[]
    assert old.read_bytes()==original


def test_harmless_nan_substring_does_not_stop_hand_check(pipeline):
    run,_=pipeline
    proc,calls=run('warp_ts_ctf.slurm',HAND_OUTPUT='Banana dataset\nThe average correlation is positive')
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert calls[-1][0]=='ts_ctf'


def set_sampling(p, angpix, binning_factor, align):
    conf=p/'pipeline.conf'
    text=conf.read_text().replace('ANGPIX=1.1845','ANGPIX='+str(angpix))
    text=text.replace('BINNING_FACTOR=8','BINNING_FACTOR='+str(binning_factor))
    text=text.replace('ALIGN_ANGPIX=9.476','ALIGN_ANGPIX='+str(align))
    conf.write_text(text)


def test_all_three_phases_use_same_explicit_alignment_pixel(pipeline):
    run,p=pipeline
    set_sampling(p, 1.1845, 4, 4.738)
    (p/'tiltstack/image.st').touch()
    (p/'tiltstack/image.tlt').touch()
    (p/'tiltstack/image.xf').touch()
    for script,verb,flag in [
        ('warp_export_stacks.slurm','ts_stack','--angpix'),
        ('warp_import_alignments.slurm','ts_import_alignments','--alignment_angpix'),
        ('warp_ts_reconstruct.slurm','ts_reconstruct','--angpix'),
    ]:
        proc,calls=run(script)
        command=next((c for c in calls if c[0]==verb),None)
        assert command is not None,proc.stdout+proc.stderr
        assert command[command.index(flag)+1]=='4.738'


def test_mrc_header_uses_actual_voxel_size_across_volumes(tmp_path):
    import mrcfile
    import numpy as np
    files = [tmp_path/'a_ali.mrc', tmp_path/'b_ali.mrc']
    for f in files:
        with mrcfile.new(f,overwrite=True) as m:
            m.set_data(np.zeros((5,10,12),dtype=np.float32))
            m.voxel_size=9.476
    voxel,dims=settings.volume_dimensions(files,4.738)
    assert voxel==pytest.approx(9.476) and dims==[12,10,5]
    with mrcfile.open(files[1],mode='r+') as m:
        m.voxel_size=4.738
    with pytest.raises(ValueError,match='disagrees'):
        settings.volume_dimensions(files,4.738)


def test_check_dimensions_allows_one_volume_voxel_rounding(tmp_path):
    import mrcfile
    import numpy as np
    fs,ts,f=tmp_path/'fs.settings',tmp_path/'ts.settings',tmp_path/'ali.mrc'
    fs.write_text(xml())
    ts.write_text(xml().replace('DimensionsX" Value="100','DimensionsX" Value="96')
                       .replace('DimensionsY" Value="100','DimensionsY" Value="79')
                       .replace('DimensionsZ" Value="100','DimensionsZ" Value="40'))
    with mrcfile.new(f,overwrite=True) as m:
        m.set_data(np.zeros((5,10,12),dtype=np.float32))
        m.voxel_size=9.476
    settings.check_dimensions(ts,fs,4.738,[f])
    ts.write_text(ts.read_text().replace('DimensionsY" Value="79','DimensionsY" Value="70'))
    with pytest.raises(ValueError,match='differ'):
        settings.check_dimensions(ts,fs,4.738,[f])


@pytest.mark.parametrize('binning_factor,align', [(8, 5.0), (1, 1.1845)])
def test_setup_rejects_inconsistent_or_subworking_alignment_pixel(pipeline,binning_factor,align):
    # 5.0 != ANGPIX x BINNING_FACTOR; 1.1845 is finer than the 2.369 A frame working pixel.
    run,p=pipeline
    set_sampling(p, 1.1845, binning_factor, align)
    proc,calls=run('warp_tiltseries_setup.slurm')
    assert proc.returncode!=0
    assert calls==[]


# --- The original bug: ANGPIX replaced by the working pixel after frame import ---

@pytest.mark.parametrize('script', [
    'warp_tiltseries_setup.slurm', 'warp_export_stacks.slurm', 'warp_update_tomo_dims.slurm',
    'warp_import_alignments.slurm', 'warp_ts_ctf.slurm', 'warp_ts_reconstruct.slurm',
    'convert_to_star.slurm',
])
def test_working_pixel_angpix_is_rejected_by_every_phase(pipeline, script):
    # 20260907: frames imported at raw 1.1845 A (bin 1), then ANGPIX edited to 2.369
    # so that ANGPIX x 2 = 4.738. Every phase must refuse before touching WARP.
    run,p=pipeline
    set_sampling(p, 2.369, 4, 9.476)
    before=(p/'ts.settings').read_bytes()
    proc,calls=run(script)
    assert proc.returncode!=0
    assert 'raw PixelSize=1.1845' in proc.stdout+proc.stderr
    assert [c for c in calls if c!=['--help']]==[]  # only the read-only install check
    assert (p/'ts.settings').read_bytes()==before


def test_contract_names_each_violation(tmp_path):
    fs, ts = tmp_path/'fs.settings', tmp_path/'ts.settings'
    fs.write_text(xml())
    ts.write_text(xml(2.369, 0))
    assert 'raw x 4' in settings.contract(fs, 1.1845, 4, 4.738)
    with pytest.raises(ValueError, match='raw PixelSize'):
        settings.contract(fs, 2.369, 2, 4.738)
    with pytest.raises(ValueError, match='ANGPIX x BINNING_FACTOR'):
        settings.contract(fs, 1.1845, 2, 4.738)
    with pytest.raises(ValueError, match='positive integer'):
        settings.contract(fs, 1.1845, 2.5, 2.96125)
    with pytest.raises(ValueError, match='differs from frame-series'):
        settings.contract(fs, 1.1845, 4, 4.738, settings=ts)


# --- Voltage ---

def append_conf(p, **values):
    with open(p/'pipeline.conf', 'a') as f:
        f.write(''.join('\n{}={}'.format(k, shlex.quote(str(v))) for k, v in values.items()))

@pytest.mark.parametrize('mode,verb,vflag,csflag', [
    ('movie', 'fs_motion_and_ctf', '--c_voltage', '--c_cs'),
    ('single', 'fs_ctf', '--voltage', '--cs'),
])
def test_frame_ctf_receives_and_records_voltage(pipeline, mode, verb, vflag, csflag):
    run,p=pipeline
    (p/'raw').mkdir()
    (p/'raw/a.mrc').touch()
    append_conf(p, FRAME_MODE=mode, FRAME_DIR=p/'raw', FILE_EXTENSION='*.mrc')
    proc,calls=run('warp_frameseries_import.slurm')
    assert proc.returncode==0, proc.stdout+proc.stderr
    ctf=next(c for c in calls if c[0]==verb)
    assert ctf[ctf.index(vflag)+1]=='200' and ctf[ctf.index(csflag)+1]=='2.7'


def test_frame_import_requires_voltage_and_verifies_xmls(pipeline):
    run,p=pipeline
    (p/'raw').mkdir()
    (p/'raw/a.mrc').touch()
    append_conf(p, FRAME_DIR=p/'raw', FILE_EXTENSION='*.mrc', CTF_VOLTAGE='')
    proc,calls=run('warp_frameseries_import.slurm')
    assert proc.returncode!=0 and calls==[]
    append_conf(p, CTF_VOLTAGE=200)
    (p/'frames/movie1.xml').write_text(item_xml('Movie', kv='300.0'))  # WARP default kept
    proc,calls=run('warp_frameseries_import.slurm')
    assert proc.returncode!=0
    assert 'movie1.xml records 300.0 kV' in proc.stdout+proc.stderr


def test_frame_ctf_at_wrong_voltage_blocks_hand_check(pipeline):
    run,p=pipeline
    (p/'frames/movie1.xml').write_text(item_xml('Movie', kv='300.0'))
    proc,calls=run('warp_ts_ctf.slurm')
    assert proc.returncode!=0
    assert calls==[]


def test_tilt_ctf_voltage_is_verified_after_fit(pipeline):
    run,p=pipeline
    proc,calls=run('warp_ts_ctf.slurm', CTF_NOWRITE='1')
    assert proc.returncode!=0
    assert calls[-1][0]=='ts_ctf'
    assert 'ts1.xml records 300.0 kV' in proc.stdout+proc.stderr


# --- Handedness ---

def test_hand_is_verified_in_every_selected_series(pipeline):
    run,p=pipeline
    proc,calls=run('warp_ts_ctf.slurm', HAND_OUTPUT='The average correlation is negative')
    assert proc.returncode==0, proc.stdout+proc.stderr
    assert 'AreAnglesInverted="True"' in (p/'custom_v2/ts1.xml').read_text()
    proc,calls=run('warp_ts_ctf.slurm', HAND_OUTPUT='The average correlation is positive',
                   SET_NOWRITE='1')
    assert proc.returncode!=0
    assert 'did not record the requested hand' in proc.stdout+proc.stderr
    assert calls[-1][0]!='ts_ctf'


def test_keep_refuses_mixed_hands(pipeline):
    run,p=pipeline
    (p/'custom_v2/ts3.xml').write_text(item_xml(hand='True'))
    proc,calls=run('warp_ts_ctf.slurm', CTF_HAND='keep')
    assert proc.returncode!=0
    assert 'mixed AreAnglesInverted' in proc.stdout+proc.stderr
    assert calls==[]


def test_keep_refuses_missing_hand(pipeline):
    run, p = pipeline
    (p/'custom_v2/ts1.xml').write_text(item_xml(hand=''))
    proc, calls = run('warp_ts_ctf.slurm', CTF_HAND='keep')
    assert proc.returncode != 0
    assert 'invalid AreAnglesInverted' in proc.stdout + proc.stderr
    assert calls == []
