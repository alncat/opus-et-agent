import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cluster


class FakeRunner:
    """Records calls and returns canned results."""

    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [cluster.Result("", "", 0)])

    def __call__(self, argv, timeout, input=None):
        self.calls.append({"argv": argv, "timeout": timeout, "input": input})
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def test_ssh_argv_multiplexes_and_disables_prompts():
    c = cluster.ClusterClient("myhost", control_path="/tmp/cp", runner=FakeRunner())
    argv = c.ssh_argv(["squeue", "--noheader"])
    assert argv[0] == "ssh"
    joined = " ".join(argv)
    assert "ControlMaster=auto" in joined
    assert "ControlPersist=60s" in joined
    assert "ControlPath=/tmp/cp" in joined
    # BatchMode makes it fail fast instead of hanging on a password prompt.
    assert "BatchMode=yes" in joined
    assert argv[-2] == "myhost"
    assert argv[-1] == "squeue --noheader"


def test_run_returns_result_and_passes_timeout():
    r = FakeRunner([cluster.Result("out", "err", 3)])
    c = cluster.ClusterClient("h", runner=r, timeout=12.0)
    got = c.run(["true"])
    assert (got.stdout, got.stderr, got.rc) == ("out", "err", 3)
    assert r.calls[0]["timeout"] == 12.0


def test_read_file_raises_when_missing():
    c = cluster.ClusterClient("h", runner=FakeRunner([cluster.Result("", "no such file", 1)]))
    with pytest.raises(FileNotFoundError):
        c.read_file("/nope")


def test_read_file_returns_contents():
    c = cluster.ClusterClient("h", runner=FakeRunner([cluster.Result("hello", "", 0)]))
    assert c.read_file("/some/path") == "hello"


def test_read_file_drops_openssh_warnings_from_the_error():
    """The stale banner used to lead with the post-quantum advisory, hiding
    the actual 'no such file'."""
    warn = (
        "** WARNING: connection is not using a post-quantum key exchange algorithm.\n"
        "** This session may be vulnerable to \"store now, decrypt later\" attacks.\n"
        "** The server may need to be upgraded. See https://openssh.com/pq.html\n"
        "cat: /run/.opus_run_state.json: No such file or directory\n"
    )
    c = cluster.ClusterClient("h", runner=FakeRunner([cluster.Result("", warn, 1)]))
    with pytest.raises(FileNotFoundError) as exc:
        c.read_file("/run/.opus_run_state.json")
    msg = str(exc.value)
    assert "No such file" in msg
    assert "post-quantum" not in msg
    assert "WARNING" not in msg


def test_write_file_passes_path_as_argument_not_interpolated():
    """The path must never be interpolated into the shell string, or a crafted
    path would be executed. It is passed positionally as $1."""
    r = FakeRunner()
    c = cluster.ClusterClient("h", runner=r)
    c.write_file("/run/pipeline.conf", "KEY=1\n")
    import shlex
    remote = r.calls[0]["argv"][-1]
    parts = shlex.split(remote)                  # what the REMOTE shell sees
    assert parts[0] == "sh" and parts[1] == "-c"
    assert "/run/pipeline.conf" not in parts[2]  # NOT baked into the shell program
    assert parts[-1] == "/run/pipeline.conf"     # passed positionally as $1
    assert r.calls[0]["input"] == "KEY=1\n"


def test_timeout_becomes_a_concise_error_not_a_subprocess_repr():
    """The message lands in the dashboard's stale banner, so it must be
    human-readable and must not dump the whole ssh argv."""
    import subprocess

    def boom(argv, timeout, input=None):
        raise subprocess.TimeoutExpired(argv, timeout)

    c = cluster.ClusterClient("myhost", runner=boom, timeout=9.0)
    with pytest.raises(cluster.ClusterError) as exc:
        c.run(["squeue"])
    msg = str(exc.value)
    assert msg == "ssh to myhost timed out after 9s"
    assert "ControlMaster" not in msg


def test_unreachable_host_becomes_a_concise_error():
    def boom(argv, timeout, input=None):
        raise OSError("Name or service not known")

    c = cluster.ClusterClient("myhost", runner=boom)
    with pytest.raises(cluster.ClusterError) as exc:
        c.run(["squeue"])
    assert "ssh to myhost failed" in str(exc.value)
    assert "ControlMaster" not in str(exc.value)


def test_remote_command_survives_the_shell_reparse_ssh_forces():
    """ssh joins its trailing argv into one string that the remote shell
    re-parses. Regression: unquoted, `sh -c 'squeue -o "%i|%j"'` arrived as
    `sh -c squeue -o ...` and silently ran bare squeue -- the jobs panel was
    empty against a real cluster while every fake-runner test passed."""
    import shlex
    c = cluster.ClusterClient("h", runner=FakeRunner())
    original = ["sh", "-c", 'squeue -u "$(whoami)" -o "%i|%j"', "_", "/p a/t.conf"]
    remote = c.ssh_argv(original)[-1]
    assert shlex.split(remote) == original


def test_arguments_with_spaces_are_not_split():
    import shlex
    c = cluster.ClusterClient("h", runner=FakeRunner())
    remote = c.ssh_argv(["cat", "--", "/a path/with spaces.conf"])[-1]
    assert shlex.split(remote) == ["cat", "--", "/a path/with spaces.conf"]


def test_read_bytes_decodes_base64_from_the_remote():
    """Binary must not be shovelled through a text pipe; base64 it."""
    import base64
    png = b"\x89PNG\r\n\x1a\n binary \x00\xff"
    r = FakeRunner([cluster.Result(base64.b64encode(png).decode(), "", 0)])
    c = cluster.ClusterClient("h", runner=r)
    assert c.read_bytes("/run/qc/a.png") == png
    assert "base64" in " ".join(r.calls[0]["argv"])


def test_read_bytes_raises_when_missing():
    c = cluster.ClusterClient("h", runner=FakeRunner([cluster.Result("", "no file", 1)]))
    with pytest.raises(FileNotFoundError):
        c.read_bytes("/nope.png")
