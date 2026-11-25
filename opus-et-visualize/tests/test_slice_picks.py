import sys
from pathlib import Path

import mrcfile
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from slice_picks import write_slice_picks_star
from slice_preview import save_slice_png
from tm_picks_overlay import read_picks_star, to_tomo_pixels


def test_rendered_crop_pick_round_trips_to_original_mrc_voxel(tmp_path):
    volume = np.zeros((19, 27, 35), dtype=np.float32)
    volume[10, 15, 23] = 1
    tomo = tmp_path / "TS_001.mrc"
    with mrcfile.new(tomo) as mrc:
        mrc.set_data(volume)
        mrc.voxel_size = 4.738

    # Read the simulated prediction from the rendered PNG pixels. The distinct
    # offsets and asymmetric crop expose transposes, Y flips, and slice offsets.
    picks = []
    with mrcfile.open(tomo) as mrc:
        tomo_angpix = float(mrc.voxel_size.x)
        for local_z, plane in enumerate(mrc.data[7:13, 14:21, 17:26]):
            png = tmp_path / f"slice_{local_z}.png"
            save_slice_png(plane, png)
            with Image.open(png) as image:
                ys, xs = np.nonzero(np.asarray(image.convert("L")))
            picks.extend((x, y, local_z) for y, x in zip(ys, xs))
    assert picks == [(6, 1, 3)]

    output = tmp_path / "predicted.star"
    write_slice_picks_star(output, picks, tomogram=tomo.name, crop_origin_xyz=(17, 14, 7),
                           tomo_angpix=tomo_angpix, coords_angpix=2.369)
    xyz, _, sampling = read_picks_star(output, tomo="TS_001")
    assert sampling == 2.369
    np.testing.assert_allclose(xyz, [[46, 30, 20]], rtol=0, atol=1e-5)
    recovered = to_tomo_pixels(xyz, sampling, tomo_angpix)
    np.testing.assert_allclose(recovered, [[23, 15, 10]], rtol=0, atol=1e-5)
