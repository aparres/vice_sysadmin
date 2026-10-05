"""Single-attempt provider clients; SDKs are imported only for live calls."""

import asyncio
import copy
import os
import time

from .interfaces import ClientError
from .schemas import Audit, LLMResponse, ModelSettings, Proposal


def _key(settings: ModelSettings) -> str:
    value = os.environ.get(settings.api_key_env or "", "")
    if not value.strip():
        raise ClientError("Configured API-key environment variable is unset or empty")
    return value


def strict_schema(schema: type[Proposal] | type[Audit]) -> dict:
    """Turn defaulted optional properties into required nullable/defaultable data."""
    result = copy.deepcopy(schema.model_json_schema())
    def visit(value: object) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(result)
    return result


def _count(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("Malformed token count")
    return value


class OpenAIClient:
    def __init__(self, schema: type[Proposal] | type[Audit] = Proposal):
        self.schema = schema

    async def _generate(self, prompt: str, settings: ModelSettings, key: str) -> object:
        from openai import AsyncOpenAI
        options = {}
        if settings.output_format == "json":
            options["text"] = {"format": {"type": "json_object"}}
        elif settings.output_format == "json_schema":
            options["text"] = {"format": {"type": "json_schema", "name": self.schema.__name__,
                                           "strict": True, "schema": strict_schema(self.schema)}}
        # Unknown model capability => plain text on the FIRST and only request.
        # Never probe/fallback with a hidden second API request.
        async with asyncio.timeout(settings.timeout_seconds):
            async with AsyncOpenAI(api_key=key, max_retries=0,
                                   timeout=settings.timeout_seconds,
                                   base_url="https://api.openai.com/v1") as client:
                return await client.responses.create(
                    model=settings.model, input=prompt, temperature=settings.temperature,
                    top_p=settings.top_p, max_output_tokens=settings.max_tokens,
                    store=False, **options,
                )

    def generate(self, prompt: str, settings: ModelSettings) -> LLMResponse:
        started = time.monotonic()
        try:
            key = _key(settings)
            response = asyncio.run(self._generate(prompt, settings, key))
            raw = response.output_text
            if not isinstance(raw, str) or not raw or getattr(response, "error", None) is not None:
                raise ValueError("Response has no usable text")
            usage = getattr(response, "usage", None)
            return LLMResponse(
                raw_response=raw, model_returned=getattr(response, "model", None),
                model_version_returned=None,
                input_tokens=_count(getattr(usage, "input_tokens", None)),
                output_tokens=_count(getattr(usage, "output_tokens", None)),
                latency_seconds=time.monotonic() - started, estimated_cost=None,
            )
        except ClientError:
            raise
        except Exception as exc:
            # SDK exception messages can contain URLs, request bodies, and keys.
            raise ClientError(f"OpenAI request failed ({type(exc).__name__})") from None


class GoogleClient:
    def __init__(self, schema: type[Proposal] | type[Audit] = Audit):
        self.schema = schema

    async def _generate(self, prompt: str, settings: ModelSettings, key: str) -> object:
        from google import genai
        from google.genai import types
        options = dict(temperature=settings.temperature, top_p=settings.top_p,
                       max_output_tokens=settings.max_tokens, candidate_count=1)
        if settings.output_format in {"json", "json_schema"}:
            options["response_mime_type"] = "application/json"
        if settings.output_format == "json_schema":
            options["response_json_schema"] = strict_schema(self.schema)
        async with asyncio.timeout(settings.timeout_seconds):
            owner = genai.Client(
                api_key=key, enterprise=False,
                http_options=types.HttpOptions(
                    timeout=max(1, int(settings.timeout_seconds * 1000)),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            try:
                async with owner.aio as client:
                    return await client.models.generate_content(
                        model=settings.model, contents=prompt,
                        config=types.GenerateContentConfig(**options),
                    )
            finally:
                owner.close()

    def generate(self, prompt: str, settings: ModelSettings) -> LLMResponse:
        started = time.monotonic()
        try:
            key = _key(settings)
            response = asyncio.run(self._generate(prompt, settings, key))
            raw = response.text
            if not isinstance(raw, str) or not raw:
                raise ValueError("Response has no usable text")
            usage = getattr(response, "usage_metadata", None)
            inputs = _count(getattr(usage, "prompt_token_count", None))
            candidates = _count(getattr(usage, "candidates_token_count", None))
            thoughts = _count(getattr(usage, "thoughts_token_count", None))
            total = _count(getattr(usage, "total_token_count", None))
            outputs = None if candidates is None else candidates + (thoughts or 0)
            if total is not None and inputs is not None:
                if total < inputs:
                    raise ValueError("Inconsistent usage metadata")
                outputs = max(outputs or 0, total - inputs)
            return LLMResponse(
                raw_response=raw, model_returned=getattr(response, "model_version", None),
                # Gemini supplies one resolved model-version field, not two
                # independent model/build identifiers. Do not invent another.
                model_version_returned=None, input_tokens=inputs, output_tokens=outputs,
                latency_seconds=time.monotonic() - started, estimated_cost=None,
            )
        except ClientError:
            raise
        except Exception as exc:
            raise ClientError(f"Google request failed ({type(exc).__name__})") from None
