#!/usr/bin/env python3
"""Exclude query particles nearest to reference picks within a physical radius."""
import argparse
import math
from pathlib import Path
import pickle

import numpy as np
import pandas as pd
import starfile

COORDS = ['rlnCoordinateX', 'rlnCoordinateY', 'rlnCoordinateZ']


def read_particles(path):
    data = starfile.read(path)
    if isinstance(data, pd.DataFrame):
        return data
    for block in data.values():
        if set(COORDS).issubset(block.columns):
            return block
    raise ValueError(f'no particle coordinate block in {path}')


def exclude_particles(reference, query, angpix, distance=136.0):
    """Preserve query rows/columns, excluding the nearest hit per reference pick."""
    if not all(math.isfinite(v) and v > 0 for v in (angpix, distance)):
        raise ValueError('angpix and distance must be positive and finite')
    keep = np.ones(len(query), dtype=bool)
    for mic in query['rlnMicrographName'].unique():
        positions = reference.loc[reference['rlnMicrographName'] == mic, COORDS].to_numpy()
        indices = np.flatnonzero((query['rlnMicrographName'] == mic).to_numpy())
        if not len(positions):
            continue
        candidates = query.iloc[indices][COORDS].to_numpy()
        dist2 = ((positions[:, None, :] - candidates[None, :, :]) ** 2).sum(axis=-1)
        nearest = dist2.argmin(axis=1)
        matched = dist2[np.arange(len(positions)), nearest] < (distance / angpix) ** 2
        keep[indices[nearest[matched]]] = False
    return query.iloc[np.flatnonzero(keep)].reset_index(drop=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', help='Reference picks to exclude')
    parser.add_argument('query', help='Query STAR whose remaining rows are written')
    parser.add_argument('--angpix', type=float, help='Coordinate pixel size in Angstroms')
    parser.add_argument('--config', default='config.pkl', help='Training config used if --angpix is omitted')
    parser.add_argument('--distance', type=float, default=136.0, help='Exclusion radius in Angstroms (default 136)')
    parser.add_argument('-o', '--out', help='Output STAR (default <query>_exclude.star)')
    args = parser.parse_args(argv)
    try:
        angpix = args.angpix
        if angpix is None:
            with open(args.config, 'rb') as handle:
                config = pickle.load(handle)
            angpix = float(config['model_args']['Apix']) * float(config['dataset_args'].get('downfrac', 1.0))
        result = exclude_particles(read_particles(args.reference), read_particles(args.query),
                                   angpix, args.distance)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(f'{exc}; provide --angpix explicitly or a valid --config')
    output = args.out or str(Path(args.query).with_suffix('')) + '_exclude.star'
    starfile.write(result, output, overwrite=True)
    print(f'Kept {len(result)} particles at {angpix:g} A/px -> {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
