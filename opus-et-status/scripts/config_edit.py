"""The ONLY write path in the status server.

Core principle: never write user-supplied bytes. A submitted value is parsed
into its declared type and the config line is REGENERATED from that typed
value, so shell metacharacters cannot reach a file that gets `source`d.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import sources


class ValidationError(ValueError):
    pass


_GRID_RE = re.compile(r"^\d{1,3}x\d{1,3}x\d{1,3}$")
# A new species conf is named species_<slug>.conf. The slug is the ONLY part
# the client controls, so it is restricted to a plain identifier -- the file
# always lands in the known species dir, never at a client-supplied path.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")

# ZAFFINEDIM shares ZDIM's range; bind them so a later edit cannot separate them.
_ZDIM_MIN, _ZDIM_MAX = 1, 16

FFT_PREFERENCE = (
    "Should be a multiple of 8, preferably 16, for FFT efficiency -- but the "
    "sizing rule is round_to_even(DIAMETER / 0.75 / pixel size), so a multiple "
    "of 8 is a preference, not a hard rule.")

# Read-only dataset facts surfaced in the UI but never editable.
DATASET_DISPLAY_KEYS = [
    "ANGPIX", "EXPOSURE", "BINNING_FACTOR", "ALIGN_ANGPIX",
    "TOMO_DIM_X", "TOMO_DIM_Y", "TOMO_DIM_Z", "FRAME_MODE", "FILE_EXTENSION",
    "GPU_PARTITION", "NUM_GPUS", "WORK_DIR",
]


@dataclass(frozen=True)
class KeySpec:
    key: str
    type: str                    # "int" | "float" | "enum" | "grid"
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple = ()
    multiple_of: int = 0
    # True where the spec says "> min" (e.g. LEARNING_RATE), False where it
    # says ">= min" (e.g. MASK_SIGMA). A blanket exclusive minimum would
    # wrongly reject MASK_SIGMA=0.
    exclusive_min: bool = False
    file: str = "species"        # "species" | "pipeline"
    stage: str = ""              # pipeline stage this key governs
    warning: str = ""
    description: str = ""


# Only tuning knobs. NO path-valued keys: paths are both the injection-prone
# case and the destructive-mistake case, and they are set once at setup.
ALLOWLIST = {
    # --- species conf, in pipeline order -------------------------------------
    # Template matching (phase 6)
    # 50 A is about the floor for TM in a binned tomogram. The largest things
    # routinely picked are the nuclear pore (~1200 A) and big viral capsids
    # (~1300 A); most targets are 200-600 A. 1500 leaves headroom without
    # admitting sizes no particle has.
    "DIAMETER": KeySpec("DIAMETER", "float", 50, 1500, stage="Template matching",
                        warning="TM_BOX_SIZE and SUBTOMO_BOX_SIZE are sized from "
                                "DIAMETER (~ DIAMETER/0.75/pixel size). Changing it "
                                "without updating both boxes leaves them inconsistent.",
                        description="Particle diameter in Angstrom."),
    # box = DIAMETER / 0.75 / ALIGN_ANGPIX. TM runs on binned tomograms, so even
    # a 2000 A assembly at ALIGN_ANGPIX ~10 A lands near 260 px.
    "TM_BOX_SIZE": KeySpec("TM_BOX_SIZE", "int", 16, 256, multiple_of=2,
                           stage="Template matching", warning=FFT_PREFERENCE,
                           description="TM template/mask box at ALIGN_ANGPIX; "
                                       "~ round_to_even(DIAMETER / 0.75 / ALIGN_ANGPIX)."),
    # The real ceiling is the cross-key rule MASK_RADIUS + MASK_SIGMA <
    # TM_BOX_SIZE/2; 128 is just the largest that could satisfy it.
    "MASK_RADIUS": KeySpec("MASK_RADIUS", "int", 1, 128, stage="Template matching",
                           description="TM sphere mask radius in pixels; "
                                       "MASK_RADIUS + MASK_SIGMA must be < TM_BOX_SIZE/2."),
    # SKILL: "Keep MASK_SIGMA=1 unless the user asks for a softer edge."
    "MASK_SIGMA": KeySpec("MASK_SIGMA", "float", 0.0, 5.0, stage="Template matching",
                          description="TM mask soft-edge sigma in px; keep at 1 "
                                      "unless a softer edge is wanted."),
    "TEMPLATE_INVERT": KeySpec("TEMPLATE_INVERT", "enum", choices=("0", "1"),
                               stage="Template matching",
                               description="Invert template contrast."),
    "TEMPLATE_MIRROR": KeySpec("TEMPLATE_MIRROR", "enum", choices=("0", "1"),
                               stage="Template matching",
                               description="Mirror the template (handedness)."),
    # The real bound is the tomogram depth, checked cross-key against
    # TOMO_DIM_Z / BINNING_FACTOR. This static ceiling is only a fallback for
    # when those are not set.
    "DEFAULT_Z_START": KeySpec("DEFAULT_Z_START", "int", 0, 1024,
                               stage="Template matching",
                               description="Z index where the TM search region "
                                           "starts; must lie inside the binned "
                                           "tomogram (TOMO_DIM_Z / BINNING_FACTOR)."),
    "TM_SPLIT_X": KeySpec("TM_SPLIT_X", "int", 1, 8, stage="Template matching",
                          description="TM job split along X."),
    "TM_SPLIT_Y": KeySpec("TM_SPLIT_Y", "int", 1, 8, stage="Template matching",
                          description="TM job split along Y."),
    "TM_SPLIT_Z": KeySpec("TM_SPLIT_Z", "int", 1, 8, stage="Template matching",
                          description="TM job split along Z."),

    # Candidate extraction (phase 6d)
    # SKILL: "Over-pick (5000-10000 for crowded specimens, lower for sparse)."
    "NUM_CANDIDATES": KeySpec("NUM_CANDIDATES", "int", 100, 20000, stage="Extraction",
                              description="Peaks per tomogram; 5000-10000 for "
                                          "crowded specimens, lower for sparse."),
    "EXTRACT_MASK_RADIUS": KeySpec(
        "EXTRACT_MASK_RADIUS", "int", 1, 128, stage="Extraction",
        warning="This is the non-max-suppression radius and is intentionally "
                "LARGER than the sphere MASK_RADIUS. Do not derive it from the "
                "mask formula: too small gives duplicate hits, too large drops "
                "adjacent particles.",
        description="Non-max-suppression radius in pixels."),
    # A cross-correlation, so a negative cutoff is legitimate.
    "MIN_SCORE": KeySpec("MIN_SCORE", "float", -1.0, 1.0, stage="Extraction",
                         description="TM score cutoff (cross-correlation)."),
    "MAX_TS": KeySpec("MAX_TS", "int", 0, 10000, stage="Extraction",
                      description="Limit tilt series (0 = all)."),

    # Subtomogram export (phase 7)
    # A pixel size in Angstrom: cryo-EM detectors land around 0.5-2 A raw, and
    # a subtomo export bins that a little. 1000 A was not a pixel size.
    "OUTPUT_ANGPIX": KeySpec("OUTPUT_ANGPIX", "float", 0.3, 20.0,
                             stage="Export",
                             description="Exported subtomogram pixel size; must be >= raw ANGPIX."),
    # Training memory scales with the cube of the box. 104 here, 176 in the
    # docs' example; 400 covers a 1500 A particle at a coarse pixel and nothing
    # beyond what could actually train.
    "SUBTOMO_BOX_SIZE": KeySpec("SUBTOMO_BOX_SIZE", "int", 32, 400, multiple_of=2,
                                stage="Export", warning=FFT_PREFERENCE,
                                description="Subtomogram box at OUTPUT_ANGPIX; "
                                            "~ round_to_even(DIAMETER / 0.75 / OUTPUT_ANGPIX)."),

    # OPUS-ET training (phase 8)
    # No evenness or multiple-of rule is documented for TEMPLATERES; do not
    # invent one, or a legitimate value gets rejected.
    "TEMPLATERES": KeySpec("TEMPLATERES", "int", 32, 512, stage="Training",
                           description="Decoder output size; ~ SUBTOMO_BOX_SIZE "
                                       "x DOWNSAMPLE_FRAC / 0.75."),
    "ZDIM": KeySpec("ZDIM", "int", _ZDIM_MIN, _ZDIM_MAX, stage="Training",
                    description="Latent dimension; 8 default, under 16. Raise "
                                "for more heterogeneous datasets."),
    "ZAFFINEDIM": KeySpec("ZAFFINEDIM", "int", _ZDIM_MIN, _ZDIM_MAX, stage="Training",
                          description="Affine/pose latent dimension; 6 typical. "
                                      "Same range as ZDIM."),
    "NUM_EPOCHS": KeySpec("NUM_EPOCHS", "int", 20, 80, stage="Training",
                          description="Training epochs; 40 typical, useful range "
                                      "20-80. Each epoch is expensive -- 40 ran "
                                      "for days on 4 GPUs."),
    # Memory-bound, so there is no universal ceiling -- the usable maximum
    # depends on box size and GPU. Warn rather than invent a hard cap.
    "BATCH_SIZE": KeySpec("BATCH_SIZE", "int", 1, 512, stage="Training",
                          warning="Bounded by GPU memory, not by a fixed limit: "
                                  "what fits depends on SUBTOMO_BOX_SIZE, "
                                  "TEMPLATERES and the card. 12 fits a 104^3 box "
                                  "on 4 GPUs; too large simply OOMs at startup.",
                          description="Batch size; GPU-memory bound."),
    "LEARNING_RATE": KeySpec(
        "LEARNING_RATE", "float", 1e-6, 5e-4, stage="Training",
        warning="Belongs in the 1e-5 range (4e-5 here). Orders of magnitude "
                "larger will not converge, and training fails silently -- it "
                "burns days of GPU and returns a useless model.",
        description="Adam learning rate; order of 1e-5."),
    "DOWNSAMPLE_FRAC": KeySpec("DOWNSAMPLE_FRAC", "float", 0.0, 1.0, exclusive_min=True,
                               stage="Training",
                               description="Encoder downsample fraction (0 < x <= 1)."),
    "BETA_CONTROL": KeySpec("BETA_CONTROL", "float", 0.0, 4.0, stage="Training",
                            description="KL beta control weight; 0.5 typical, "
                                        "should not exceed 4."),
    "LAMB": KeySpec("LAMB", "float", 0.0, 2.0, stage="Training",
                    description="Regularisation weight; 0.5 typical, "
                                "should stay under 2."),
    "BFACTOR": KeySpec("BFACTOR", "float", 0.0, 10.0, stage="Training",
                       description="B-factor applied during training; 3.75 "
                                   "typical, under 10."),
    # 1.0 would hold out everything and leave no training data at all.
    "VAL_FRAC": KeySpec("VAL_FRAC", "float", 0.0, 0.5, stage="Training",
                        description="Fraction held out for validation; 0.1 typical."),

    # Training mask (phase 8a)
    "MASK_RADIUS_FRACTION": KeySpec("MASK_RADIUS_FRACTION", "float", 0.0, 1.0,
                                    exclusive_min=True, stage="Training mask",
                                    description="Sphere radius as a fraction of "
                                                "min box dim / 2."),
    "MASK_SOFT_EDGE": KeySpec("MASK_SOFT_EDGE", "int", 0, 64, stage="Training mask",
                              description="Training mask soft edge in voxels."),

    # --- pipeline.conf: processing choices for phases not yet run ------------
    # Deliberately absent: all paths, and acquisition facts (ANGPIX, EXPOSURE,
    # TOMO_DIM_*, MAP_ANGPIX) which describe the data rather than tune it.
    "BINNING_FACTOR": KeySpec(
        "BINNING_FACTOR", "int", 1, 16, file="pipeline", stage="Processing",
        warning="Load-bearing: ALIGN_ANGPIX derives from it, the TM template and "
                "box sizes are built at this binning, and every existing tomogram "
                "was reconstructed with it. Changing it mid-run makes prior "
                "reconstructions inconsistent with new ones.",
        description="Tilt-series binning for alignment/reconstruction."),
    "CTF_DEFOCUS_MAX": KeySpec("CTF_DEFOCUS_MAX", "float", 0.0, 50.0, exclusive_min=True,
                               file="pipeline", stage="Processing",
                               description="CTF search max defocus (um)."),
    "CTF_RANGE_MAX": KeySpec("CTF_RANGE_MAX", "float", 0.0, 50.0, exclusive_min=True,
                             file="pipeline", stage="Processing",
                             description="CTF fit resolution range max."),
    "CTF_WINDOW": KeySpec("CTF_WINDOW", "int", 64, 2048, multiple_of=8, file="pipeline",
                          stage="Processing", description="CTF estimation window (px)."),
    "CTF_GRID": KeySpec("CTF_GRID", "grid", file="pipeline", stage="Processing",
                        description="CTF grid, e.g. 2x2x1."),
    "MOTION_GRID": KeySpec("MOTION_GRID", "grid", file="pipeline", stage="Processing",
                           description="Motion grid, e.g. 1x1x3."),
    "MIN_INTENSITY": KeySpec("MIN_INTENSITY", "float", 0.0, 1.0, file="pipeline",
                             stage="Processing",
                             description="Minimum tilt intensity to keep."),
}


# Order the stages the way the pipeline runs them.
STAGE_ORDER = ["Processing", "Template matching", "Extraction", "Export",
               "Training", "Training mask"]


def coerce(spec, raw):
    """Parse into the declared type. This is what kills injection: a shell
    metacharacter cannot parse as an int, float, or enum member."""
    text = str(raw).strip()
    if spec.type == "grid":
        # Digits and 'x' only -- no shell metacharacter can survive this.
        if not _GRID_RE.match(text):
            raise ValidationError(f"{spec.key} must look like 2x2x1, got {text!r}")
        return text
    if spec.type == "enum":
        if text not in spec.choices:
            raise ValidationError(f"{spec.key} must be one of {spec.choices}, got {text!r}")
        return text
    try:
        value = int(text) if spec.type == "int" else float(text)
    except ValueError:
        raise ValidationError(f"{spec.key} must be {spec.type}, got {text!r}")
    if spec.minimum is not None:
        if spec.exclusive_min and value <= spec.minimum:
            raise ValidationError(f"{spec.key} must be > {spec.minimum}, got {value}")
        if not spec.exclusive_min and value < spec.minimum:
            raise ValidationError(f"{spec.key} must be >= {spec.minimum}, got {value}")
    if spec.maximum is not None and value > spec.maximum:
        raise ValidationError(f"{spec.key} must be <= {spec.maximum}, got {value}")
    if spec.multiple_of and value % spec.multiple_of != 0:
        raise ValidationError(
            f"{spec.key} must be a multiple of {spec.multiple_of}, got {value}")
    return value


def _num(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _box_pixel_size(box_key, pipeline_values, species_values):
    """The pixel size each box is sized at, and its name."""
    if box_key == "TM_BOX_SIZE":
        # TM runs on the binned reconstructions: ALIGN_ANGPIX = ANGPIX x BINNING.
        angpix = _num(pipeline_values.get("ANGPIX"))
        binning = _num(pipeline_values.get("BINNING_FACTOR"))
        px = angpix * binning if angpix and binning else None
        return px, "ALIGN_ANGPIX (ANGPIX x BINNING_FACTOR)"
    return _num(species_values.get("OUTPUT_ANGPIX")), "OUTPUT_ANGPIX"


def _check_box_covers_particle(box_key, box, diameter, px, px_label):
    """A box must physically contain the particle -- the same rule validate.sh
    enforces. The documented sizing is round_to_even(DIAMETER / 0.75 / px),
    with DIAMETER / 0.5 / px for high-res or strong-CTF cases, so the
    recommended value is reported rather than imposed."""
    if not (box and diameter and px):
        return
    covered = box * px
    if covered < diameter:
        raise ValidationError(
            f"{box_key} {box:g} at {px_label} {px:g} A spans {covered:.0f} A, "
            f"smaller than the {diameter:g} A particle. The sizing rule gives "
            f"~{diameter / 0.75 / px:.0f} px (round to even; up to "
            f"~{diameter / 0.5 / px:.0f} for high-res or strong CTF).")


def recommend(key, pipeline_values, species_values=None):
    """The value the documented rule gives for `key`, as display text.

    A static range is close to useless for a derived quantity -- '32 to 1024'
    tells you nothing, while '~104 from DIAMETER/0.75/OUTPUT_ANGPIX' tells you
    what to type. Returns None when the inputs are not available.
    """
    species_values = species_values or {}
    diameter = _num(species_values.get("DIAMETER"))

    if key in ("TM_BOX_SIZE", "SUBTOMO_BOX_SIZE"):
        px, label = _box_pixel_size(key, pipeline_values, species_values)
        if diameter and px:
            lo = diameter / 0.75 / px
            hi = diameter / 0.5 / px
            return (f"~{lo:.0f} (up to ~{hi:.0f} for high-res), "
                    f"from {diameter:g} A / 0.75 / {label.split(' ')[0]} {px:g}")
    if key == "MASK_RADIUS":
        px, _ = _box_pixel_size("TM_BOX_SIZE", pipeline_values, species_values)
        box = _num(species_values.get("TM_BOX_SIZE"))
        sigma = _num(species_values.get("MASK_SIGMA")) or 0.0
        if diameter and px:
            note = f"~{diameter / 2 / px:.0f}, from {diameter:g} A / 2 / {px:g}"
            if box:
                note += f"; must stay under {box / 2 - sigma:.0f} (box/2 - sigma)"
            return note
    if key == "EXTRACT_MASK_RADIUS":
        sphere = _num(species_values.get("MASK_RADIUS"))
        if sphere:
            return f"> {sphere:g} (the sphere MASK_RADIUS)"
    if key == "OUTPUT_ANGPIX":
        angpix = _num(pipeline_values.get("ANGPIX"))
        if angpix:
            return f">= {angpix:g} (raw ANGPIX); a light bin of it"
    if key == "DEFAULT_Z_START":
        depth = _num(pipeline_values.get("TOMO_DIM_Z"))
        binning = _num(pipeline_values.get("BINNING_FACTOR"))
        if depth and binning:
            return f"< {depth / binning:.0f} (TOMO_DIM_Z / BINNING_FACTOR)"
    return None


def cross_key_check(key, value, pipeline_values, species_values=None):
    """Domain rules spanning keys (and files)."""
    species_values = species_values or {}

    if key in ("TM_BOX_SIZE", "SUBTOMO_BOX_SIZE"):
        px, label = _box_pixel_size(key, pipeline_values, species_values)
        _check_box_covers_particle(key, float(value),
                                   _num(species_values.get("DIAMETER")), px, label)

    if key == "DIAMETER":
        # Raising the diameter can leave the existing boxes too small, which is
        # exactly what this key's warning is about -- so enforce it.
        for box_key in ("TM_BOX_SIZE", "SUBTOMO_BOX_SIZE"):
            px, label = _box_pixel_size(box_key, pipeline_values, species_values)
            _check_box_covers_particle(box_key, _num(species_values.get(box_key)),
                                       float(value), px, label)

    if key == "MASK_RADIUS":
        box = species_values.get("TM_BOX_SIZE")
        sigma = species_values.get("MASK_SIGMA")
        if box:
            limit = float(box) / 2.0
            total = float(value) + (float(sigma) if sigma else 0.0)
            if total >= limit:
                raise ValidationError(
                    f"MASK_RADIUS + MASK_SIGMA ({total:g}) must be < TM_BOX_SIZE/2 "
                    f"({limit:g}), or the soft edge falls outside the template box.")
    if key == "DEFAULT_Z_START":
        # TM runs on the binned reconstructions, so the usable Z range is the
        # unbinned depth divided by the binning factor.
        depth, binning = pipeline_values.get("TOMO_DIM_Z"), pipeline_values.get("BINNING_FACTOR")
        try:
            nz = float(depth) / float(binning) if depth and binning else None
        except (TypeError, ValueError, ZeroDivisionError):
            nz = None
        if nz and float(value) >= nz:
            raise ValidationError(
                f"DEFAULT_Z_START ({value:g}) must be inside the tomogram: "
                f"TOMO_DIM_Z {depth} / BINNING_FACTOR {binning} = {nz:g} slices.")
    if key == "EXTRACT_MASK_RADIUS":
        sphere = species_values.get("MASK_RADIUS")
        if sphere and float(value) <= float(sphere):
            raise ValidationError(
                f"EXTRACT_MASK_RADIUS ({value}) must exceed the sphere "
                f"MASK_RADIUS ({sphere}): it is the non-max-suppression radius, "
                "and too small merges nothing, giving duplicate hits.")
    if key == "OUTPUT_ANGPIX":
        raw = pipeline_values.get("ANGPIX")
        if raw:
            angpix = float(raw)
            if float(value) < angpix:
                raise ValidationError(
                    f"OUTPUT_ANGPIX ({value}) is finer than raw ANGPIX ({angpix}) "
                    "-- that is invalid upsampling."
                )


def _fmt(value, integer_style=False):
    """Render a typed value, following the spelling already in the file.

    ``DIAMETER=320`` must not become ``320.0`` -- scripts do shell arithmetic
    on it and that fails on a float spelling. But ``OUTPUT_ANGPIX="4.2"`` set
    to 5 should stay ``5.0``, because that field is written as a decimal.
    """
    if integer_style and isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def render_line(key, value, comment, quoted=True, integer_style=False):
    """Regenerate the line from the typed value, keeping the file's own
    quoting style so an edit does not churn unrelated formatting."""
    shown = _fmt(value, integer_style)
    body = f'{key}="{shown}"' if quoted else f"{key}={shown}"
    return f"{body}                     {comment}".rstrip() if comment else body


def apply_edit(text, key, value):
    """PURE: return the new file text with exactly one line replaced."""
    spec = ALLOWLIST.get(key)
    if spec is None:
        raise ValidationError(f"{key} is not an editable key")
    typed = coerce(spec, value)
    cfg = sources.parse_config(text)
    if key not in cfg:
        raise ValidationError(f"{key} is not present in this config file")
    entry = cfg[key]
    lines = text.splitlines()
    # Preserve the existing quoting style: this file mixes ZDIM=8 with
    # OUTPUT_ANGPIX="4.2", and both are valid bash.
    after_eq = lines[entry.lineno].split("=", 1)[1].lstrip()
    quoted = after_eq.startswith(('"', "'"))
    # 320 stays 320; 4.2 stays a decimal.
    integer_style = "." not in entry.value and "e" not in entry.value.lower()
    lines[entry.lineno] = render_line(key, typed, entry.comment, quoted=quoted,
                                      integer_style=integer_style)
    trailing = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + trailing


class ConfigEditor:
    def __init__(self, client, species_conf_path, pipeline_conf_path, validate_cmd):
        self.client = client
        self.species_conf_path = species_conf_path
        self.pipeline_conf_path = pipeline_conf_path
        self.validate_cmd = list(validate_cmd)

    @property
    def _species_dir(self):
        return self.species_conf_path.rsplit("/", 1)[0]

    def available_species(self):
        """Species confs sitting beside the active one. This list is the
        allowlist for switching -- a name from the client is matched against
        it and never used to build a path directly."""
        res = self.client.run(
            ["sh", "-c", 'ls -1 "$1"/species*.conf 2>/dev/null || true',
             "_", self._species_dir])
        names = [line.rsplit("/", 1)[-1].strip()
                 for line in res.stdout.splitlines() if line.strip()]
        # species.example.conf matches the glob but is the template shipped with
        # the skill, not a real species -- offering it would invite editing it.
        names = [n for n in names if not n.endswith(".example.conf")]
        return sorted(set(names))

    def _species_path(self, species=None):
        if species is None:
            return self.species_conf_path
        if species not in self.available_species():
            raise ValidationError(f"unknown species conf: {species!r}")
        return f"{self._species_dir}/{species}"

    def _path_for(self, spec, species=None):
        return (self.pipeline_conf_path if spec.file == "pipeline"
                else self._species_path(species))

    def current_values(self, species=None):
        """Current values of the editable keys, across both conf files. Read on
        demand (configs change rarely), so it is not a polled source."""
        parsed = {
            "species": sources.parse_config(
                self.client.read_file(self._species_path(species))),
            "pipeline": sources.parse_config(self.client.read_file(self.pipeline_conf_path)),
        }
        out = {}
        for key, spec in ALLOWLIST.items():
            cfg = parsed[spec.file]
            if key in cfg:
                out[key] = cfg[key].value
        return out

    def recommendations(self, species=None):
        """Per-key derived guidance for the form, e.g. the box size the sizing
        rule gives for the current DIAMETER and pixel size."""
        species_vals = self.current_values(species)
        pipeline_vals = dict(self.dataset_values())
        pipeline_vals.update({k: v for k, v in species_vals.items()
                              if ALLOWLIST[k].file == "pipeline"})
        out = {}
        for key in ALLOWLIST:
            try:
                text = recommend(key, pipeline_vals, species_vals)
            except Exception:
                text = None
            if text:
                out[key] = text
        return out

    def dataset_values(self):
        """Read-only dataset facts for display. Never writable.

        Values the conf derives from others (ALIGN_ANGPIX) are computed
        arithmetically -- showing the raw shell expression is useless, and
        sourcing the file to expand it is not something a read-only viewer
        should do.
        """
        cfg = sources.parse_config(self.client.read_file(self.pipeline_conf_path))
        allvals = {k: v.value for k, v in cfg.items()}
        out = {}
        for k in DATASET_DISPLAY_KEYS:
            if k not in cfg:
                continue
            raw = cfg[k].value
            out[k] = sources.resolve_derived(raw, allvals) or raw if "$" in (raw or "") else raw
        return out

    def create_species(self, slug, values=None, template=None):
        """Clone a species conf into species_<slug>.conf with `values` applied.

        Every value is validated BEFORE anything is written, so a bad input
        leaves no file behind. A create has no prior version to restore, so if
        validate.sh rejects the result the new file is deleted.
        """
        if not _SLUG_RE.match(slug or ""):
            raise ValidationError(
                "species name must be 1-32 characters of letters, digits, _ or -")
        filename = f"species_{slug}.conf"
        if filename in self.available_species():
            raise ValidationError(f"{filename} already exists")

        text = self.client.read_file(self._species_path(template))
        for key, raw in (values or {}).items():
            spec = ALLOWLIST.get(key)
            if spec is None or spec.file != "species":
                raise ValidationError(f"{key} is not an editable species key")
            text = apply_edit(text, key, raw)      # raises before any write

        target = f"{self._species_dir}/{filename}"
        self.client.write_file(target, text)
        res = self.client.run(self.validate_cmd + ["--species", target])
        if res.rc != 0:
            self.client.run(["rm", "-f", "--", target])
            raise ValidationError(
                f"validate.sh rejected the new conf, removed it: {res.stdout.strip()}")
        return filename

    def apply(self, key, raw, species=None):
        spec_for_path = ALLOWLIST.get(key)
        if spec_for_path is None:
            raise ValidationError(f"{key} is not an editable key")
        target = self._path_for(spec_for_path, species)
        original = self.client.read_file(target)
        pipeline_vals = {
            k: v.value
            for k, v in sources.parse_config(
                self.client.read_file(self.pipeline_conf_path)
            ).items()
        }
        spec = ALLOWLIST.get(key)
        if spec is None:
            raise ValidationError(f"{key} is not an editable key")
        species_vals = {k: v.value for k, v in
                        sources.parse_config(original).items()}
        cross_key_check(key, coerce(spec, raw), pipeline_vals, species_vals)

        new_text = apply_edit(original, key, raw)

        backup = f"{target}.bak.{time.strftime('%Y%m%dT%H%M%S')}"
        self.client.write_file(backup, original)
        self.client.write_file(target, new_text)

        res = self.client.run(self.validate_cmd)
        if res.rc != 0:
            self.client.write_file(target, original)  # roll back
            raise ValidationError(
                f"validate.sh rejected the change, rolled back: {res.stdout.strip()}"
            )
        return backup
