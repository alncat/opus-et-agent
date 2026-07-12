import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_artiax_scene as gs


def test_euler_identity_is_identity():
    m = gs.euler_to_matrix(0.0, 0.0, 0.0)
    assert np.allclose(m, np.eye(3))


def test_euler_matrix_is_orthonormal():
    m = gs.euler_to_matrix(37.0, 51.0, 12.0)
    assert np.allclose(m @ m.T, np.eye(3), atol=1e-9)
    assert np.isclose(np.linalg.det(m), 1.0, atol=1e-9)


def test_euler_tilt_90_maps_z_axis():
    # RELION: A(2,2)=cos(tilt); tilt=90 -> A[2,2] ~ 0
    m = gs.euler_to_matrix(0.0, 90.0, 0.0)
    assert abs(m[2, 2]) < 1e-9


def test_emit_cxc_contains_key_commands(tmp_path):
    star = tmp_path / "state0.star"
    star.write_text("dummy")
    out = tmp_path / "scene.cxc"
    text = gs.emit_cxc(
        tomogram="tomo.mrc", map_path="ref.mrc",
        state_stars={0: str(star)}, out_cxc=out,
        coords_angpix=4.2, contour_level=0.012,
    )
    assert out.exists()
    assert "artiax start" in text
    assert "artiax open tomo tomo.mrc" in text
    # particle lists load via plain `open ... format relion`, NOT `artiax open particles`
    assert f"open {star} format relion" in text
    assert "artiax open particles" not in text
    # coords must be rescaled to physical A, and the map attached as the surface
    assert "originScaleFactor 4.2" in text
    assert "artiax attach #2 toParticleList #1.2.1" in text
    assert "volume #2 level 0.012" in text
    assert "ref.mrc" in text


def test_emit_cxc_multistate_ids_and_movie(tmp_path):
    s1, s2 = tmp_path / "k1.star", tmp_path / "k2.star"
    s1.write_text("a"); s2.write_text("b")
    out = tmp_path / "scene.cxc"
    text = gs.emit_cxc(
        tomogram="t.mrc", map_path="m.mrc",
        state_stars={1: str(s1), 2: str(s2)}, out_cxc=out,
        coords_angpix=4.2, contour_sd=5,
        state_colors={1: "tomato", 2: "gold"},
        tomo_transfer=[(-0.008, 0.7), (0.008, 0.7)],
        movie_out=str(tmp_path / "spin.mp4"),
    )
    # two states -> maps #2,#3 attach to lists #1.2.1,#1.2.2
    assert "artiax attach #2 toParticleList #1.2.1" in text
    assert "artiax attach #3 toParticleList #1.2.2" in text
    assert "volume #2 sdLevel 5" in text
    assert "color #1.2.1 tomato" in text and "color #1.2.2 gold" in text
    # translucent tomo slab is a third-plus plain copy -> #4, with the ortho slice hidden
    assert "hide #1.1.1 models" in text
    assert "volume #4 style image" in text
    assert "level -0.008,0.7" in text
    # movie turntable appended
    assert "movie encode" in text and "turn y 2 180" in text


def test_emit_cxc_per_species_maps_and_contours(tmp_path):
    """Two species in one scene: each particle list gets its OWN map + contour."""
    ribo, fas = tmp_path / "ribo.star", tmp_path / "fas.star"
    ribo.write_text("r"); fas.write_text("f")
    out = tmp_path / "insitu.cxc"
    text = gs.emit_cxc(
        tomogram="ts.mrc", map_path=None,
        state_stars={0: str(ribo), 1: str(fas)}, out_cxc=out,
        coords_angpix=13.48,
        state_maps={0: "ribo_ref.mrc", 1: "fas_ref.mrc"},
        state_contours={0: 0.021, 1: 0.055},
        state_colors={0: "tomato", 1: "gold"},
    )
    # each species opens its own map (states sorted -> ribo=#2, fas=#3)
    assert "open ribo_ref.mrc" in text and "open fas_ref.mrc" in text
    # per-state contour, not a shared level
    assert "volume #2 level 0.021" in text and "volume #3 level 0.055" in text
    # right map attaches to right particle list
    assert "artiax attach #2 toParticleList #1.2.1" in text
    assert "artiax attach #3 toParticleList #1.2.2" in text


def test_emit_cxc_hero_aesthetic_opts(tmp_path):
    """Opt-in shadows/silhouettes/still/rock/step produce the finale hero look."""
    ribo, fas = tmp_path / "r.star", tmp_path / "f.star"
    ribo.write_text("r"); fas.write_text("f")
    text = gs.emit_cxc(
        tomogram="ts.mrc", map_path=None,
        state_stars={0: str(ribo), 1: str(fas)}, out_cxc=tmp_path / "h.cxc",
        state_maps={0: "ribo.mrc", 1: "fas.mrc"}, state_contours={0: 0.01, 1: 0.007},
        tilt_x=-55, shadows=True, silhouettes=True,
        still_out=str(tmp_path / "hero.png"),
        movie_out=str(tmp_path / "rock.mp4"), movie_rock=30, movie_step=2,
    )
    assert "lighting shadows true intensity 0.7" in text
    assert "graphics silhouettes true width 1.2" in text
    assert "turn x -55" in text
    assert "save " in text and "hero.png" in text
    # rock, not a 360 turntable; shadows dropped + maps coarsened for the movie
    assert "turn y -1.0 60" in text and "turn y 2 180" not in text
    assert "volume #2 step 2" in text and "volume #3 step 2" in text
    assert "lighting shadows false" in text


def test_emit_cxc_defaults_unchanged_360(tmp_path):
    """With no aesthetic opts, the movie is still the plain 360 turntable."""
    s = tmp_path / "s.star"; s.write_text("s")
    text = gs.emit_cxc(tomogram="t.mrc", map_path="m.mrc", state_stars={0: str(s)},
                       out_cxc=tmp_path / "d.cxc", movie_out=str(tmp_path / "s.mp4"))
    assert "turn y 2 180" in text
    assert "lighting shadows" not in text and "graphics silhouettes" not in text
    # the shared map_path=None must NOT leak into any open command
    assert "open None" not in text
