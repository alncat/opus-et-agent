"""Rotating single-map showcases for the M-refinement result — one 360 turntable per
refined map (ribosome 7.76 Å, FAS 13.88 Å). Plain ChimeraX (no ArtiaX): contour at
~mean+4sd, `surface dust` to clean speckle, shadows + silhouettes on white — the moving
companion to the same-scale still `m_refined_maps.png`. Local ChimeraX 1.10.

Inputs (cluster-synced): ribo_refined.mrc = m/species/ribo_11ea1073/ribo_filtsharp.mrc,
fas_refined.mrc = m/species/fas_89b38e4c/fas_filtsharp.mrc (both 3.37 Å/px).

    ChimeraX map_spins.cxc     # -> m_refined_ribo_spin.mp4 + m_refined_fas_spin.mp4
"""
from pathlib import Path

# (map, contour ~mean+4sd, color, out.mp4) — one turntable each
MAPS = [
    ("ribo_refined.mrc", 0.010, "cornflower blue", "m_refined_ribo_spin.mp4"),
    ("fas_refined.mrc", 0.0072, "gold", "m_refined_fas_spin.mp4"),
]


def emit(input_dir=".", out_cxc="map_spins.cxc"):
    d = str(Path(input_dir).resolve())
    lines = []
    for i, (mrc, level, color, movie) in enumerate(MAPS):
        if i:
            lines.append("close all")
        lines += [
            f"open {d}/{mrc}",
            f"volume #1 level {level}",
            "surface dust #1 size 120",          # drop disconnected speckle
            f"color #1 {color}",
            "lighting soft",
            "lighting shadows true intensity 0.7",
            "graphics silhouettes true width 1.2",
            "set bgColor white",
            "view",
            "movie record size 1000,1000 supersample 2",
            "turn y 3 120",                       # 360 over 120 frames (single map = cheap)
            "wait 120",
            f"movie encode {d}/{movie} framerate 24 quality high",
        ]
    lines.append("exit")
    Path(out_cxc).write_text("\n".join(lines) + "\n")
    print("wrote", out_cxc)


if __name__ == "__main__":
    import sys
    emit(sys.argv[1] if len(sys.argv) > 1 else ".")
