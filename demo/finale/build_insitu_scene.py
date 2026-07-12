"""In-cell two-species ArtiaX finale driver. Run from a dir holding the cluster-synced
inputs (ribo_refined.mrc, fas_refined.mrc, TS_028.mrc, ribo_TS028.star, fas_TS028.star) —
see demo/render_commands.md §B. Writes finale.cxc → finale_insitu_still.png (hero still) +
finale_insitu.mp4 (gentle rock). Render LOCALLY on the Mac (GUI mode, no --nogui/--offscreen):
    /Applications/ChimeraX-1.10.app/Contents/bin/ChimeraX finale.cxc

Render method = render_commands.md §B footnote (the proven one): perspective tilt +
silhouettes, gentle ROCK (not a 360 turntable — a thin particle slab goes edge-on mid-spin),
coarsened surfaces (movie_step) for a tractable ~3,387-instance movie, and NO shadows —
per-frame shadow maps over thousands of ArtiaX instances hang even a single still (keep
shadows for the single-map spins only). Uses the CURRENT refined maps @ their mean+4·sd
contour; an older/stale map reads skeletal at the same level.
"""
import sys, numpy as np, mrcfile
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "opus-et-visualize/scripts"))
from gen_artiax_scene import emit_cxc


def crop(src, dst, box):
    with mrcfile.open(src, permissive=True) as m:
        d = np.asarray(m.data, np.float32); vx = float(m.voxel_size.x)
    n = d.shape[0]; c = n // 2; h = box // 2
    with mrcfile.new(dst, overwrite=True) as o:
        o.set_data(d[c - h:c + h, c - h:c + h, c - h:c + h]); o.voxel_size = vx
    print(f"cropped {src} {d.shape} -> {box}^3 (vox {vx:.3f})")


crop("ribo_refined.mrc", "ribo_crop.mrc", 128)   # ribosome ~250 A fits a 431 A box
crop("fas_refined.mrc", "fas_crop.mrc", 120)     # FAS barrel

emit_cxc(
    tomogram="TS_028.mrc",
    map_path=None,
    state_stars={0: "ribo_TS028.star", 1: "fas_TS028.star"},
    state_maps={0: "ribo_crop.mrc", 1: "fas_crop.mrc"},
    state_contours={0: 0.0100, 1: 0.0072},        # ~mean+4·sd per current refined map
    state_colors={0: "cornflower blue", 1: "gold"},
    coords_angpix=4.2,
    tomo_transfer=None,            # ArtiaX's own orthoslice gives the tomo/cell context
    bg_color="black",
    tilt_x=-55,                    # 3D perspective, not flat top-down
    silhouettes=True,              # crisp outlines once the slab is tilted
    still_out="finale_insitu_still.png",  # hero still saved before the movie (shipped asset name)
    movie_rock=30,                 # rock ±30° (a thin slab goes edge-on on a full 360)
    movie_step=3,                  # coarsen surfaces for a tractable 3,387-instance movie
    # shadows deliberately OFF — they hang over thousands of instances
    out_cxc="finale.cxc",
    movie_out="finale_insitu.mp4",
)

# emit_cxc ends on `movie encode`; append an explicit exit so an unattended
# `ChimeraX finale.cxc` closes. Also emit a still-only variant (stop before the
# movie) for a fast look without the full ~3,387-instance rock render.
lines = open("finale.cxc").read().splitlines()
open("finale.cxc", "w").write("\n".join(lines + ["exit"]) + "\n")
cut = next(k for k, l in enumerate(lines) if l.startswith("movie record"))
open("finale_still.cxc", "w").write("\n".join(lines[:cut] + ["exit"]) + "\n")
print("wrote finale.cxc + finale_still.cxc (hero still + gentle-rock movie, silhouettes, no shadows)")
