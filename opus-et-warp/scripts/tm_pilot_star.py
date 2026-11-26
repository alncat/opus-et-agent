#!/usr/bin/env python3
"""Write a scored QC STAR from one PyTOM pilot, on the original TM volume grid."""
import argparse
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from warp_settings import tm_coordinate_factor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("particle_xml", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    suffix = "_particles.xml"
    if not args.particle_xml.name.endswith(suffix):
        parser.error(f"expected <series>{suffix}")
    series = args.particle_xml.name[:-len(suffix)]
    root = ET.parse(args.particle_xml).getroot()
    if root.tag != "ParticleList":
        parser.error("expected a PyTOM ParticleList")
    particles = root.findall("Particle")
    # With 1 A per output unit the existing conversion gives the source voxel size in A.
    angpix = tm_coordinate_factor(args.particle_xml, 1.0) if particles else None
    columns = ("MicrographName", "CoordinateX", "CoordinateY", "CoordinateZ", "PixelSize",
               "AutopickFigureOfMerit")
    lines = ["data_particles", "", "loop_", *("_rln" + col for col in columns)]
    for particle in particles:
        position = particle.find("PickPosition")
        score = particle.find("Score")
        if position is None or score is None:
            parser.error("pilot particle is missing its PickPosition or Score")
        values = [float(position.attrib[key]) for key in "XYZ"]
        values.extend((angpix, float(score.attrib["Value"])))
        if not all(math.isfinite(value) for value in values):
            parser.error("pilot particle has a non-finite coordinate or score")
        lines.append(" ".join([series + ".tomostar", *map(str, values)]))
    args.output.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
