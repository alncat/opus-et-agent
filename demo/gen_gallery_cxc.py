#!/usr/bin/env python3
"""Compatibility shim — canonical script now lives in opus-et-visualize/scripts/gen_gallery_cxc.py."""
from pathlib import Path
import sys

_SKILL_SCRIPTS = Path(__file__).resolve().parents[1] / "opus-et-visualize" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from gen_gallery_cxc import (  # noqa: E402,F401
    _numkey,
    build_cxc,
    golden_hex,
    main,
    percentile_level,
)

if __name__ == "__main__":
    main()
