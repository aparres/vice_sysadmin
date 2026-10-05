"""Argument and byte-budget tests only; no subprocess or LXD is executed."""

from io import BytesIO
import json

import pytest

from vice_sysadmin.live_support import RealHostCheckRunner
from vice_sysadmin.lxd_runtime import LxdRuntime, validate_name
from vice_sysadmin.mocks import FakeClock
from vice_sysadmin.processes import OutputBudget, capture_stream
from vice_sysadmin.schemas import ExecutionRequest, ExecutionResult


def result(stdout="", code=0, **changes):
    values = dict(stdout=stdout, stderr="", exit_code=code, timed_out=False,
                  stdout_truncated=False, stderr_truncated=False, latency_seconds=0.0)
    return ExecutionResult(**(values | changes))


class Transport:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def run(self, argv, timeout_seconds, output_byte_cap):
        self.calls.append((argv, timeout_seconds, output_byte_cap))
        return next(self.results)


def runtime(results):
    transport = Transport(results)
    return LxdRuntime(transport, clock=FakeClock(), locator=lambda _: "/usr/bin/lxc"), transport


def request(**changes):
    return ExecutionRequest(**(dict(container_name="task-example-rep-1", command="id",
                                    user="agent", working_directory="/home/agent",
                                    timeout_seconds=10.0, output_byte_cap=1024) | changes))


def test_missing_binary_fails_before_any_process():
    with pytest.raises(RuntimeError, match="lxc was not found"):
        LxdRuntime(locator=lambda _: None)


@pytest.mark.parametrize("name", ["-bad", "remote:container", "a/b", "name;id", "../x", "a" * 64])
def test_names_are_validated_before_transport(name):
    instance, transport = runtime([])
    with pytest.raises(ValueError):
        instance.exists(name)
    assert not transport.calls


def test_derived_container_name_can_exceed_identifier_length():
    assert validate_name("task-" + "a" * 45 + "-rep-1")
    with pytest.raises(ValueError):
        validate_name("a" * 49, container=False)


def test_exists_matches_exact_name_not_substring():
    instance, transport = runtime([result(json.dumps([{"name": "task-example-rep-10"}]))])
    assert not instance.exists("task-example-rep-1")
    assert transport.calls[0][0] == ["/usr/bin/lxc", "list", "task-example-rep-1", "--format", "json"]


def test_create_profile_copy_start_and_readiness():
    instance, transport = runtime([result("[]"), result(), result(), result(code=1), result(), result()])
    instance.create("task-example-rep-1", "vice-template", "base", "vicesysadmin")
    assert [call[0] for call in transport.calls] == [
        ["/usr/bin/lxc", "list", "task-example-rep-1", "--format", "json"],
        ["/usr/bin/lxc", "copy", "vice-template/base", "task-example-rep-1", "--profile", "vicesysadmin"],
        ["/usr/bin/lxc", "start", "task-example-rep-1"],
        ["/usr/bin/lxc", "exec", "task-example-rep-1", "--disable-stdin", "--force-noninteractive", "--", "true"],
        ["/usr/bin/lxc", "exec", "task-example-rep-1", "--disable-stdin", "--force-noninteractive", "--", "true"],
        ["/usr/bin/lxc", "exec", "task-example-rep-1", "--disable-stdin", "--force-noninteractive",
         "--", "test", "-S", "/run/systemd/private"],
    ]
    assert transport.calls[-2][1] < transport.calls[-3][1]
    assert transport.calls[-1][1] == transport.calls[-2][1]


def test_create_rejects_existing_container():
    instance, transport = runtime([result('[{"name":"task-example-rep-1"}]')])
    with pytest.raises(ValueError, match="already exists"):
        instance.create("task-example-rep-1", "vice-template", "base", "vicesysadmin")
    assert len(transport.calls) == 1


def test_get_ip_polls_for_global_private_ipv4():
    initial = {"name": "task-example-rep-1", "state": {"network": {"eth0": {"addresses": []}}}}
    assigned = json.loads(json.dumps(initial))
    assigned["state"]["network"]["eth0"]["addresses"] = [
        {"family": "inet", "scope": "link", "address": "169.254.1.1"},
        {"family": "inet6", "scope": "global", "address": "2001:db8::1"},
        {"family": "inet", "scope": "global", "address": "10.18.28.10"},
    ]
    instance, transport = runtime([result(json.dumps([initial])), result(json.dumps([assigned]))])
    assert instance.get_ip("task-example-rep-1") == "10.18.28.10"
    assert len(transport.calls) == 2


