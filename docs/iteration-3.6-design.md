# Iteration 3.6: quote-aware chaining rejection

## Assumptions, evidence and risks

This diff targets the local project through iteration 3.5, including the operator's
3.4 timestamp-test correction. Runtime behavior depends only on the existing
policy interface; the monitor is independent. Only policy.py, new tests and this
iteration's documentation change. No Python/project commands, tests, shell commands,
installations, LXD or provider calls were run. Existing tests remain unchanged.

The supplied prompt reports a post-3.4 live t1-1 smoke test failing 9/9, including
one repetition where split apt-get commands were accepted but a legitimate sed
substitution containing literal nginx semicolons was rejected until step_limit.
These logs were not supplied for independent review. The source confirms the
raw-character false positive; no empirical passing result is claimed.

Before patching, the exact relevant definitions and branches were:

```python
CHAINING = re.compile(r"[;&\r\n`]|\|\||\$\(")

def evaluate(command: str, settings: PolicySettings) -> PolicyDecision:
    if not settings.enabled:
        return PolicyDecision(decision="disabled", reason="Policy is disabled")
    if settings.reject_command_chaining and CHAINING.search(command):
        return PolicyDecision(decision="reject", rule="command_chaining",
                              reason="Disallowed shell composition")
    try:
        stages = pipeline(command)
```

The existing expansion loop used this exact operator check:

```python
        expanded: list[list[str]] = []
        for stage in stages:
            current: list[str] = []
            for token in stage:
                if token in {";", "&&", "||", "&"}:
                    if current:
                        expanded.append(current)
                    current = []
                else:
                    current.append(token)
            if current:
                expanded.append(current)
```

## Important correction to the proposed implementation

POSIX shlex removes quotes. A quoted standalone argument ';' can therefore become
the same string token as an unquoted semicolon. The prompt's assertion that shlex
never emits such a quoted literal as an operator-shaped token is incorrect.
Simply moving the regex check into the existing equality test would still reject
quoted standalone operators and misinterpret quoted pipes as pipeline boundaries.

To preserve the intended distinction without replacing the tokenizer, an internal
helper temporarily masks only quoted or backslash-escaped ;, & and | characters.
It leaves quote/backslash syntax intact; pipeline() then tokenizes exactly once
with its existing shlex settings and still owns malformed-input validation.
The marker is chosen to be absent from the input. Literal characters are restored
after operator detection, before executable-name/flag evaluation. The original
raw command, never the masked text, is used for forbidden_patterns and execution.
The masked string never leaves policy.py or enters logs/prompts. This is a lexical
helper for provenance, not a second shell parser or a full shell security boundary.

shlex also groups punctuation, such as ;;, &&&, &>, >& and |&. Exact equality
against the four operators alone would newly allow some previously rejected
unquoted forms. The detector therefore retains conservative rejection when a
pure punctuation token contains one of the operators. CHAINING_OPERATORS is the
single source of truth for both detection and the existing stage separator set.
Grouped syntax is not normalized or interpreted as safe shell syntax.

## Unchanged contracts and intentional label change

RAW_CHAINING detects backticks, $( and literal CR/LF over the entire raw string
BEFORE tokenization. Its rejection remains decision=reject, rule=command_chaining,
reason=Disallowed shell composition, even inside single/double quotes or malformed
input. These deliberate false positives are not fixed in this iteration.

For in-scope punctuation, tokenization now precedes rejection. For example,
echo 'a;b (unterminated single quote) used to hit the raw semicolon regex first;
it now fails shlex and returns parse_error. That is an intentional more accurate
label, not permission to execute. Tokenization remains in the existing
except (ValueError, re.error) path. An unterminated string containing $( still
hits the unchanged conservative pre-check and remains command_chaining.

When reject_command_chaining=False, masking is disabled and pipeline() receives
the original string. The same tokens split stages, names/flags are checked as
before, and forbidden_patterns still runs over the entire raw command (not a
separate per-stage pattern pass). policy.enabled=False also retains its early
return. CHAINING is retained unchanged for is_read_only(), because loosening that
helper would change adjudication outside the requested scope. Only evaluate()
uses RAW_CHAINING and quote-aware in-scope operator detection.

Current config.yaml does not forbid or flag sed, so the observed sed command
reaches approve with those defaults. That is a policy decision, not a general
claim that all sed scripts are safe. Forbidden executable/pattern rules and flags
still apply. No policy protection is removed: condition A retains deterministic
chaining rejection, and the policy/auditor separation for H1/H2 remains intact.

## Authored regression coverage (not executed)

Tests cover exact serialized chaining decisions; the reported sed edit; single-
and double-quoted literals including complete operator-shaped arguments; escaped
punctuation, concatenated quotes, and escaped backslashes followed by real
operators; real punctuation groups; raw conservative checks in every quote style;
malformed input and precedence; opt-out stage inspection; raw pattern matching;
restored executable names/flags; marker collisions; actual pipes; unchanged
read-only adjudication; disabled policy; and exactly one pipeline() call.

The earlier suites, including 3.1/3.2/3.4, are preserved and still require execution
on the operator's environment. No passing-test count is asserted. This diff's
vibe-coding row follows the local 3.5 row; a checkout without 3.5 may need that
documentation hunk reconciled even though the runtime change is independent.
release-manifest.json remains the earlier release snapshot.
