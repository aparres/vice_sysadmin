"""Strict JSON parsing and separately logged transport/repair attempts."""

import hashlib
import json
import re
from typing import Callable, TypeVar

from pydantic import ValidationError

from .budget import Budget
from .clients import strict_schema
from .interfaces import ClientError, LLMClient
from .prompts import PromptBook
from .schemas import Audit, ModelSettings, Proposal

T = TypeVar("T", Proposal, Audit)


class StructuredOutputError(RuntimeError):
    pass


def parse_output(raw: str, schema: type[T]) -> T:
    text = raw.strip()
    if text.startswith("```"):
        fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
        if fence is None:
            raise ValueError("Expected exactly one JSON code fence")
        text = fence[1]
    return schema.model_validate_json(text)


def request_structured(client: LLMClient, settings: ModelSettings, prompt: str,
                       schema: type[T], *, budget: Budget, prompts: PromptBook,
                       emit: Callable[..., None], role: str,
                       attempt_offset: int = 0, max_repairs: int = 1) -> T:
    """Log and charge every call; callers may disable further parse repairs."""
    if max_repairs not in (0, 1) or attempt_offset < 0:
        raise ValueError("Invalid structured request attempt limits")
    attempt = attempt_offset
    for repair in range(max_repairs + 1):
        response = None
        for transport in range(settings.retries + 1):
            # A provider-side schema is extra request input, in addition to the
            # schema already present in the textual prompt. Reserve its bytes.
            accounting_prompt = prompt
            if settings.output_format == "json_schema":
                accounting_prompt += json.dumps(strict_schema(schema), sort_keys=True)
            reservation = budget.reserve(accounting_prompt, settings)
            attempt += 1
            timeout = budget.timeout(settings.timeout_seconds)
            single_attempt = settings.model_copy(update={"timeout_seconds": timeout, "retries": 0})
            started = budget.clock.monotonic()
            error: Exception | None = None
            try:
                response = client.generate(prompt, single_attempt)
                if (budget.clock.monotonic() - started > timeout
                        or response.latency_seconds > timeout):
                    raise ClientError("Provider exceeded attempt timeout")
            except Exception as exc:
                error = exc
            charged_tokens, charged_cost = budget.charge(reservation, response)
            fields = response.model_dump() if response is not None else {}
            if response is None:
                fields["latency_seconds"] = max(0.0, budget.clock.monotonic() - started)
            emit(f"{role}_attempt", attempt=attempt, prompt_sent=prompt,
                 prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                 temperature=settings.temperature, charged_tokens=charged_tokens,
                 charged_cost=charged_cost, error=str(error) if error else None, **fields)
            budget.check()
            if error is None:
                break
            response = None
            if not isinstance(error, (ClientError, TimeoutError)) or transport == settings.retries:
                raise StructuredOutputError(f"{role} transport failed: {error}") from error
        if response is None:
            raise StructuredOutputError(f"{role} returned no response")
        try:
            return parse_output(response.raw_response, schema)
        except (ValidationError, ValueError) as exc:
            emit(f"{role}_invalid_output", attempt=attempt, error=str(exc))
            if repair == max_repairs:
                message = "invalid after one repair" if max_repairs else "invalid semantic repair"
                raise StructuredOutputError(f"{role} {message}") from exc
            prompt = prompts.repair(response.raw_response, str(exc), schema)
    raise StructuredOutputError("Unreachable structured-output state")
