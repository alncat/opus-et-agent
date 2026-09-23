#!/usr/bin/env python3
"""Read WARP sampling and update only tomogram dimensions, preserving settings."""
import argparse
import math
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET


def sampling(path):
    root = ET.parse(path).getroot()
    params = {e.get('Name'): e.get('Value') for e in root.findall('./Import/Param')}
    pixel, binning = float(params['PixelSize']), float(params['BinTimes'])
    if not math.isfinite(pixel) or pixel <= 0 or not math.isfinite(binning) or binning < 0:
        raise ValueError('Invalid PixelSize or BinTimes in {}'.format(path))
    return pixel, binning


def check_sampling(settings, frames):
    actual, expected = sampling(settings), sampling(frames)
    if not all(math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-8) for a, b in zip(actual, expected)):
        raise ValueError('Tilt-series sampling {} differs from frame-series sampling {}. '
                         'Rebuild in a new processing tree; do not relabel existing exports.'.format(actual, expected))
    return actual


def validate_target(frames, alignment_angpix):
    pixel, binning = sampling(frames)
    working = pixel * 2 ** binning
    ratio = alignment_angpix / working
    if not math.isfinite(ratio) or ratio < 1 or not math.isclose(ratio, round(ratio), rel_tol=1e-6):
        raise ValueError('ALIGN_ANGPIX={} A must be a whole-number multiple of the '
                         'frame-series working pixel size {} A'.format(alignment_angpix, working))
    return int(round(ratio))


def validate_alignment(settings, frames, alignment_angpix):
    check_sampling(settings, frames)
    return validate_target(frames, alignment_angpix)


def contract(frames, angpix, binning_factor, alignment_angpix, settings=None):
    """One sampling convention for every phase: ANGPIX is the raw detector pixel
    recorded at frame import, and ALIGN_ANGPIX = ANGPIX x BINNING_FACTOR."""
    pixel, binning = sampling(frames)
    if not math.isclose(angpix, pixel, rel_tol=1e-6):
        raise ValueError(
            'ANGPIX={} A but the frame series was imported at raw PixelSize={} A '
            '(BinTimes={}). ANGPIX must stay the raw detector pixel; never replace it '
            'with a working pixel size. Restore ANGPIX={} and choose BINNING_FACTOR '
            'relative to it.'.format(angpix, pixel, binning, pixel))
    if binning_factor != int(binning_factor) or binning_factor < 1:
        raise ValueError('BINNING_FACTOR={} must be a positive integer'.format(binning_factor))
    if not math.isclose(alignment_angpix, angpix * binning_factor, rel_tol=1e-6):
        raise ValueError(
            'ALIGN_ANGPIX={} A differs from ANGPIX x BINNING_FACTOR = {} A. AreTomo VolZ '
            'and TM coordinate conversion scale by BINNING_FACTOR, so they must agree.'
            .format(alignment_angpix, angpix * binning_factor))
    validate_target(frames, alignment_angpix)
    if settings is not None:
        check_sampling(settings, frames)
    return 'raw {:g} A, frame bin 2^{:g} (working {:g} A), ALIGN_ANGPIX {:g} A = raw x {:g}'.format(
        pixel, binning, pixel * 2 ** binning, alignment_angpix, binning_factor)


def processing_folder(settings):
    root = ET.parse(settings).getroot()
    node = root.find("./Import/Param[@Name='ProcessingFolder']")
    if node is None or not node.get('Value'):
        raise ValueError('No Import/ProcessingFolder in {}'.format(settings))
    folder = Path(node.get('Value'))
    return folder if folder.is_absolute() else Path(settings).parent / folder


def selected_metadata(settings):
    """Per-item XMLs (movies or tilt series) that WARP will process."""
    items = []
    for path in sorted(processing_folder(settings).glob('*.xml')):
        root = ET.parse(path).getroot()
        if root.get('UnselectManual') != 'True':
            items.append((path, root))
    if not items:
        raise ValueError('No selected item XMLs in {}'.format(processing_folder(settings)))
    return items


def _report(problems, what):
    if problems:
        shown = '; '.join(problems[:5]) + ('; ...' if len(problems) > 5 else '')
        raise ValueError('{} of the selected items {}: {}'.format(len(problems), what, shown))


def check_voltage(settings, kv):
    problems = []
    for path, root in selected_metadata(settings):
        node = root.find("./CTF/Param[@Name='Voltage']")
        value = float(node.get('Value')) if node is not None else float('nan')
        if not math.isclose(value, kv):
            problems.append('{} records {} kV'.format(path.name, value))
    _report(problems, 'were not fitted at CTF_VOLTAGE={} kV'.format(kv))


