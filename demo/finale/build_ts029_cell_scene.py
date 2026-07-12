"""In-cell 'cellular context' render for TS_029 — the organelle-rich companion to the
TS_028 finale. TS_029 carries a large membrane-bound organelle (a clear round arc) that
the 1,825 ribosomes visibly *exclude*, plus 38 FAS — molecular sociology with real
cellular architecture. Uses the same hero aesthetic as the TS_028 finale (perspective
tilt + shadows + silhouettes, molecules spilling past a floating slab in black), tuned
so the organelle still reads. Local ChimeraX 1.10 + ArtiaX 0.7.0, GUI mode.

Inputs (cluster-synced into the run dir — see demo/render_commands.md §B3):
  TS_029_bin4.mrc   bin4 of warp_tiltseries/reconstruction/TS_029_13.48Apx.mrc (53.92 A/px)
  ribo_refined.mrc  m/species/ribo_11ea1073/ribo_filtsharp.mrc   (contour 0.010)
  fas_refined.mrc   m/species/fas_89b38e4c/fas_filtsharp.mrc     (contour 0.0072)
  ts029_ribo.star   sel_ribo.star filtered to TS_029.tomostar    (1,825 rows)
  ts029_fas.star    sel_fas30.star filtered to TS_029.tomostar   (38 rows)

Run:  ChimeraX ts029_cell.cxc      (renders ts029_hero.png + ts029_insitu.mp4, then exits)
"""
from pathlib import Path

# emit an absolute-path .cxc so it runs from anywhere (ArtiaX resolves relative to CWD otherwise)


def emit(input_dir: str, out_cxc: str = "ts029_cell.cxc",
         still: str = "ts029_hero.png", movie: str = "ts029_insitu.mp4"):
    d = str(Path(input_dir).resolve())
    lines = [
        "artiax start",
        f"artiax open tomo {d}/TS_029_bin4.mrc",
        f"open {d}/ribo_refined.mrc",           # #2
        f"open {d}/fas_refined.mrc",             # #3
        "volume #2 level 0.010",
        "volume #3 level 0.0072",
        f"open {d}/ts029_ribo.star format relion",   # #1.2.1
        f"open {d}/ts029_fas.star format relion",    # #1.2.2
        "artiax particles #1.2.1 originScaleFactor 4.2",   # sel coords at 4.2 A subtomo px
        "artiax particles #1.2.2 originScaleFactor 4.2",
        "artiax attach #2 toParticleList #1.2.1",
        "artiax attach #3 toParticleList #1.2.2",
        "artiax show #1.2.1 surface",
        "artiax show #1.2.2 surface",
        "hide #1.2.1.3 models",                  # drop the default marker spheres
        "hide #1.2.2.3 models",
        "color #1.2.1 cornflower blue",          # ribosome
        "color #1.2.2 gold",                     # FAS
        "lighting soft",
        "lighting shadows true intensity 0.7",   # shadows ground the molecules -> 3D pop (still only)
        "lighting depthCue true depthCueStart 0.55 depthCueEnd 1.0",
        "graphics silhouettes true width 1.2",   # crisp dark outlines; clean at this tilt (slab edge-on)
        "set bgColor black",
        "view",
        "turn x -55",                            # perspective tilt: molecules read as a 3D cloud that
        #                                          spills past the floating slab (finale aesthetic), while
        #                                          the organelle arc still reads on the right
        f"save {d}/{still} width 1800 height 1300 supersample 3",   # hero still: shadows + full-res surfaces
        "lighting shadows false",                # drop shadows for the movie (per-frame shadow map = slow)
        "volume #2 step 3",                      # coarsen ribosome surfaces -> tractable movie (~1-2 min)
        "volume #3 step 2",
        "movie record size 1300,1000 supersample 1",
        "turn y 1.0 30", "wait 30",              # gentle rock +30 deg (keeps the organelle side facing) ...
        "turn y -1.0 60", "wait 60",             # ... through center to -30 ...
        "turn y 1.0 30", "wait 30",              # ... and back
        f"movie encode {d}/{movie} framerate 24 quality high",
        "exit",
    ]
    Path(out_cxc).write_text("\n".join(lines) + "\n")
    print("wrote", out_cxc)


if __name__ == "__main__":
    import sys
    emit(sys.argv[1] if len(sys.argv) > 1 else ".")
