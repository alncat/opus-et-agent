import importlib.util
from pathlib import Path

import pandas as pd
import pytest
import starfile

spec = importlib.util.spec_from_file_location('exclude_stars', Path(__file__).resolve().parents[1] / 'scripts/exclude_stars.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def particles(mic, xs):
    return pd.DataFrame({'rlnMicrographName': [mic] * len(xs), 'rlnCoordinateX': xs,
                         'rlnCoordinateY': 0., 'rlnCoordinateZ': 0., 'rlnOriginX': 2.})


def test_disjoint_tomograms_preserve_query_rows_and_refined_origins():
    query = particles('B', [0., 40.])
    pd.testing.assert_frame_equal(mod.exclude_particles(particles('A', [0.]), query, 4.2), query)


def test_threshold_uses_explicit_physical_sampling():
    query = particles('A', [35.])
    assert len(mod.exclude_particles(particles('A', [0.]), query, 4.2)) == 1
    assert len(mod.exclude_particles(particles('A', [0.]), query, 3.37)) == 0


def test_cli_missing_sampling_refuses_to_guess(tmp_path):
    with pytest.raises(SystemExit) as exc:
        mod.main(['ref.star', str(tmp_path / 'query.star'), '--config', str(tmp_path / 'missing.pkl')])
    assert exc.value.code == 2
    assert not (tmp_path / 'query_exclude.star').exists()


def test_cli_writes_disjoint_query_without_index_columns(tmp_path):
    ref, query = tmp_path / 'ref.star', tmp_path / 'query.star'
    starfile.write(particles('A', [0.]), ref)
    starfile.write(particles('B', [1., 2.]), query)
    assert mod.main([str(ref), str(query), '--angpix', '4.2']) == 0
    data = starfile.read(tmp_path / 'query_exclude.star')
    assert data['rlnCoordinateX'].tolist() == [1., 2.]
    assert 'index' not in data and 'rlnOriginX' in data


def test_all_overlaps_removed_without_crossing_tomograms():
    ref = particles('A', [0., 10.])
    query = pd.concat([particles('A', [0., 10., 40., 50.]), particles('B', [0.])], ignore_index=True)
    result = mod.exclude_particles(ref, query, 4.2)
    assert list(zip(result.rlnMicrographName, result.rlnCoordinateX)) == [('A', 50.), ('B', 0.)]


def test_no_shared_names_warns():
    with pytest.warns(UserWarning, match='No shared micrograph'):
        assert len(mod.exclude_particles(particles('TS', [0.]), particles('TS.tomostar', [0.]), 4.2)) == 1


def test_cli_config_distance_and_output(tmp_path):
    import pickle
    ref, query, output = [tmp_path / n for n in ('ref.star', 'query.star', 'filtered.star')]
    starfile.write(particles('A', [0.]), ref)
    starfile.write(particles('A', [1., 3., 5.]), query)
    config = tmp_path / 'config.pkl'
    config.write_bytes(pickle.dumps({'model_args': {'Apix': 8.4}, 'dataset_args': {'downfrac': 0.5}}))
    assert mod.main([str(ref), str(query), '--config', str(config), '--distance', '13', '-o', str(output)]) == 0
    assert starfile.read(output).rlnCoordinateX.tolist() == [5.]