def check_hand(settings, expected):
    values = {path.name: root.get('AreAnglesInverted') for path, root in selected_metadata(settings)}
    if expected == 'uniform':
        invalid = ['{}={}'.format(k, v) for k, v in values.items() if v not in ('True', 'False')]
        _report(invalid, 'have invalid AreAnglesInverted; choose a valid hand deliberately')
        if len(set(values.values())) > 1:
            _report(['{}={}'.format(k, v) for k, v in values.items()],
                    'have mixed AreAnglesInverted; choose one hand deliberately')
        return next(iter(values.values()))
    want = 'True' if expected == 'flip' else 'False'
    _report(['{} has AreAnglesInverted={}'.format(k, v) for k, v in values.items() if v != want],
            'did not record the requested hand {!r}'.format(expected))
    return want


def tm_coordinate_factor(particle_xml, coords_angpix):
    """Scale PyTOM volume pixels into the pixel units used by the output STAR."""
    import mrcfile
    if not math.isfinite(coords_angpix) or coords_angpix <= 0:
        raise ValueError('COORDS_ANGPIX must be a positive finite pixel size')
    origins = {node.get('Origin') for node in ET.parse(particle_xml).getroot().findall('./Particle/PickPosition')}
    if not origins or None in origins or '' in origins:
        raise ValueError('{} has no usable PickPosition Origin MRC'.format(particle_xml))
    voxel_ref = None
    for origin in sorted(origins):
        path = Path(origin)
        if not path.is_absolute() and not path.exists():
            path = Path(particle_xml).parent / path
        with mrcfile.open(path, header_only=True, permissive=True) as m:
            voxel = [float(m.voxel_size.x), float(m.voxel_size.y), float(m.voxel_size.z)]
        if any(not math.isfinite(v) or v <= 0 for v in voxel) or \
           any(not math.isclose(v, voxel[0], rel_tol=1e-4) for v in voxel):
            raise ValueError('{} has invalid or anisotropic voxel size {} A'.format(path, voxel))
        if voxel_ref is None:
            voxel_ref = voxel[0]
        elif not math.isclose(voxel[0], voxel_ref, rel_tol=1e-4):
            raise ValueError('{} has voxel size {} A, expected {} A'.format(path, voxel[0], voxel_ref))
    return voxel_ref / coords_angpix

def volume_dimensions(paths, alignment_angpix):
    import mrcfile
    if not paths:
        raise ValueError('No AreTomo *_ali.mrc volumes found')
    voxel_ref = None
    dims = [0, 0, 0]
    for path in paths:
        with mrcfile.open(path, header_only=True, permissive=True) as m:
            voxel = [float(m.voxel_size.x), float(m.voxel_size.y), float(m.voxel_size.z)]
            actual = [int(m.header.nx), int(m.header.ny), int(m.header.nz)]
        if any(not math.isfinite(v) or v < alignment_angpix * (1 - 1e-4)
               for v in voxel) or any(d <= 0 for d in actual):
            raise ValueError('{} has invalid voxel size {} A or dimensions {}'.format(path, voxel, actual))
        if voxel_ref is None:
            voxel_ref = voxel[0]
        if any(not math.isclose(v, voxel_ref, rel_tol=1e-4) for v in voxel):
            raise ValueError('{} voxel size {} A disagrees with other volumes ({} A)'.format(
                path, voxel, voxel_ref))
        dims = [max(a, b) for a, b in zip(dims, actual)]
    return voxel_ref, dims


def expected_raw_dimensions(voxel_angpix, dims, raw_pixel):
    return [math.ceil(d * voxel_angpix / raw_pixel - 1e-8) for d in dims]


def check_dimensions(settings, frames, alignment_angpix, paths):
    validate_alignment(settings, frames, alignment_angpix)
    voxel, dims = volume_dimensions(paths, alignment_angpix)
    pixel, _ = sampling(settings)
    expected = expected_raw_dimensions(voxel, dims, pixel)
    root = ET.parse(settings).getroot()
    actual = [int(root.find("./Tomo/Param[@Name='Dimensions{}']".format(a)).get('Value'))
              for a in 'XYZ']
    tolerance = voxel / pixel  # one binned voxel accounts for AreTomo rounding
    if any(abs(a-b) > tolerance + 1e-6 for a, b in zip(actual, expected)):
        raise ValueError('WARP raw dimensions {} differ from AreTomo-derived {} '
                         '(tolerance {:.3g} raw pixels)'.format(actual, expected, tolerance))
    return actual, expected


