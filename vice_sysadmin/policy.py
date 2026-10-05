"""Deterministic shell-text heuristics, never a security boundary or interpreter."""

import re
import shlex
from pathlib import PurePosixPath

from .schemas import PolicyDecision, PolicySettings

DEFAULT_READ_ONLY = frozenset({
    "id", "whoami", "pwd", "ls", "cat", "head", "tail", "grep", "ss",
    "stat", "wc", "df", "du", "ps", "uname", "systemctl", "journalctl",
})
CHAINING = re.compile(r"[;&\r\n`]|\|\||\$\(")
RAW_CHAINING = re.compile(r"[\r\n`]|\$\(")
CHAINING_OPERATORS = frozenset({";", "&&", "||", "&"})


def pipeline(command: str) -> list[list[str]]:
    """Split simple pipeline stages; malformed/empty stages are rejected."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    stages: list[list[str]] = [[]]
    for token in lexer:
        if token == "|":
            if not stages[-1]:
                raise ValueError("Empty pipeline stage")
            stages.append([])
        else:
            stages[-1].append(token)
    if not stages[-1]:
        raise ValueError("Empty command")
    return stages


def _mask_literal_operators(command: str) -> tuple[str, dict[str, str]]:
    """Preserve literal punctuation provenance before POSIX shlex removes quotes.

    Only used with chaining rejection enabled. Keep quotes/backslashes so shlex
    remains responsible for token boundaries and malformed-input errors.
    """
    marker = "\ue000"
    while marker in command:
        marker += "\ue000"
    encoded = {character: marker + str(index) + marker
               for index, character in enumerate(";&|")}
    result: list[str] = []
    quote = None
    index = 0
    while index < len(command):
        character = command[index]
        if quote == "'":
            if character == "'":
                quote = None
            result.append(encoded.get(character, character))
        elif character == "\\":
            result.append(character)
            index += 1
            if index < len(command):
                character = command[index]
                result.append(encoded.get(character, character))
        elif character in ("'", '"'):
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            result.append(character)
        else:
            result.append(encoded.get(character, character) if quote else character)
        index += 1
    return "".join(result), {value: key for key, value in encoded.items()}


def evaluate(command: str, settings: PolicySettings) -> PolicyDecision:
    if not settings.enabled:
        return PolicyDecision(decision="disabled", reason="Policy is disabled")
    if settings.reject_command_chaining and RAW_CHAINING.search(command):
        return PolicyDecision(decision="reject", rule="command_chaining",
                              reason="Disallowed shell composition")
    try:
        token_input, literals = (_mask_literal_operators(command)
                                 if settings.reject_command_chaining else (command, {}))
        stages = pipeline(token_input)
        # When chaining is allowed, inspect each simple command as well as
        # pipeline stages. This remains a lexical heuristic, not a shell AST.
        expanded: list[list[str]] = []
        for stage in stages:
            current: list[str] = []
            for token in stage:
                # shlex groups adjacent punctuation (for example ";;" or "&>").
                # Keep the old conservative rejection for those unquoted forms.
                composition = token in CHAINING_OPERATORS or (
                    bool(token) and all(char in "|;&()<>" for char in token)
                    and any(operator in token for operator in CHAINING_OPERATORS))
                if settings.reject_command_chaining and composition:
                    return PolicyDecision(decision="reject", rule="command_chaining",
                                          reason="Disallowed shell composition")
                if token in CHAINING_OPERATORS:
                    if current:
                        expanded.append(current)
                    current = []
                else:
                    for encoded, literal in literals.items():
                        token = token.replace(encoded, literal)
                    current.append(token)
            if current:
                expanded.append(current)
        stages = expanded
        for pattern in settings.forbidden_patterns:
            if re.search(pattern, command):
                return PolicyDecision(decision="reject", rule=f"pattern:{pattern}",
                                      reason="Forbidden pattern matched")
    except (ValueError, re.error) as exc:
        return PolicyDecision(decision="reject", rule="parse_error", reason=str(exc))
    names = [PurePosixPath(stage[0]).name for stage in stages]
    flags = [f"high_risk:{name}" for name in dict.fromkeys(names)
             if name in settings.high_risk_commands]
    for name in names:
        if name in settings.forbidden_commands:
            return PolicyDecision(decision="reject", rule=f"command:{name}",
                                  reason="Forbidden executable", flags=flags)
    return PolicyDecision(decision="approve", reason="No blocking rule matched", flags=flags)


def is_read_only(command: str, allowed: list[str] | None = None) -> bool:
    """Recognize a deliberately narrow subset, independent of policy.enabled.

    Variable expansion, aliases, executable replacement, and script indirection
    cannot be resolved here. Unknown executables and shell expansions are denied.
    """
    if CHAINING.search(command) or re.search(r"[$<>\\(){}]", command):
        return False
    try:
        stages = pipeline(command)
    except ValueError:
        return False
    allowlist = DEFAULT_READ_ONLY if allowed is None else set(allowed)
    for stage in stages:
        name, args = PurePosixPath(stage[0]).name, stage[1:]
        if name not in allowlist or name in {"tee", "sudo", "su", "bash", "sh", "env"}:
            return False
        # Paths outside standard executable directories may be attacker-owned.
        if "/" in stage[0] and str(PurePosixPath(stage[0]).parent) not in {"/bin", "/usr/bin", "/sbin", "/usr/sbin"}:
            return False
        if name == "find" and any(a.startswith(("-delete", "-exec", "-ok", "-fprint", "-fls")) for a in args):
            return False
        if name == "sed":
            # Sed can execute/write via its program; accept no sed programs.
            return False
        if name == "systemctl" and (not args or args[0] not in {
            "status", "show", "is-active", "is-enabled", "list-units", "list-unit-files",
        }):
            return False
        if name == "journalctl" and any(a.startswith(("--vacuum", "--rotate", "--flush", "--sync", "--setup-keys", "--update-catalog")) for a in args):
            return False
        if name == "ss" and any(a == "--kill" or (a.startswith("-") and "K" in a) for a in args):
            return False
    return True
