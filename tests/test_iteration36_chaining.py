"""Quote-aware chaining regressions; no shell or model execution."""

from pathlib import Path

import pytest

from vice_sysadmin import policy
from vice_sysadmin.policy import evaluate, is_read_only
from vice_sysadmin.schemas import PolicyDecision, load_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings():
    return load_config(ROOT / "config.yaml").policy


def assert_chaining(command, settings):
    expected = PolicyDecision(decision="reject", rule="command_chaining",
                              reason="Disallowed shell composition")
    assert evaluate(command, settings).model_dump_json() == expected.model_dump_json()


@pytest.mark.parametrize("command", [
    "apt-get update && apt-get install -y nginx",
    "echo 'a;b'; reboot",
    "echo a & reboot",
    "echo a || reboot",
    "echo a;reboot",
    "echo a;;reboot",
    "echo a &&& reboot",
    "echo a &> output",
    "echo a 2>&1",
    "echo a |& cat",
])
def test_real_composition_retains_exact_rejection(command, settings):
    assert_chaining(command, settings)


def test_observed_sed_edit_is_approved_with_current_defaults(settings):
    assert "sed" not in settings.forbidden_commands
    assert "sed" not in settings.high_risk_commands
    command = "sed -i 's/listen 80;/listen 8080;/' /etc/nginx/sites-available/default"
    result = evaluate(command, settings)
    assert result.decision == "approve" and result.rule is None


@pytest.mark.parametrize("quote", ["'", '"'])
@pytest.mark.parametrize("literal", [";", "&", "&&", "||", "|", "a;b", "a&b", "a&&b", "a||b"])
def test_quoted_arguments_are_literals_even_when_whole_token_is_operator(settings, quote, literal):
    assert evaluate("echo " + quote + literal + quote, settings).decision == "approve"


@pytest.mark.parametrize("command", [
    r"echo a\;b", r"echo \;", r"echo \&\&", r"echo \|\|",
    "echo a';'b", "echo 'a'\";\"b", r'''echo "a\";b"''',
    r'''echo 'a\;b' ''',
])
def test_escaped_and_concatenated_literals(settings, command):
    assert evaluate(command, settings).decision == "approve"


@pytest.mark.parametrize("command", [
    r"echo a\\; reboot", r'''echo "a\\"; reboot''',
    "echo ';' && reboot", "echo '||'; reboot",
])
def test_escaped_backslash_does_not_hide_following_operator(settings, command):
    assert_chaining(command, settings)


@pytest.mark.parametrize("quote", ["", "'", '"'])
@pytest.mark.parametrize("payload", [chr(96) + "id" + chr(96), "$(id)", "a\nb", "a\rb"])
def test_raw_conservative_checks_stay_unchanged(settings, quote, payload):
    assert_chaining("echo " + quote + payload + quote, settings)


@pytest.mark.parametrize("command", ["echo 'unterminated", "echo 'a;b", 'echo "a&b', "id |", "| id"])
def test_malformed_commands_use_parse_error(settings, command):
    result = evaluate(command, settings)
    assert result.decision == "reject" and result.rule == "parse_error"


def test_raw_check_still_precedes_malformed_quote(settings):
    assert_chaining("echo '$(id)", settings)


@pytest.mark.parametrize("separator", [";", "&&", "||", "&"])
def test_chaining_opt_out_still_inspects_executable_names(settings, separator):
    settings = settings.model_copy(update={"reject_command_chaining": False})
    result = evaluate(f"id {separator} reboot", settings)
    assert result.decision == "reject" and result.rule == "command:reboot"
    assert evaluate(f"id {separator} whoami", settings).decision == "approve"
    flagged = evaluate(f"id {separator} chmod 600 report", settings)
    assert flagged.decision == "approve" and flagged.flags == ["high_risk:chmod"]


@pytest.mark.parametrize("enabled", [False, True])
def test_forbidden_pattern_still_matches_original_raw_text(settings, enabled):
    pattern = "listen 80;"
    settings = settings.model_copy(update={"reject_command_chaining": enabled,
                                           "forbidden_patterns": [pattern]})
    result = evaluate("sed -i 's/listen 80;/listen 8080;/' file", settings)
    assert result.rule == "pattern:" + pattern


def test_restore_literals_before_executable_rules_and_flags(settings):
    settings = settings.model_copy(update={"forbidden_commands": ["odd;name"],
                                           "high_risk_commands": ["odd;name"]})
    result = evaluate("'odd;name' argument", settings)
    assert result.rule == "command:odd;name" and result.flags == ["high_risk:odd;name"]


def test_actual_pipeline_stages_still_checked(settings):
    assert evaluate("echo ';' | reboot", settings).rule == "command:reboot"
    assert evaluate("echo ';' | cat", settings).decision == "approve"


def test_marker_cannot_collide_with_user_input(settings):
    name = "odd\ue0000\ue000;name"
    settings = settings.model_copy(update={"forbidden_commands": [name]})
    assert evaluate("'" + name + "'", settings).rule == "command:" + name


def test_policy_disabled_and_read_only_adjudication_are_unchanged(settings):
    disabled = settings.model_copy(update={"enabled": False})
    assert evaluate("reboot && poweroff", disabled).decision == "disabled"
    assert not is_read_only("echo ';'", ["echo"])
    assert not is_read_only("echo '&'", ["echo"])
    assert not is_read_only("echo '||'", ["echo"])
    assert not is_read_only("echo '$(id)'", ["echo"])


def test_one_pipeline_tokenization_and_parse_exception_path(settings, monkeypatch):
    original = policy.pipeline
    calls = []

    def counted(command):
        calls.append(command)
        return original(command)

    monkeypatch.setattr(policy, "pipeline", counted)
    assert evaluate("echo ';'", settings).decision == "approve"
    assert len(calls) == 1
    result = evaluate("echo 'a;b", settings)
    assert result.rule == "parse_error" and len(calls) == 2


def test_opt_out_does_not_disable_raw_forbidden_patterns(settings):
    settings = settings.model_copy(update={"reject_command_chaining": False,
                                           "forbidden_patterns": ["sensitive"]})
    result = evaluate("id && cat sensitive", settings)
    assert result.decision == "reject" and result.rule == "pattern:sensitive"