def update_dimensions(settings, frames, alignment_angpix, dims):
    pixel, _ = check_sampling(settings, frames)
    if not math.isfinite(alignment_angpix) or alignment_angpix <= 0 or any(d <= 0 for d in dims):
        raise ValueError('Alignment pixel size and dimensions must be positive')
    # Preserve physical field of view using the raw detector pixel in settings.
    raw_dims = expected_raw_dimensions(alignment_angpix, dims, pixel)
    path = Path(settings)
    original = path.read_bytes()
    text = original.decode('utf-8')
    root = ET.fromstring(text)
    for axis, dim in zip('XYZ', raw_dims):
        name = 'Dimensions' + axis
        nodes = root.findall("./Tomo/Param[@Name='{}']".format(name))
        if len(nodes) != 1:
            raise ValueError('Expected one Tomo/{} in {}'.format(name, path))
        pattern = r'(<Param\s+Name="' + name + r'"\s+Value=")[^"]*("\s*/>)'
        text, count = re.subn(pattern, lambda m: m[1] + str(dim) + m[2], text)
        if count != 1:
            raise ValueError('Unrecognized or ambiguous {} format'.format(name))
    # All validation precedes writes. Keep a unique backup of the exact input.
    backup = Path(str(path) + '.before_update')
    i = 1
    while backup.exists():
        backup = Path(str(path) + '.before_update.{}'.format(i))
        i += 1
    shutil.copy2(path, backup)
    path.write_bytes(text.encode('utf-8'))
    print('Updated raw tomogram dimensions: {}; backup: {}'.format('x'.join(map(str, raw_dims)), backup))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('sampling')
    s.add_argument('frames')
    c = sub.add_parser('check')
    c.add_argument('settings')
    c.add_argument('frames')
    t = sub.add_parser('validate-target')
    t.add_argument('frames')
    t.add_argument('alignment_angpix', type=float)
    a = sub.add_parser('validate-alignment')
    a.add_argument('settings')
    a.add_argument('frames')
    a.add_argument('alignment_angpix', type=float)
    u = sub.add_parser('update-dimensions')
    u.add_argument('settings')
    u.add_argument('frames')
    u.add_argument('alignment_angpix', type=float)
    u.add_argument('dims', nargs=3, type=int)
    m = sub.add_parser('update-from-mrc')
    m.add_argument('settings')
    m.add_argument('frames')
    m.add_argument('alignment_angpix', type=float)
    m.add_argument('volumes', nargs='+')
    d = sub.add_parser('check-dimensions')
    d.add_argument('settings')
    d.add_argument('frames')
    d.add_argument('alignment_angpix', type=float)
    d.add_argument('volumes', nargs='+')
    k = sub.add_parser('contract')
    k.add_argument('frames')
    k.add_argument('angpix', type=float)
    k.add_argument('binning_factor', type=float)
    k.add_argument('alignment_angpix', type=float)
    k.add_argument('--settings')
    v = sub.add_parser('check-voltage')
    v.add_argument('settings')
    v.add_argument('kv', type=float)
    h = sub.add_parser('check-hand')
    h.add_argument('settings')
    h.add_argument('expected', choices=['flip', 'noflip', 'uniform'])
    f = sub.add_parser('tm-coordinate-factor')
    f.add_argument('particle_xml')
    f.add_argument('coords_angpix', type=float)
    args = p.parse_args()
    try:
        if args.command == 'contract':
            print('Sampling contract OK: ' + contract(args.frames, args.angpix, args.binning_factor,
                                                     args.alignment_angpix, args.settings))
        elif args.command == 'check-voltage':
            check_voltage(args.settings, args.kv)
        elif args.command == 'check-hand':
            print(check_hand(args.settings, args.expected))
        elif args.command == 'tm-coordinate-factor':
            print('{:.10g}'.format(tm_coordinate_factor(args.particle_xml, args.coords_angpix)))
        elif args.command == 'sampling':
            print('{:.10g} {:.10g}'.format(*sampling(args.frames)))
        elif args.command == 'check':
            check_sampling(args.settings, args.frames)
        elif args.command == 'validate-target':
            print(validate_target(args.frames, args.alignment_angpix))
        elif args.command == 'validate-alignment':
            print(validate_alignment(args.settings, args.frames, args.alignment_angpix))
        elif args.command == 'check-dimensions':
            actual, expected = check_dimensions(args.settings, args.frames,
                                                args.alignment_angpix, args.volumes)
            print('WARP raw dimensions {} match AreTomo-derived {}'.format(actual, expected))
        elif args.command == 'update-from-mrc':
            validate_alignment(args.settings, args.frames, args.alignment_angpix)
            voxel, dims = volume_dimensions(args.volumes, args.alignment_angpix)
            update_dimensions(args.settings, args.frames, voxel, dims)
        else:
            update_dimensions(args.settings, args.frames, args.alignment_angpix, args.dims)
    except (ImportError, OSError, ValueError, KeyError, TypeError, ET.ParseError) as e:
        p.exit(1, 'ERROR: {}\n'.format(e))


if __name__ == '__main__':
    main()
