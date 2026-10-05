"""Mock SDK-shape checks; no SDK installation, credentials, or network needed."""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from vice_sysadmin.clients import GoogleClient, OpenAIClient, strict_schema
from vice_sysadmin.budget import Budget
from vice_sysadmin.interfaces import ClientError
from vice_sysadmin.mocks import FakeClock, MockClient
from vice_sysadmin.prompts import PromptBook
from vice_sysadmin.schemas import Audit, Proposal, load_config
from vice_sysadmin.structured import request_structured

ROOT = Path(__file__).resolve().parents[1]


def test_openai_arguments_retry_timeout_and_returned_metadata(monkeypatch):
    calls = []
    class SDK:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
            self.responses = self
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def create(self, **kwargs):
            calls.append(("create", kwargs))
            return SimpleNamespace(output_text='{"command":"id"}', model="returned-not-requested",
                                   error=None, usage=SimpleNamespace(input_tokens=5, output_tokens=6))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=SDK))
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    settings = load_config(ROOT / "config.yaml").proposer.model_copy(update={"output_format": "json_schema"})
    response = OpenAIClient().generate("prompt", settings)
    assert calls[0][1]["max_retries"] == 0
    assert calls[0][1]["timeout"] == settings.timeout_seconds
    assert calls[1][1]["model"] == settings.model
    assert calls[1][1]["max_output_tokens"] == settings.max_tokens
    assert calls[1][1]["temperature"] == settings.temperature
    assert calls[1][1]["top_p"] == settings.top_p
    assert calls[1][1]["text"]["format"]["strict"] is True
    assert response.model_returned == "returned-not-requested"
    assert response.model_version_returned is None and response.estimated_cost is None


def test_google_retry_timeout_and_reasoning_usage(monkeypatch):
    calls = []
    class SDK:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
            self.aio, self.models = self, self
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def generate_content(self, **kwargs):
            calls.append(("generate", kwargs))
            return SimpleNamespace(text='{"verdict":"reject"}', model_version="returned-version",
                                   usage_metadata=SimpleNamespace(prompt_token_count=10,
                                       candidates_token_count=5, thoughts_token_count=7, total_token_count=22))
        def close(self):
            calls.append(("close", {}))
    types = SimpleNamespace(HttpOptions=lambda **kwargs: kwargs, HttpRetryOptions=lambda **kwargs: kwargs,
                            GenerateContentConfig=lambda **kwargs: kwargs)
    genai = SimpleNamespace(Client=SDK, types=types)
    monkeypatch.setitem(sys.modules, "google", SimpleNamespace(genai=genai))
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    settings = load_config(ROOT / "config.yaml").heterogeneous_auditor
    response = GoogleClient().generate("prompt", settings)
    assert calls[0][1]["http_options"]["retry_options"] == {"attempts": 1}
    assert calls[0][1]["http_options"]["timeout"] == int(settings.timeout_seconds * 1000)
    assert calls[1][1]["model"] == settings.model
    assert calls[1][1]["config"]["max_output_tokens"] == settings.max_tokens
    assert "response_mime_type" not in calls[1][1]["config"]  # auto uses plain text
    assert response.output_tokens == 12 and response.input_tokens == 10
    assert response.model_returned == "returned-version" and response.model_version_returned is None
    assert [kind for kind, _ in calls].count("generate") == 1


def test_sdk_error_is_sanitized_and_not_retried(monkeypatch):
    count = []
    async def broken(self, prompt, settings, key):
        count.append(1)
        raise RuntimeError("secret-that-must-not-escape")
    monkeypatch.setattr(OpenAIClient, "_generate", broken)
    monkeypatch.setenv("OPENAI_API_KEY", "secret-that-must-not-escape")
    with pytest.raises(ClientError) as error:
        OpenAIClient().generate("prompt", load_config(ROOT / "config.yaml").proposer)
    assert "secret-that-must-not-escape" not in str(error.value)
    assert len(count) == 1


@pytest.mark.parametrize("client,role,env", [
    (OpenAIClient, "proposer", "OPENAI_API_KEY"),
    (GoogleClient, "heterogeneous_auditor", "GEMINI_API_KEY"),
])
def test_missing_key_fails_without_sdk_call(monkeypatch, client, role, env):
    monkeypatch.delenv(env, raising=False)
    with pytest.raises(ClientError, match="unset or empty"):
        client().generate("prompt", getattr(load_config(ROOT / "config.yaml"), role))


@pytest.mark.parametrize("schema", [Proposal, Audit])
def test_strict_schema_requires_every_declared_property(schema):
    converted = strict_schema(schema)
    assert set(converted["required"]) == set(converted["properties"])
    assert converted["additionalProperties"] is False
    assert all("default" not in value for value in converted["properties"].values())


def test_schema_bytes_are_reserved_without_altering_prompt():
    config = load_config(ROOT / "config.yaml")
    settings = config.proposer.model_copy(update={"output_format": "json_schema"})
    seen = []
    class RecordingBudget(Budget):
        def reserve(self, prompt, settings):
            seen.append(prompt)
            return super().reserve(prompt, settings)
    client = MockClient(ROOT / "fixtures/proposer.json")
    request_structured(client, settings, "original prompt", Proposal,
                       budget=RecordingBudget(config, FakeClock()), prompts=PromptBook(),
                       emit=lambda *args, **kwargs: None, role="proposer")
    assert len(seen[0]) > len("original prompt")
    assert client.calls[0][0] == "original prompt"
