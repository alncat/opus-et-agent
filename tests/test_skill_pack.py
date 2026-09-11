"""Pack-level checks on the five SKILL.md files.

These guard the shape of the skills, not their content. A description that
summarises the workflow becomes a shortcut agents follow instead of reading
the body; a session log inside a reference doc explains nothing to the next
user; and a hub that does not know one of its own skills exists makes the
README lie.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ["opus-et-conductor", "opus-et-warp", "opus-et-analysis",
          "opus-et-visualize", "opus-et-status"]


def _frontmatter(skill):
    text = (ROOT / skill / "SKILL.md").read_text()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, f"{skill}: no YAML frontmatter"
    return m.group(1), text[m.end():]


def _description(skill):
    fm, _ = _frontmatter(skill)
    m = re.search(r"^description:\s*(.*)$", fm, re.M)
    assert m, f"{skill}: no description"
    return m.group(1).strip().strip('"')


@pytest.mark.parametrize("skill", SKILLS)
def test_description_states_when_to_use_not_what_it_does(skill):
    """'Use when ...' puts the triggering conditions first. A description that
    opens by narrating the workflow is one an agent will follow instead of
    the body."""
    assert _description(skill).startswith("Use when"), _description(skill)[:80]


@pytest.mark.parametrize("skill", SKILLS)
def test_description_is_trigger_sized(skill):
    d = _description(skill)
    assert len(d) <= 500, f"{skill}: {len(d)} chars"
    fm, _ = _frontmatter(skill)
    assert len(fm) <= 1024


# One realistic ask per skill and the words an agent would search for. The
# intended skill's description must carry every term -- that is what makes it
# findable without reading all five bodies.
DISCOVERY = [
    ("opus-et-conductor", "where did my run stop, what gate is waiting on me",
     ["run", "gate"]),
    ("opus-et-conductor", "process this dataset end to end on the cluster",
     ["end-to-end", "resume"]),
    ("opus-et-warp", "template matching with PyTOM on my tomostar files",
     ["template matching", "tomostar"]),
    ("opus-et-warp", "export subtomograms with ts_export_particles",
     ["subtomogram", "ts_export_particles"]),
    ("opus-et-analysis", "k-means on the latent codes from epoch 39, volumes per cluster",
     ["k-means", "latent", "volume"]),
    ("opus-et-analysis", "gold-standard FSC between the two half maps",
     ["FSC"]),
    ("opus-et-visualize", "place the refined map at every pose inside the tomogram in ChimeraX",
     ["pose", "ChimeraX"]),
    ("opus-et-visualize", "show that the picks land on real density in the raw tomogram",
     ["picks", "raw"]),
    ("opus-et-status", "is my SLURM job still running or did it fail",
     ["SLURM", "fail"]),
    ("opus-et-status", "which tilt series has the worst CTF fit",
     ["tilt series", "CTF"]),
]


@pytest.mark.parametrize("skill,ask,terms", DISCOVERY,
                         ids=[a[:40] for _, a, _ in DISCOVERY])
def test_a_realistic_ask_finds_its_skill(skill, ask, terms):
    d = _description(skill).lower()
    missing = [t for t in terms if t.lower() not in d]
    assert not missing, f"{skill} description lacks {missing} for: {ask!r}"


def test_conductor_knows_the_dashboard_exists():
    """The README says the conductor can launch the dashboard alongside a run.
    The conductor's own instructions must actually say so."""
    _, body = _frontmatter("opus-et-conductor")
    assert "opus-et-status" in body


@pytest.mark.parametrize("skill", SKILLS)
def test_no_session_narrative_in_skill_body(skill):
    """A skill is a reference for the next user, not a log of how it was
    built. Changelogs and 'this session' notes belong in git history."""
    _, body = _frontmatter(skill)
    assert not re.search(r"^## Status\s*$", body, re.M), f"{skill}: '## Status' section"
    assert "this session" not in body.lower(), f"{skill}: 'this session'"


def test_status_skill_does_not_argue_from_one_dataset():
    """Numbers from the run it was developed against ('all fourteen running
    jobs', 'this run used 600, 800, 5000') were true that day and explain
    nothing to the next user. The reasoning must stand without them."""
    _, body = _frontmatter("opus-et-status")
    for marker in ("fourteen", "58 finished", "600, 800, 5000", "9305",
                   "120 of them", "built against", "in this run"):
        assert marker not in body, f"opus-et-status: {marker!r}"


# 500 words is the rubric's target for a general skill; these are
# technology-specific and reference-heavy, so the pack's ceiling is 1200 --
# an overview plus a quick reference, with anything heavier in references/.
SKILL_BODY_CEILING = 1200


@pytest.mark.parametrize("skill", SKILLS)
def test_skill_body_fits_the_ceiling(skill):
    _, body = _frontmatter(skill)
    words = len(body.split())
    assert words <= SKILL_BODY_CEILING, f"{skill}: {words} words"


@pytest.mark.parametrize("skill", SKILLS)
def test_references_named_by_the_skill_exist(skill):
    _, body = _frontmatter(skill)
    for ref in set(re.findall(r"references/[A-Za-z0-9_.-]+\.md", body)):
        assert (ROOT / skill / ref).exists(), f"{skill}: {ref} does not exist"


# pipeline.conf / species.conf is a convention four skills depend on. It gets
# one home; everything that restates it points there, so the restatements
# cannot drift from the definition.
CONF_HOME = "opus-et-warp/references/configuration.md"
CONF_RESTATERS = {
    "opus-et-warp/SKILL.md": "references/configuration.md",
    "opus-et-warp/references/scripts.md": "references/configuration.md",
    "opus-et-status/SKILL.md": CONF_HOME,
    "opus-et-conductor/references/gate_protocols.md": CONF_HOME,
}


def test_conf_convention_has_one_home():
    home = ROOT / CONF_HOME
    assert home.exists(), f"{CONF_HOME} missing"
    text = home.read_text()
    # the facts a reader needs, all in the one file
    for fact in ('SKILL_DIR="$(pwd)"', "SPECIES_CONF=", 'SCRIPT_DIR="${SKILL_DIR:-',
                 "pipeline.example.conf", "species.example.conf"):
        assert fact in text, f"{CONF_HOME} lacks {fact!r}"


@pytest.mark.parametrize("path,pointer", CONF_RESTATERS.items(),
                         ids=list(CONF_RESTATERS))
def test_every_restatement_points_at_the_home(path, pointer):
    text = (ROOT / path).read_text()
    assert "pipeline.conf" in text and "species.conf" in text, "probe is stale"
    assert pointer in text, f"{path} restates the conf split without citing {pointer}"
