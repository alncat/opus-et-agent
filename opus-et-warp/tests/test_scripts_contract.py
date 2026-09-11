"""Contract tests for the 27 SLURM scripts and the manifest that drives them.

The conductor submits every phase as `sbatch --export=ALL,SKILL_DIR=$(pwd)
scripts/<name>` and reads the result from `logs/<name>_<jobid>.out`. A script
that drifts from that contract does not fail loudly -- it runs with the wrong
config, or writes its log somewhere the dashboard cannot find. These checks
run with no cluster and no toolchain.
"""
import py_compile
import re
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
SLURM = sorted(SCRIPTS.glob("*.slurm"))
PY = sorted(SCRIPTS.glob("*.py"))


def _manifest():
    """manifest.yml has one shape -- phases -> fields -> scalars or lists --
    and validate.sh parses it with a bash function, so this stays in step
    with that rather than pulling in a YAML library for one test."""
    phases, phase, field = {}, None, None
    for raw in (SCRIPTS / "manifest.yml").read_text().splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        if indent == 2 and body.endswith(":"):
            phase = body[:-1].strip('"'); phases[phase] = {}; field = None
        elif indent == 4 and phase is not None:
            key, _, val = body.partition(":")
            val = val.strip().strip('"')
            phases[phase][key] = val if val else []
            field = key if not val else None
        elif indent == 6 and field is not None and body.startswith("- "):
            phases[phase][field].append(body[2:].strip().strip('"'))
    assert phases, "manifest.yml parsed to nothing"
    return phases


@pytest.mark.parametrize("script", [s.name for s in SLURM] + ["validate.sh"])
def test_shell_script_parses(script):
    path = SCRIPTS / script if script.endswith(".slurm") else SKILL / script
    proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("script", [p.name for p in PY])
def test_python_script_compiles(script):
    py_compile.compile(str(SCRIPTS / script), doraise=True)


@pytest.mark.parametrize("script", [s.name for s in SLURM])
def test_slurm_script_reads_its_config_the_standard_way(script):
    """SKILL_DIR is how sbatch tells a script where pipeline.conf is; a script
    that derives its directory any other way silently runs with defaults."""
    text = (SCRIPTS / script).read_text()
    assert 'SCRIPT_DIR="${SKILL_DIR:-' in text, "does not honour SKILL_DIR"
    assert '/pipeline.conf"' in text, "does not source pipeline.conf"


@pytest.mark.parametrize("script", [s.name for s in SLURM])
def test_slurm_script_logs_by_job_id_under_logs(script):
    """The dashboard and the log panel find a job's output by globbing
    logs/*_<jobid>.out; any other --output path makes the job invisible."""
    text = (SCRIPTS / script).read_text()
    assert re.search(r"^#SBATCH --output=logs/\S*%j", text, re.M), "--output"
    assert re.search(r"^#SBATCH --error=logs/\S*%j", text, re.M), "--error"
    assert re.search(r"^#SBATCH --job-name=", text, re.M), "--job-name"


def test_every_manifest_script_exists():
    missing = [(ph, s) for ph, spec in _manifest().items()
               for s in spec.get("scripts", []) if not (SCRIPTS / s).exists()]
    assert not missing, missing


def test_species_conf_sourcing_matches_the_manifest():
    """A phase that declares requires_species promises SPECIES_CONF variables
    to its scripts. A script in such a phase that never sources species.conf
    runs with those variables unset -- and a pre-species script that does
    source it fails on a fresh run where species.conf does not exist yet."""
    phases = _manifest()
    problems = []
    for ph, spec in phases.items():
        need = str(spec.get("requires_species", "")).lower() == "true"
        for s in spec.get("scripts", []):
            if not s.endswith(".slurm"):
                continue
            sources = "SPECIES_CONF" in (SCRIPTS / s).read_text()
            if need != sources:
                problems.append((ph, s, "needs species" if need else "pre-species",
                                 "sources it" if sources else "never sources it"))
    assert not problems, problems


def test_every_slurm_script_is_reachable():
    """A script nothing refers to is one the conductor can never submit and
    validate.sh can never check. Three things may legitimately name a script:
    a manifest phase (the per-phase loop submits it), the gate protocols (a
    human-gate tool such as analyze_opuset / select_state, run at Gate 3
    rather than as a phase), or validate.sh itself (its built-in fallback for
    the M sub-phases the manifest does not enumerate, e.g. Phase 8d)."""
    listed = {s for spec in _manifest().values() for s in spec.get("scripts", [])}
    gate_tools = (SKILL.parent / "opus-et-conductor" / "references"
                  / "gate_protocols.md").read_text()
    fallback = (SKILL / "validate.sh").read_text()
    unreachable = sorted(s.name for s in SLURM
                         if s.name not in listed
                         and s.name not in gate_tools
                         and s.name not in fallback)
    assert not unreachable, unreachable
