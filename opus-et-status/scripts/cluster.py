"""SSH transport for the status server.

The ONLY module that talks to the cluster. Contains no cryo-ET knowledge, so it
can be tested with an injected fake runner and no network.
"""
from __future__ import annotations

import base64
import shlex
import subprocess
from dataclasses import dataclass

SSH_OPTS = [
    "-o", "ControlMaster=auto",
    "-o", "ControlPersist=60s",
    # Fail fast instead of hanging forever on an interactive password prompt.
    "-o", "BatchMode=yes",
]


_SSH_NOISE = (
    "** WARNING",
    "** This session",
    "** The server",
    "openssh.com/pq.html",
)


def clean_ssh_text(text):
    """Drop OpenSSH client warnings so they never reach the stale banner.

    `ssh` prints the post-quantum key-exchange warning on stderr even when
    `cat` itself only said the file was missing; the banner then hid the
    actual error behind a paragraph of advisory text.
    """
    lines = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if any(s.startswith(p) or p in s for p in _SSH_NOISE):
            continue
        lines.append(s)
    return " ".join(lines)


class ClusterError(RuntimeError):
    """A cluster call failed. Carries a short, human-readable reason -- the
    message reaches the dashboard's stale banner, so it must never be a raw
    subprocess repr with the full ssh argv in it."""


@dataclass(frozen=True)
class Result:
    stdout: str
    stderr: str
    rc: int


def _subprocess_runner(argv, timeout, input=None):
    cp = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, input=input
    )
    return Result(cp.stdout, cp.stderr, cp.returncode)


class ClusterClient:
    def __init__(self, host, *, control_path=None, timeout=30.0, runner=None):
        self.host = host
        self.control_path = control_path
        self.timeout = timeout
        self._runner = runner or _subprocess_runner

    def ssh_argv(self, argv):
        """Build the ssh argv, with the remote command as ONE quoted string.

        ssh concatenates its trailing arguments and the REMOTE shell re-parses
        the result, so anything containing spaces or quotes must be quoted
        first. Passing argv through unquoted turns
        `sh -c 'squeue -o "%i|%j"'` into `sh -c squeue -o "%i|%j"`, which
        silently runs bare squeue instead of the intended command.
        """
        opts = list(SSH_OPTS)
        if self.control_path:
            opts += ["-o", f"ControlPath={self.control_path}"]
        remote = " ".join(shlex.quote(a) for a in argv)
        return ["ssh", *opts, self.host, remote]

    def run(self, argv, *, timeout=None, input=None):
        t = timeout if timeout is not None else self.timeout
        try:
            return self._runner(self.ssh_argv(argv), t, input)
        except subprocess.TimeoutExpired:
            raise ClusterError(f"ssh to {self.host} timed out after {t:.0f}s") from None
        except OSError as exc:
            raise ClusterError(f"ssh to {self.host} failed: {exc}") from None

    def read_file(self, path):
        res = self.run(["cat", "--", path])
        if res.rc != 0:
            raise FileNotFoundError(f"{path}: {clean_ssh_text(res.stderr) or 'not found'}")
        return res.stdout

    def read_bytes(self, path):
        """Fetch a binary file. base64 on the remote side, since the ssh pipe
        is decoded as text."""
        res = self.run(["base64", "--", path])
        if res.rc != 0:
            raise FileNotFoundError(f"{path}: {clean_ssh_text(res.stderr) or 'not found'}")
        return base64.b64decode(res.stdout)

    def write_file(self, path, content):
        # The path is passed as a positional argument ($1), never interpolated
        # into the shell program, so a crafted path cannot execute.
        res = self.run(["sh", "-c", 'cat > "$1"', "_", path], input=content)
        if res.rc != 0:
            raise OSError(f"write {path} failed: {res.stderr.strip()}")
