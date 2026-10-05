"""LXD readiness polling with the existing transport stub; no real processes."""

from itertools import chain, repeat

import pytest

from test_iteration3_runtime import result, runtime

NAME = "task-example-rep-1"
EXEC = ["/usr/bin/lxc", "exec", NAME, "--disable-stdin", "--force-noninteractive", "--"]
BUS = EXEC + ["test", "-S", "/run/systemd/private"]


def create(instance, timeout_seconds=30.0):
    return instance.create(NAME, "vice-template", "base", "vicesysadmin",
                           timeout_seconds=timeout_seconds)


def test_exec_must_be_ready_before_bus_probe():
    instance, transport = runtime([
        result("[]"), result(), result(), result(code=1), result(), result(),
    ])
    assert create(instance) is None
    assert [call[0] for call in transport.calls[3:]] == [EXEC + ["true"], EXEC + ["true"], BUS]
    assert instance.clock.monotonic() == pytest.approx(0.1)
    assert transport.calls[-1][1] == pytest.approx(29.9)
    assert next(transport.results, None) is None


@pytest.mark.parametrize("not_ready", [result(code=1), result(code=0, timed_out=True)])
def test_bus_retries_nonzero_or_timed_out_then_returns(not_ready):
    instance, transport = runtime([
        result("[]"), result(), result(), result(), not_ready, result(),
    ])
    assert create(instance) is None
    assert [call[0] for call in transport.calls[3:]] == [EXEC + ["true"], BUS, BUS]
    assert instance.clock.monotonic() == pytest.approx(0.1)
    assert transport.calls[-1][1] < transport.calls[-2][1]
    assert next(transport.results, None) is None
    assert not instance._identities


def test_bus_deadline_uses_time_already_spent_waiting_for_exec():
    instance, transport = runtime(chain(
        [result("[]"), result(), result(), result(code=1), result()],
        repeat(result(code=1)),
    ))
    with pytest.raises(TimeoutError, match="^LXD operation deadline expired$"):
        create(instance, timeout_seconds=0.25)
    assert instance.clock.monotonic() == pytest.approx(0.25)
    assert [call[0] for call in transport.calls[3:]] == [
        EXEC + ["true"], EXEC + ["true"], BUS, BUS,
    ]
    assert [call[1] for call in transport.calls[-2:]] == pytest.approx([0.15, 0.05])
    assert all(call[1] > 0 for call in transport.calls)


def test_exec_deadline_still_expires_without_probing_bus():
    instance, transport = runtime(chain(
        [result("[]"), result(), result()], repeat(result(code=1)),
    ))
    with pytest.raises(TimeoutError, match="^LXD operation deadline expired$"):
        create(instance, timeout_seconds=0.25)
    assert instance.clock.monotonic() == pytest.approx(0.25)
    assert all(call[0] == EXEC + ["true"] for call in transport.calls[3:])


def test_ready_bus_adds_exactly_one_call_and_no_sleep():
    instance, transport = runtime([
        result("[]"), result(), result(), result(), result(),
    ])
    assert create(instance) is None
    assert len(transport.calls) == 5
    assert [call[0] for call in transport.calls[3:]] == [EXEC + ["true"], BUS]
    assert all(call[1] == 30.0 for call in transport.calls)
    assert instance.clock.monotonic() == 0.0
    assert next(transport.results, None) is None
