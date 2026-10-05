# SDK and API disclosure — iteration 3

Checked on 2026-09-28 through public documentation, release metadata, and the
Google SDK's tagged source. No package was installed and no provider request was
made. Model names and prices must come from the operator.

## Selected SDK surfaces

| Package | Pin | Calls and fields used |
| --- | --- | --- |
| openai | 3.20.0 | AsyncOpenAI(max_retries=0, timeout=...), responses.create(model, input, temperature, top_p, max_output_tokens, store=False); output_text, model, usage.input_tokens/output_tokens. |
| google-genai | 2.25.0 | Client(api_key, enterprise=False, http_options).aio; models.generate_content(model, contents, config); HttpOptions.timeout in milliseconds and HttpRetryOptions(attempts=1); response.text/model_version/usage_metadata. |

[Official OpenAI Python reference](https://developers.openai.com/api/reference/python)
documents retry disabling, async use, and timeout configuration.
[Responses creation reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
documents the request surface.
[Google SDK documentation](https://googleapis.github.io/python-genai/)
and [the pinned client source](https://github.com/googleapis/python-genai/blob/v2.25.0/google/genai/client.py)
establish the Google client/context interface.
[The pinned types source](https://github.com/googleapis/python-genai/blob/v2.25.0/google/genai/types.py)
establishes retry options, time units, and response/usage fields.

OpenAI uses its returned model string, with no fabricated separate build.
Gemini returns one model_version field; it is logged as the resolved model
identifier, and no second identifier is invented. Candidate/thought usage is
included in output accounting; missing metadata remains null.

## Uncertainties that are not silently guessed

The configured models do not exist in this artifact until the operator chooses
them. Therefore support for temperature, top_p, JSON mode, the accepted JSON
Schema subset, and billed reasoning-token ceilings is NOT established here.
Auto output mode starts with text and the schema in the existing prompt.
Explicit json/json_schema is an operator-reviewed capability choice.

There is no hidden fallback API call. If an explicitly selected mode or generation
parameter is rejected, the single attempt becomes ClientError and the orchestrator
handles any configured retry with its own log and budget. SDK errors expose only
a sanitized exception class, never the SDK error body or credential.

An asyncio deadline supplements each SDK's transport timeout. These adapters use
asyncio.run from a synchronous CLI; calling them inside another running event loop
is unsupported. Client cancellation cannot prove that provider-side computation
or billing stopped. Future model selections must meet Budget's reservation
contract; provider-specific reasoning costs cannot be inferred from a name.

## Wheel evidence and installation scope

[OpenAI release metadata](https://pypi.org/pypi/openai/3.20.0/json) and
[Google SDK release metadata](https://pypi.org/pypi/google-genai/2.25.0/json)
list universal SDK wheels. The table below records every new pinned package and
one wheel relevant to ordinary CPython 3.14 on Linux x86_64. ABI3 wheels are usable
on later compatible CPython versions. Exact filenames and dependency metadata
are saved in iteration-3-dependency-evidence.json.

This is metadata evidence, not a tested installation or resolver result. The
live lock was assembled from those constraints and retains the known baseline
freeze. It rejects source builds for the new package names. PyYAML 6.0.2 is the
existing, explicitly disclosed pure-Python source-build exception; it was not
upgraded or represented as having a cp314 wheel.

| Package | Version | Matching wheel example |
| --- | --- | --- |
| anyio | 4.15.1 | anyio-4.15.1-py3-none-any.whl |
| certifi | 2026.7.22 | certifi-2026.7.22-py3-none-any.whl |
| cffi | 2.1.1 | cffi-2.1.1-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl |
| charset-normalizer | 3.5.1 | charset_normalizer-3.5.1-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl |
| cryptography | 50.0.1 | cryptography-50.0.1-cp311-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl |
| distro | 1.9.0 | distro-1.9.0-py3-none-any.whl |
| google-auth | 2.58.1 | google_auth-2.58.1-py3-none-any.whl |
| google-genai | 2.25.0 | google_genai-2.25.0-py3-none-any.whl |
| h11 | 0.16.0 | h11-0.16.0-py3-none-any.whl |
| httpcore | 1.0.9 | httpcore-1.0.9-py3-none-any.whl |
| httpcore2 | 2.13.1 | httpcore2-2.13.1-py3-none-any.whl |
| httpx | 0.28.1 | httpx-0.28.1-py3-none-any.whl |
| httpx2 | 2.13.1 | httpx2-2.13.1-py3-none-any.whl |
| idna | 3.20 | idna-3.20-py3-none-any.whl |
| jiter | 0.17.0 | jiter-0.17.0-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl |
| openai | 3.20.0 | openai-3.20.0-py3-none-any.whl |
| pyasn1 | 0.6.4 | pyasn1-0.6.4-py3-none-any.whl |
| pyasn1-modules | 0.4.2 | pyasn1_modules-0.4.2-py3-none-any.whl |
| pycparser | 3.0 | pycparser-3.0-py3-none-any.whl |
| requests | 2.34.2 | requests-2.34.2-py3-none-any.whl |
| sniffio | 1.3.1 | sniffio-1.3.1-py3-none-any.whl |
| tenacity | 9.1.4 | tenacity-9.1.4-py3-none-any.whl |
| truststore | 0.10.4 | truststore-0.10.4-py3-none-any.whl |
| urllib3 | 2.8.0 | urllib3-2.8.0-py3-none-any.whl |
| websockets | 16.1.1 | websockets-16.1.1-cp314-cp314-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl |
