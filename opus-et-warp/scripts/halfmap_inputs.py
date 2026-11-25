"""Bind fixed-mode half-maps to their input STARs and check particle independence."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pandas as pd
import starfile


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def sidecar(volume):
    return Path(str(volume.resolve()) + '.inputs.json')


def save_record(volume, star, datadir, initial_digest):
    if digest(star) != initial_digest:
        raise ValueError(f'Input STAR changed during training: {star}')
    record = dict(star=str(star.resolve()), datadir=str(datadir.resolve()),
                  star_sha256=initial_digest, map_sha256=digest(volume))
    with tempfile.TemporaryDirectory(dir=volume.parent) as tmp:
        pending = Path(tmp) / 'inputs.json'
        pending.write_text(json.dumps(record) + '\n')
        os.replace(pending, sidecar(volume))


def image_ids(star, datadir):
    blocks = starfile.read(star, always_dict=True, parse_as_string=['rlnImageName'])
    particles = [b for b in blocks.values() if isinstance(b, pd.DataFrame) and 'rlnImageName' in b]
    if len(particles) != 1 or particles[0].empty:
        raise ValueError(f'Expected one nonempty particle table with rlnImageName in {star}')
    ids = set()
    for name in particles[0].rlnImageName:
        if pd.isna(name) or str(name).strip() in ('', '?', '.'):
            raise ValueError(f'Missing rlnImageName in {star}')
        index, sep, path = str(name).partition('@')
        if sep and index.isdecimal():
            ids.add((int(index), (datadir / path).resolve()))
        else:
            ids.add((None, (datadir / str(name)).resolve()))
    return ids


def verified_ids(volume, supplied_star, legacy_datadir):
    receipt = sidecar(volume)
    if receipt.exists():
        record = json.loads(receipt.read_text())
        star = Path(supplied_star or record['star'])
        if digest(volume) != record['map_sha256'] or digest(star) != record['star_sha256']:
            raise ValueError(f'Map or training STAR differs from the recorded input/output for {volume}')
        datadir = Path(record['datadir'])
    elif supplied_star:
        star = Path(supplied_star)
        datadir = legacy_datadir
        print(f'Legacy map {volume}: using operator-supplied training STAR {star}')
    else:
        raise ValueError(f'Missing provenance for {volume}. Supply its original training STAR '
                         'in M_INPUT_STAR1 or M_INPUT_STAR2 for a legacy run.')
    return image_ids(star, datadir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    subs.add_parser('fingerprint').add_argument('map', type=Path)
    record = subs.add_parser('record')
    for arg in ('map', 'star', 'datadir'):
        record.add_argument(arg, type=Path)
    record.add_argument('initial_digest')
    check = subs.add_parser('check')
    for arg in ('half1', 'half2', 'datadir'):
        check.add_argument(arg, type=Path)
    check.add_argument('--star1')
    check.add_argument('--star2')
    args = parser.parse_args()
    try:
        if args.command == 'fingerprint':
            if args.map.exists():
                st = args.map.stat()
                print(st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            else:
                print('absent')
        elif args.command == 'record':
            save_record(args.map, args.star, args.datadir, args.initial_digest)
        else:
            left = verified_ids(args.half1, args.star1, args.datadir)
            right = verified_ids(args.half2, args.star2, args.datadir)
            overlap = left & right
            if overlap:
                raise ValueError(f'{len(overlap)} shared particle ID(s) across the two half-sets')
            print(f'Verified {len(left)} and {len(right)} unique particle IDs; zero overlap.')
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
