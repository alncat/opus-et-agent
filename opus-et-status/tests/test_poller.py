import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import poller


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_first_refresh_fetches_every_source():
    calls = {"a": 0}

    def fetch_a():
        calls["a"] += 1
        return "va"

    p = poller.Poller({"a": fetch_a}, {"a": 10.0}, clock=Clock())
    p.refresh_due()
    assert calls["a"] == 1
    assert p.snapshot()["a"].data == "va"


def test_source_not_refetched_before_ttl_expires():
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return calls["n"]

    clk = Clock()
    p = poller.Poller({"a": fetch}, {"a": 10.0}, clock=clk)
    p.refresh_due()
    clk.t += 5          # not yet due
    p.refresh_due()
    assert calls["n"] == 1
    clk.t += 6          # now past the 10s TTL
    p.refresh_due()
    assert calls["n"] == 2


def test_failing_source_keeps_last_good_data_and_records_error():
    state = {"boom": False}

    def fetch():
        if state["boom"]:
            raise RuntimeError("ssh died")
        return "good"

    clk = Clock()
    p = poller.Poller({"a": fetch}, {"a": 10.0}, clock=clk)
    p.refresh_due()
    state["boom"] = True
    clk.t += 11
    p.refresh_due()

    s = p.snapshot()["a"]
    assert s.data == "good"              # stale data retained, never blanked
    assert "ssh died" in s.error
    assert s.fetched_at == 1000.0        # freshness still reflects last SUCCESS


def test_one_failing_source_does_not_affect_another():
    def ok():
        return "fine"

    def bad():
        raise RuntimeError("nope")

    p = poller.Poller({"a": ok, "b": bad}, {"a": 10.0, "b": 10.0}, clock=Clock())
    p.refresh_due()
    snap = p.snapshot()
    assert snap["a"].data == "fine" and snap["a"].error is None
    assert snap["b"].data is None and snap["b"].error is not None


def test_repeated_failures_back_off_and_cap():
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise RuntimeError("down")

    clk = Clock()
    p = poller.Poller({"a": bad}, {"a": 10.0}, clock=clk, max_backoff=40.0)
    p.refresh_due()                 # attempt 1 -> next due at +20
    assert calls["n"] == 1
    clk.t += 11
    p.refresh_due()                 # backed off; not due yet
    assert calls["n"] == 1
    clk.t += 10                     # now past +20
    p.refresh_due()                 # attempt 2 -> next due at +40
    assert calls["n"] == 2
    clk.t += 41
    p.refresh_due()                 # attempt 3, capped at max_backoff
    assert calls["n"] == 3


def test_success_after_failure_clears_error_and_backoff():
    state = {"boom": True}

    def fetch():
        if state["boom"]:
            raise RuntimeError("down")
        return "back"

    clk = Clock()
    p = poller.Poller({"a": fetch}, {"a": 10.0}, clock=clk)
    p.refresh_due()
    state["boom"] = False
    clk.t += 100
    p.refresh_due()
    s = p.snapshot()["a"]
    assert s.data == "back"
    assert s.error is None
    assert s.failures == 0


def test_invalidate_forces_an_immediate_refetch():
    """After a user action changes something on the cluster, the affected
    source must not wait out its TTL."""
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return calls["n"]

    clk = Clock()
    p = poller.Poller({"a": fetch}, {"a": 300.0}, clock=clk)
    p.refresh_due()
    p.refresh_due()                 # still within TTL
    assert calls["n"] == 1
    p.invalidate("a")
    p.refresh_due()
    assert calls["n"] == 2


def test_invalidate_ignores_an_unknown_source():
    p = poller.Poller({"a": lambda: 1}, {"a": 10.0}, clock=Clock())
    p.invalidate("nope")            # must not raise