def test_destroy_absent_and_already_stopped_are_idempotent():
    instance, transport = runtime([result("[]")])
    instance.destroy("task-example-rep-1")
    assert len(transport.calls) == 1
    stopped = '[{"name":"task-example-rep-1","status":"Stopped"}]'
    instance, transport = runtime([result(stopped), result(code=1), result(stopped), result()])
    instance.destroy("task-example-rep-1")
    assert transport.calls[1][0] == ["/usr/bin/lxc", "stop", "--force", "task-example-rep-1"]
    assert transport.calls[-1][0] == ["/usr/bin/lxc", "delete", "task-example-rep-1"]


def test_unprivileged_argv_uid_gid_cached_and_command_is_literal():
    instance, transport = runtime([result("1001\n"), result("1002\n"), result(), result()])
    command = "printf '%s' '$(touch /host-would-be-bad)'"
    instance.execute(request(command=command))
    instance.execute(request())
    assert transport.calls[0][0][-3:] == ["id", "-u", "agent"]
    assert transport.calls[1][0][-3:] == ["id", "-g", "agent"]
    assert transport.calls[2][0] == [
        "/usr/bin/lxc", "exec", "task-example-rep-1", "--disable-stdin", "--force-noninteractive",
        "--user", "1001", "--group", "1002", "--env", "HOME=/home/agent", "--cwd", "/home/agent",
        "--", "timeout", "--signal=KILL", "8.000000s", "bash", "-c", command,
    ]
    assert transport.calls[2][1] == 9.0
    assert len(transport.calls) == 4


def test_elevated_argv_has_no_user_flag_or_identity_probe():
    instance, transport = runtime([result()])
    instance.execute(request(elevate=True, task_requires_sudo=True))
    assert "--user" not in transport.calls[0][0]
    assert transport.calls[0][0][-3:] == ["bash", "-c", "id"]


@pytest.mark.parametrize("purpose", ["setup", "verification", "snapshot"])
def test_harness_always_uses_root_and_same_bounds(purpose):
    instance, transport = runtime([result()])
    instance.run_harness_script("task-example-rep-1", "trusted script", purpose=purpose,
                                 timeout_seconds=10.0, output_byte_cap=20)
    assert "--user" not in transport.calls[0][0]
    assert "HOME=/root" in transport.calls[0][0]
    assert transport.calls[0][0][-1] == "trusted script"
    assert transport.calls[0][2] == 20


@pytest.mark.parametrize("exit_code", [124, 137])
def test_remote_timeout_stops_container_and_maps_result(exit_code):
    instance, transport = runtime([result(code=exit_code), result()])
    output = instance.execute(request(elevate=True, task_requires_sudo=True))
    assert output.timed_out and output.exit_code is None
    assert transport.calls[-1][0] == ["/usr/bin/lxc", "stop", "--force", "task-example-rep-1"]


def test_output_limit_stops_remote_target():
    instance, transport = runtime([result(code=None, output_limit_reached=True, stdout_truncated=True), result()])
    output = instance.execute(request(elevate=True, task_requires_sudo=True))
    assert output.output_limit_reached and output.exit_code is None
    assert "stop" in transport.calls[-1][0]


def test_incremental_combined_cap_and_partial_utf8():
    budget = OutputBudget(5)
    assert not budget.feed("stdout", b"ab")
    assert budget.feed("stderr", "éé".encode())
    assert len(budget.stdout) + len(budget.stderr) == 5
    assert bytes(budget.stderr).decode("utf-8", errors="ignore") == "é"
    source = BytesIO(b"x" * 10000)
    captured = capture_stream(source, 16)
    assert len(captured.stdout) == 16 and captured.stdout_truncated
    assert source.tell() == 16


def test_host_script_is_one_literal_bash_argument():
    transport = Transport([result()])
    RealHostCheckRunner(transport).run("nc -z 10.18.28.10 80", "task-example-rep-1", 2.0, 100)
    assert transport.calls == [(["bash", "-c", "nc -z 10.18.28.10 80"], 2.0, 100)]


def test_uid_zero_cannot_bypass_unprivileged_default():
    instance, transport = runtime([result("0\n")])
    with pytest.raises(ValueError, match="nonzero"):
        instance.execute(request())
    assert len(transport.calls) == 1


def test_submillisecond_timeout_cannot_be_formatted_as_disabled_timeout():
    instance, transport = runtime([])
    with pytest.raises(TimeoutError):
        instance.execute(request(elevate=True, task_requires_sudo=True, timeout_seconds=0.0000001))
    assert not transport.calls
