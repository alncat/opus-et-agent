"""CLI tests for opus-et-visualize/scripts/gen_artiax_scene.py."""
import pickle
import subprocess
import sys
from pathlib import Path

import pandas as pd
import starfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "opus-et-visualize" / "scripts" / "gen_artiax_scene.py"


def test_gen_artiax_scene_cli_attaches_labels_before_tomo_filter(tmp_path):
    df = pd.DataFrame({
        "rlnMicrographName": ["TS_001.tomostar", "TS_002.tomostar", "TS_002.tomostar"],
        "rlnCoordinateX": [10.0, 20.0, 30.0],
        "rlnCoordinateY": [11.0, 21.0, 31.0],
        "rlnCoordinateZ": [12.0, 22.0, 32.0],
        "rlnAngleRot": [0.0, 10.0, 20.0],
        "rlnAngleTilt": [0.0, 30.0, 40.0],
        "rlnAnglePsi": [0.0, 50.0, 60.0],
    })
    star = tmp_path / "picks.star"
    starfile.write(df, star, overwrite=True)

    labels_path = tmp_path / "labels.pkl"
    with open(labels_path, "wb") as fh:
        pickle.dump([0, 1, 2], fh)

    out_cxc = tmp_path / "scene.cxc"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--star", str(star),
            "--tomogram", "TS_002.mrc",
            "--map", "ref.mrc",
            "--coords-angpix", "7.84",
            "--labels", str(labels_path),
            "--tomo-name", "TS_002.tomostar",
            "--silhouettes",
            "--out-cxc", str(out_cxc),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "2 particles, 2 state(s)" in proc.stdout
    cxc = out_cxc.read_text()
    assert "artiax start" in cxc
    assert "graphics silhouettes true" in cxc
    assert "state1.star" in cxc and "state2.star" in cxc
    assert "state0.star" not in cxc
    assert (tmp_path / "scene_stars" / "state1.star").is_file()
    assert (tmp_path / "scene_stars" / "state2.star").is_file()

    # A second scene in the same directory uses the same state ID for different particles.
    args = [sys.executable, str(SCRIPT), "--star", str(star), "--map", "ref.mrc",
            "--coords-angpix", "7.84"]
    for name in ("TS_001", "TS_002"):
        subprocess.run(args + ["--tomo-name", name + ".tomostar", "--tomogram", name + ".mrc",
                               "--out-cxc", str(tmp_path / (name + ".cxc"))], check=True)
    for name in ("TS_001", "TS_002"):
        star_path = tmp_path / (name + "_stars") / "state0.star"
        assert str(star_path) in (tmp_path / (name + ".cxc")).read_text()
        assert set(starfile.read(star_path)["rlnMicrographName"]) == {name + ".tomostar"}
