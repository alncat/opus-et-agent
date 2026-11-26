"""Exercise the public updater against local Git and isolated client installations."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/opus-et"
SKILLS = ["opus-et-" + s for s in ("analysis", "conductor", "status", "visualize", "warp")]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()


def write_command(path, body):
    path.write_text("#!" + sys.executable + "\n" + textwrap.dedent(body))
    path.chmod(0o755)


@pytest.fixture
def setup(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    baseline = tmp_path / "baseline.tar"
    subprocess.run(["git", "-C", ROOT, "archive", "-o", baseline, "HEAD", "--",
                    *SKILLS, ".agents/plugins", "AGENTS.md", "README.md", "tools"], check=True)
    with tarfile.open(baseline) as t:
        t.extractall(origin, filter="data")
    shutil.copy2(CLI, origin / "tools/opus-et")
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "test@example.org")
    git(origin, "config", "user.name", "OPUS-ET tests")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "first")
    desktop = tmp_path / "desktop"
    cluster = tmp_path / "cluster"
    for d in (desktop, cluster):
        d.mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    # Redirect Path.home only in these Python processes, including the SSH receiver.
    # The real user profile and the environment's HOME are never changed.
    driver = tmp_path / "driver.py"
    driver.write_text(textwrap.dedent("""
        import os, runpy, sys
        from pathlib import Path
        from unittest.mock import patch
        with patch.object(Path, 'home', return_value=Path(os.environ['OPUS_TEST_PROFILE'])):
            cli = sys.argv.pop(1)
            runpy.run_path(cli, run_name='__main__')
    """))
    write_command(commands / "ssh", """
        import os, shlex, sys
        from pathlib import Path
        from unittest.mock import patch
        assert sys.argv[1:3] == ['-T','super']
        source, *args = shlex.split(sys.argv[3].split(' -c ', 1)[1])
        sys.argv = ['-c', *args]
        with patch.object(Path, 'home', return_value=Path(os.environ['OPUS_TEST_CLUSTER'])):
            exec(compile(source, '<ssh>', 'exec'), {'__name__':'__main__'})
    """)
    write_command(commands / "codex", """
        import json, os, sys
        from pathlib import Path
        action, name, *args = sys.argv[2:]
        store = Path(os.environ['OPUS_TEST_PROFILE']) / (name + '.json')
        if action == 'get':
            if not store.exists():
                sys.exit("No MCP server named " + name)
            print(store.read_text())
        elif action == 'add':
            assert not store.exists()
            store.write_text(json.dumps({'transport': {'command': args[1], 'args':args[2:]}}))
        else:
            sys.exit(1)
    """)
    # Keep the global Antigravity install directory inside the test profile.
    # Run the actual validator when available.
    if not shutil.which("agy"):
        write_command(commands / "agy", """
            import sys
            from pathlib import Path
            assert sys.argv[1:4] == ['plugin', 'validate', '--strict']
            assert (Path(sys.argv[4]) / 'hooks.json').is_file()
        """)
    def run(*args, ok=True):
        env = dict(os.environ, OPUS_TEST_PROFILE=str(desktop), OPUS_TEST_CLUSTER=str(cluster),
                   PATH=str(commands) + os.pathsep + os.environ["PATH"])
        result = subprocess.run([sys.executable, driver, CLI, *args], env=env, text=True,
                                capture_output=True, timeout=45)
        if ok:
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0, result.stdout
        return result.stdout + result.stderr
    return origin, desktop, cluster, run


def test_git_update_and_remote_migration(setup):
    origin, desktop, cluster, run = setup
    old = git(origin, "rev-parse", "HEAD")
    copied = cluster / ".claude/skills/opus-et-warp"
    shutil.copytree(origin / copied.name, copied)
    initial = run("install", "--source", str(origin), "--client", "codex", "--client", "claude",
                  "--client", "antigravity", "--remote", "super")
    assert old in initial and copied.is_symlink()
    assert list((cluster / ".local/share/opus-et/backups").glob("*/opus-et-warp/SKILL.md"))
    assert (desktop / ".local/bin/opus-et").is_file()
    mcp = json.loads((desktop / "opus-super.json").read_text())["transport"]
    assert mcp["command"] == "ssh" and mcp["args"][:2] == ["-T", "super"]
    doc = origin / "opus-et-warp/SKILL.md"
    doc.write_text(doc.read_text() + "\nUpdated fixture\n")
    git(origin, "commit", "-qam", "second")
    new = git(origin, "rev-parse", "HEAD")
    run("update", "--remote", "super")
    run("update", "--remote", "super")
    for host in (desktop, cluster):
        for root in (".claude/skills", ".agents/skills"):
            assert (host / root / "opus-et-warp/SKILL.md").read_bytes() == doc.read_bytes()
        assert (host / ".agents/plugins/opus-et").resolve().parent.name == new
    assert (desktop / ".gemini/config/plugins/opus-et/skills/opus-et-warp/SKILL.md").read_bytes() == doc.read_bytes()
    assert old not in run("status", "--remote", "super")
    # A client can still point at an older release even when skill content matches.
    for skill in SKILLS:
        link = desktop / ".claude/skills" / skill
        link.unlink()
        link.symlink_to(desktop / ".local/share/opus-et/releases" / old / "opus-et/skills" / skill)
    assert f"claude skills  {old}" in run("status")


def test_conflicting_copy_is_preserved_before_link_changes(setup):
    origin, desktop, _, run = setup
    dest = desktop / ".claude/skills/opus-et-warp"
    shutil.copytree(origin / dest.name, dest)
    (dest / "pipeline.conf").write_text("local project settings")
    failed = run("install", "--source", str(origin), "--client", "claude", ok=False)
    assert str(dest) in failed and (dest / "pipeline.conf").read_text() == "local project settings"
    assert not (desktop / ".claude/skills/opus-et-analysis").exists()
    assert not (desktop / ".agents/plugins/opus-et").exists()


def test_modified_active_release_is_reported_and_preserved(setup):
    origin, desktop, _, run = setup
    run("install", "--source", str(origin), "--client", "claude")
    skill = desktop / ".claude/skills/opus-et-warp/SKILL.md"
    skill.write_text("local edit")
    assert "modified or unknown" in run("status")
    assert "local changes" in run("update", ok=False)
    assert skill.read_text() == "local edit"


def test_failed_cluster_update_can_resume_from_same_commit(setup):
    origin, desktop, cluster, run = setup
    run("install", "--source", str(origin), "--client", "claude", "--remote", "super")
    active = cluster / ".agents/plugins/opus-et"
    before = active.resolve()
    # An edited, independent cluster copy should survive the failed remote step.
    skill = cluster / ".claude/skills/opus-et-warp"
    skill.unlink()
    shutil.copytree(before / "skills/opus-et-warp", skill)
    (skill / "local.conf").write_text("scientist's copy")
    doc = origin / "opus-et-warp/SKILL.md"
    doc.write_text(doc.read_text() + "\nnew upstream revision\n")
    git(origin, "commit", "-qam", "update")
    revision = git(origin, "rev-parse", "HEAD")
    assert str(skill) in run("update", "--remote", "super", ok=False)
    assert active.resolve() == before
    assert (desktop / ".agents/plugins/opus-et").resolve().parent.name == revision
    (skill / "local.conf").rename(cluster / "saved.conf")
    run("update", "--remote", "super")
    assert active.resolve().parent.name == revision and skill.is_symlink()
    assert (cluster / "saved.conf").read_text() == "scientist's copy"


def test_antigravity_edit_aborts_install_and_restores_skills(setup):
    origin, desktop, _, run = setup
    skill = desktop / ".claude/skills/opus-et-analysis"
    shutil.copytree(origin / skill.name, skill)
    plugin = desktop / ".gemini/config/plugins/opus-et"
    plugin.mkdir(parents=True)
    (plugin / "local.txt").write_text("personal edit")
    assert str(plugin) in run("install", "--source", str(origin), "--client", "claude",
                              "--client", "antigravity", ok=False)
    assert not skill.is_symlink() and (skill / "SKILL.md").is_file()
    assert not (desktop / ".agents/plugins/opus-et").exists()
    assert (plugin / "local.txt").read_text() == "personal edit"
