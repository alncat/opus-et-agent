"""Write picks from native-size, consecutive XY slice images to a coordinate STAR."""
import numpy as np
import pandas as pd
import starfile


def write_slice_picks_star(path, picks_xyz, *, tomogram, crop_origin_xyz,
                           tomo_angpix, coords_angpix):
    """Use zero-based (column, row, local slice) and the crop's first voxel in XYZ.

    The input images have the same orientation and sampling as volume[z, y, x].
    The STAR coordinates use coords_angpix on the same full-volume origin.
    """
    points = np.asarray(picks_xyz, dtype=float)
    if points.shape == (0,):
        points = points.reshape(0, 3)
    origin = np.asarray(crop_origin_xyz, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or origin.shape != (3,):
        raise ValueError("expected a list of XYZ picks and one XYZ crop origin")
    if not np.isfinite(points).all() or not np.isfinite(origin).all():
        raise ValueError("pick coordinates and crop origin must be finite")
    if not all(np.isfinite(v) and v > 0 for v in (tomo_angpix, coords_angpix)):
        raise ValueError("pixel sizes must be positive and finite")

    full_xyz = (points + origin) * (tomo_angpix / coords_angpix)
    table = pd.DataFrame(full_xyz, columns=["rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ"])
    table["rlnPixelSize"] = coords_angpix
    table["rlnMicrographName"] = tomogram
    starfile.write(table, path)
