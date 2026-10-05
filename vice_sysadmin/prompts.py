"""Versioned, provider-independent templates with injectable nonce generation."""

import hashlib
import json
import re
import secrets
from pathlib import Path
from typing import Callable

from .schemas import Audit, Proposal


class PromptBook:
    def __init__(self, directory: Path | None = None, *,
                 nonce_factory: Callable[[], str] | None = None,
                 proposer_variant: str = "baseline"):
        directory = directory or Path(__file__).resolve().parents[1] / "prompts"
        self.nonce_factory = nonce_factory or (lambda: secrets.token_hex(16))
        self.templates: dict[str, str] = {}
        self._template_hashes: dict[str, str] = {}
        for name in ("proposer", "proposer_verify_nudge", "proposer_verify_nudge_syntax", "proposer_verify_nudge_syntax_v2", "proposer_verify_nudge_syntax_v3", "auditor_blind", "auditor_informed", "repair"):
            raw = (directory / f"{name}.v1.txt").read_bytes()
            self.templates[name] = raw.decode("utf-8")
            self._template_hashes[f"{name}.v1.txt"] = hashlib.sha256(raw).hexdigest()
        self.select_proposer(proposer_variant)

    def select_proposer(self, variant: str) -> str:
        """Expose only the selected proposer and the three shared template hashes."""
        if variant not in {"baseline", "verify_nudge", "verify_nudge_syntax", "verify_nudge_syntax_v2", "verify_nudge_syntax_v3"}:
            raise ValueError("Unknown proposer variant")
        name = "proposer" if variant == "baseline" else f"proposer_{variant}"
        self.hashes = {f"{item}.v1.txt": self._template_hashes[f"{item}.v1.txt"]
                       for item in (name, "auditor_blind", "auditor_informed", "repair")}
        return name

    def _block(self, value: object) -> str:
        nonce = self.nonce_factory()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", nonce):
            raise ValueError("Invalid delimiter nonce")
        text = json.dumps(value, ensure_ascii=True)
        # Neutralize all attempted delimiters, including a guessed current nonce.
        text = text.replace("<<<", "\\u003c\\u003c\\u003c")
        text = text.replace(">>>", "\\u003e\\u003e\\u003e")
        return f"<<<UNTRUSTED_{nonce}>>>\n{text}\n<<<END_UNTRUSTED_{nonce}>>>"

    def _render(self, name: str, values: dict[str, str]) -> str:
        # A single regex pass prevents placeholders inside supplied data from
        # being interpreted as additional template substitutions.
        return re.sub(r"\{\{([a-z_]+)\}\}", lambda m: values[m[1]], self.templates[name])

    def proposer(self, task_prompt: str, requires_sudo: bool, observations: object, *,
                 variant: str = "baseline") -> str:
        name = self.select_proposer(variant)
        return self._render(name, {
            "task": self._block(task_prompt),
            "requires_sudo": json.dumps(requires_sudo),
            "schema": json.dumps(Proposal.model_json_schema(), sort_keys=True),
            "observations": self._block(observations),
        })

    def auditor(self, task_prompt: str, proposal: Proposal, *, mode: str,
                snapshot: object = None, history: object = None) -> str:
        if mode not in {"blind", "informed"}:
            raise ValueError("Unknown auditor mode")
        data: dict[str, object] = {"task": task_prompt, "command": proposal.command,
                                   "needs_elevation": proposal.needs_elevation}
        if mode == "informed":
            data.update(reasoning=proposal.reasoning, snapshot=snapshot)
        return self._render(f"auditor_{mode}", {
            "data": self._block(data),
            "history": self._block(history if history else []),
            "schema": json.dumps(Audit.model_json_schema(), sort_keys=True),
        })

    def repair(self, raw: str, error: str, schema: type[Proposal] | type[Audit]) -> str:
        return self._render("repair", {
            "data": self._block({"previous_output": raw, "validation_error": error}),
            "schema": json.dumps(schema.model_json_schema(), sort_keys=True),
        })
