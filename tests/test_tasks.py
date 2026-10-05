"""Benchmark catalog tests. bash -n only parses syntax; nothing is executed."""

import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from vice_sysadmin.schemas import Check, Verification, container_name, load_task

ROOT = Path(__file__).resolve().parents[1]
TASK_FILES = sorted((ROOT / "tasks").glob("*.yaml"))
EXPECTED_CATEGORIES = {
    "service-management": 2, "users-permissions": 3, "fault-diagnosis": 2,
    "log-analysis": 3, "ambiguous-instructions": 4, "network-firewall": 4,
}


@pytest.fixture(scope="module")
def tasks():
    return [load_task(path) for path in TASK_FILES]


def test_catalog_has_18_unique_tasks_with_expected_distribution(tasks):
    assert len(tasks) == 18
    assert len({t.id for t in tasks}) == 18
    assert Counter(t.category for t in tasks) == EXPECTED_CATEGORIES


def test_file_name_matches_task_id(tasks):
    for path, task in zip(TASK_FILES, tasks):
        assert path.stem == task.id


def test_ids_form_valid_lxd_container_names(tasks):
    for task in tasks:
        assert container_name(task.id, 1)
        assert container_name(task.id, 999)


def test_only_log_analysis_tasks_contain_prompt_injection(tasks):
    flagged = {t.id for t in tasks if t.contains_prompt_injection}
    expected = {t.id for t in tasks if t.category == "log-analysis"}
    assert flagged == expected


def test_setup_scripts_have_no_placeholders(tasks):
    for task in tasks:
        assert "{{" not in task.setup.script


def test_every_task_has_named_checks(tasks):
    for task in tasks:
        assert task.verification.checks
        assert task.verification.success_if


def test_host_checks_are_used_only_for_external_reachability(tasks):
    hosts = {t.id for t in tasks
             if any(c.run_on == "host" for c in t.verification.checks)}
    assert hosts == {"t6-1-open-port", "t6-3-harden-ports", "t6-4-diagnose-unreachable"}


def test_container_check_rejects_placeholders():
    with pytest.raises(ValidationError):
        Check(name="x", run_on="container", script="nc -z {{CONTAINER_IP}} 22")


def test_host_check_accepts_known_and_rejects_unknown_placeholders():
    assert Check(name="x", run_on="host", script="nc -z {{CONTAINER_IP}} 22")
    with pytest.raises(ValidationError):
        Check(name="x", run_on="host", script="echo {{SECRET_KEY}}")
    with pytest.raises(ValidationError):
        Check(name="x", run_on="host", script="echo {{ }}")


def test_check_names_must_be_unique_and_well_formed():
    dup = [{"name": "a", "run_on": "container", "script": "true"},
           {"name": "a", "run_on": "container", "script": "true"}]
    with pytest.raises(ValidationError):
        Verification(success_if="x", checks=dup)
    with pytest.raises(ValidationError):
        Check(name="Bad-Name", run_on="container", script="true")
    with pytest.raises(ValidationError):
        Verification(success_if="x", checks=[])


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_all_scripts_pass_bash_syntax_check(tasks):
    for task in tasks:
        scripts = [("setup", task.setup.script)]
        for check in task.verification.checks:
            body = check.script.replace("{{CONTAINER_IP}}", "10.0.0.1")
            scripts.append((check.name, body.replace("{{CONTAINER_NAME}}", "name")))
        for label, script in scripts:
            done = subprocess.run(["bash", "-n"], input=script, text=True,
                                  capture_output=True, check=False)
            assert done.returncode == 0, f"{task.id}/{label}: {done.stderr}"
