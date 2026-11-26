#!/usr/bin/env python3
"""Build a standalone OPUS-ET plugin ZIP from this checkout."""
import argparse
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / ".agents/plugins/opus-et"


def build(output):
    skills = sorted(p.name for p in (PLUGIN / "skills").iterdir() if not p.name.startswith("."))
    if not skills:
        raise ValueError("No linked skill directories found in the checkout")
    # Package the working versions of version-controlled skill files. A configured
    # checkout can also contain local pipeline.conf files, run data and caches.
    listing = subprocess.check_output(["git", "ls-files", "-z", "--", *skills], cwd=ROOT)
    files = [(ROOT / path, "skills/" + path) for path in os.fsdecode(listing).split("\0") if path]
    for skill in skills:
        if (ROOT / skill / "SKILL.md", f"skills/{skill}/SKILL.md") not in files:
            raise ValueError(f"Missing tracked SKILL.md for {skill}")
    for name in ("plugin.json", "hooks.json", "mcp_config.json", "rules/AGENTS.md",
                 "scripts/guard_sbatch.py", "scripts/mcp_server.py"):
        files.append((PLUGIN / name, name))

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as staging:
        archive = Path(staging) / "opus-et.zip"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
            for source, member in files:
                bundle.write(source, member)  # follow the shared rules symlink
        archive.replace(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="destination ZIP file")
    args = parser.parse_args()
    build(args.output)
    print(args.output)
