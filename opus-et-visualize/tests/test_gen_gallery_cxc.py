"""Tests for opus-et-visualize/scripts/gen_gallery_cxc.py and demo shim."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "opus-et-visualize" / "scripts" / "gen_gallery_cxc.py"
DEMO_SHIM = ROOT / "demo" / "gen_gallery_cxc.py"


def test_gallery_cli_emits_lighting_after_tile_and_numeric_order(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    for idx in (10, 2, 1):
        (maps_dir / f"vol_{idx}.mrc").write_bytes(b"")
    out_cxc = tmp_path / "gallery.cxc"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--maps-dir", str(maps_dir), "--out", str(out_cxc), "--columns", "3"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "3 maps" in proc.stdout
    lines = out_cxc.read_text().splitlines()
    opened = [line for line in lines if line.startswith("open ")]
    assert [Path(line.split()[1]).name for line in opened] == ["vol_1.mrc", "vol_2.mrc", "vol_10.mrc"]
    assert lines.index("lighting soft") > lines.index("tile columns 3")


def test_demo_shim_delegates_to_skill_script(tmp_path):
    maps_dir = tmp_path / "maps"
    maps_dir.mkdir()
    (maps_dir / "state0.mrc").write_bytes(b"")
    out_cxc = tmp_path / "shim.cxc"

    subprocess.run(
        [sys.executable, str(DEMO_SHIM), "--maps-dir", str(maps_dir), "--out", str(out_cxc)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "open " in out_cxc.read_text()
