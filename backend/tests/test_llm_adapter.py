"""LLM adapter tests — mocked AsyncAnthropic, no live API key required."""

import asyncio

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from app.llm import adapter as adapter_module
from app.llm.adapter import (
    LLMAdapter,
    LLMError,
    LLMRateLimited,
    LLMRefusal,
    LLMSchemaValidationError,
    LLMTimeout,
    load_model_config,
)
from app.usage import UsageLogger


class Box(BaseModel):
    """Minimal stand-in schema for adapter tests."""

    name: str
    score: int = 0


VALID_BOX = Box(name="ok", score=7)


class FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=20):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def model_dump(self):
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


class FakeParsedMessage:
    def __init__(self, parsed_output=None, stop_reason="end_turn", usage=None):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason
        self.usage = usage if usage is not None else FakeUsage()


class FakeMessages:
    """Scripted messages API: each entry is a response or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.parse_calls = []
        self.stream_calls = []

    async def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class FakeClient:
    def __init__(self, messages):
        self.messages = messages


class RecordingUsage(UsageLogger):
    def __init__(self):
        self.records = []

    async def log(self, **kwargs):
        self.records.append(kwargs)


def make_adapter(script, max_retries=2, usage=None, backoff_base_s=0.0):
    return LLMAdapter(
        client=FakeClient(FakeMessages(script)),
        max_retries=max_retries,
        usage_logger=usage if usage is not None else RecordingUsage(),
        backoff_base_s=backoff_base_s,
    )


def sdk_error(exc_class, status=None):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    if status is None:  # connection-level errors take a request, not a response
        return exc_class(request=request)
    return exc_class(
        f"{exc_class.__name__}", response=httpx.Response(status, request=request), body=None
    )


def collect(stream_gen):
    async def run():
        return [event async for event in stream_gen]

    return asyncio.run(run())


# ---------- complete_structured ----------

def test_complete_structured_returns_validated_model():
    usage = RecordingUsage()
    adapter = make_adapter(
        [FakeParsedMessage(parsed_output=VALID_BOX)], usage=usage
    )

    result = asyncio.run(
        adapter.complete_structured(
            model="claude-sonnet-5",
            system="sys prompt",
            messages=[{"role": "user", "content": "hi"}],
            output_schema=Box,
            agent="decision",
            claim_id="CLM-1",
        )
    )

    assert result == VALID_BOX
    call = adapter._client.messages.parse_calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["system"] == "sys prompt"
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert usage.records[0]["model"] == "claude-sonnet-5"
    assert usage.records[0]["input_tokens"] == 10
    assert usage.records[0]["output_tokens"] == 20
    assert usage.records[0]["claim_id"] == "CLM-1"
    assert usage.records[0]["agent"] == "decision"


def test_schema_validation_failure_raises_typed_error():
    adapter = make_adapter([FakeParsedMessage(parsed_output={"name": 12345, "score": "x"})])

    with pytest.raises(LLMSchemaValidationError):
        asyncio.run(
            adapter.complete_structured(
                model="claude-haiku-4-5", system="s", messages=[], output_schema=Box
            )
        )
    # Schema failures are deterministic: exactly one attempt, no retries.
    assert len(adapter._client.messages.parse_calls) == 1


def test_refusal_raises_and_never_retries():
    usage = RecordingUsage()
    adapter = make_adapter(
        [FakeParsedMessage(parsed_output=None, stop_reason="refusal")], usage=usage
    )

    with pytest.raises(LLMRefusal):
        asyncio.run(
            adapter.complete_structured(
                model="m", system="s", messages=[], output_schema=Box, agent="intake"
            )
        )
    assert len(adapter._client.messages.parse_calls) == 1
    # Refused calls still consumed tokens: they are logged for cost attribution.
    assert len(usage.records) == 1


def test_retry_then_success_on_transient_errors():
    adapter = make_adapter(
        [sdk_error(anthropic.RateLimitError, 429), sdk_error(anthropic.InternalServerError, 500),
         FakeParsedMessage(parsed_output=VALID_BOX)],
        max_retries=2,
    )

    result = asyncio.run(
        adapter.complete_structured(model="m", system="s", messages=[], output_schema=Box)
    )

    assert result == VALID_BOX
    assert len(adapter._client.messages.parse_calls) == 3


def test_rate_limit_past_cap_raises_llm_rate_limited():
    adapter = make_adapter([sdk_error(anthropic.RateLimitError, 429)] * 5, max_retries=2)

    with pytest.raises(LLMRateLimited):
        asyncio.run(
            adapter.complete_structured(model="m", system="s", messages=[], output_schema=Box)
        )
    # Hard retry cap: initial attempt + max_retries retries.
    assert len(adapter._client.messages.parse_calls) == 3


def test_timeout_past_cap_raises_llm_timeout():
    adapter = make_adapter(
        [sdk_error(anthropic.APITimeoutError)] * 5, max_retries=1
    )

    with pytest.raises(LLMTimeout):
        asyncio.run(
            adapter.complete_structured(model="m", system="s", messages=[], output_schema=Box)
        )
    assert len(adapter._client.messages.parse_calls) == 2


def test_server_error_past_cap_raises_base_llm_error():
    adapter = make_adapter([sdk_error(anthropic.InternalServerError, 500)] * 5, max_retries=1)

    with pytest.raises(LLMError) as excinfo:
        asyncio.run(
            adapter.complete_structured(model="m", system="s", messages=[], output_schema=Box)
        )
    assert not isinstance(excinfo.value, (LLMTimeout, LLMRateLimited))
    assert len(adapter._client.messages.parse_calls) == 2


def test_non_retryable_error_fails_immediately():
    adapter = make_adapter(
        [sdk_error(anthropic.BadRequestError, 400), FakeParsedMessage(parsed_output=VALID_BOX)],
        max_retries=3,
    )

    with pytest.raises(LLMError):
        asyncio.run(
            adapter.complete_structured(model="m", system="s", messages=[], output_schema=Box)
        )
    assert len(adapter._client.messages.parse_calls) == 1


def _temperature_deprecation_error():
    """The real 400 body the API returns for models that deprecate temperature."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': '`temperature` is deprecated for this model.'}}",
        response=httpx.Response(400, request=request),
        body=None,
    )


def test_temperature_deprecation_degrades_without_temperature():
    """A model that deprecates the temperature parameter 400s the first call;
    the adapter retries immediately without it and remembers the model, so the
    per-agent temperature feature degrades instead of failing every claim."""
    adapter = make_adapter(
        [
            _temperature_deprecation_error(),
            FakeParsedMessage(parsed_output=VALID_BOX),
            FakeParsedMessage(parsed_output=VALID_BOX),
        ],
        max_retries=2,
    )

    result = asyncio.run(
        adapter.complete_structured(
            model="claude-sonnet-4-20250514",
            system="s",
            messages=[],
            output_schema=Box,
            temperature=0.2,
        )
    )
    assert result == VALID_BOX

    calls = adapter._client.messages.parse_calls
    assert len(calls) == 2
    assert calls[0]["extra_body"] == {"temperature": 0.2}
    assert "extra_body" not in calls[1]

    # The deprecation is cached per model: a later call never sends it again.
    asyncio.run(
        adapter.complete_structured(
            model="claude-sonnet-4-20250514",
            system="s",
            messages=[],
            output_schema=Box,
            temperature=0.2,
        )
    )
    assert "extra_body" not in calls[2]


def test_other_bad_requests_still_fail_immediately():
    """A 400 without the temperature-deprecation message is a real error."""
    adapter = make_adapter(
        [sdk_error(anthropic.BadRequestError, 400), FakeParsedMessage(parsed_output=VALID_BOX)],
        max_retries=3,
    )

    with pytest.raises(LLMError):
        asyncio.run(
            adapter.complete_structured(
                model="m", system="s", messages=[], output_schema=Box, temperature=0.2
            )
        )
    assert len(adapter._client.messages.parse_calls) == 1


# ---------- stream ----------

class FakeStreamHandle:
    def __init__(self, chunks, final=None, fail_with=None):
        self.chunks = chunks
        self.final = final if final is not None else FakeParsedMessage()
        self.fail_with = fail_with  # raised after emitting all chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    @property
    def text_stream(self):
        return self._iter()

    async def _iter(self):
        for chunk in self.chunks:
            yield chunk
        if self.fail_with is not None:
            raise self.fail_with

    async def get_final_message(self):
        return self.final


def test_stream_emits_text_deltas_then_done():
    usage = RecordingUsage()
    handle = FakeStreamHandle(["Hello ", "world"], final=FakeParsedMessage(usage=FakeUsage(3, 5)))
    adapter = make_adapter([handle], usage=usage)

    events = collect(adapter.stream(model="m", system="s", messages=[], claim_id="CLM-2"))

    assert [e.type for e in events] == ["text_delta", "text_delta", "done"]
    assert events[0].text == "Hello "
    assert events[2].usage == {"input_tokens": 3, "output_tokens": 5}
    assert usage.records[0]["call_type"] == "stream"
    assert usage.records[0]["claim_id"] == "CLM-2"


def test_stream_retries_transient_error_before_first_token():
    handle_ok = FakeStreamHandle(["abc"])
    adapter = make_adapter(
        [sdk_error(anthropic.RateLimitError, 429), handle_ok], max_retries=2
    )

    events = collect(adapter.stream(model="m", system="s", messages=[]))

    assert [e.type for e in events] == ["text_delta", "done"]
    assert len(adapter._client.messages.stream_calls) == 2


def test_stream_emits_error_event_after_first_token_without_retry():
    exc = sdk_error(anthropic.APITimeoutError)
    handle = FakeStreamHandle(["partial "], fail_with=exc)
    adapter = make_adapter([handle, FakeStreamHandle(["should not run"])], max_retries=2)

    events = collect(adapter.stream(model="m", system="s", messages=[]))

    assert [e.type for e in events] == ["text_delta", "error"]
    assert "timeout" in events[1].text
    # No retry once deltas reached the consumer (would duplicate text).
    assert len(adapter._client.messages.stream_calls) == 1


def test_stream_emits_error_event_past_retry_cap():
    adapter = make_adapter(
        [sdk_error(anthropic.RateLimitError, 429)] * 5, max_retries=1
    )

    events = collect(adapter.stream(model="m", system="s", messages=[]))

    assert [e.type for e in events] == ["error"]
    assert len(adapter._client.messages.stream_calls) == 2


# ---------- configuration ----------

def test_default_model_tiering():
    config = load_model_config(env={})

    assert config["intake"] == "claude-haiku-4-5"
    assert config["document"] == "claude-haiku-4-5"
    assert config["policy"] == "claude-sonnet-5"
    assert config["eligibility"] == "claude-sonnet-5"
    assert config["decision"] == "claude-sonnet-5"


def test_env_overrides_per_agent_model(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_DECISION", "claude-opus-4-5")
    monkeypatch.setenv("LLM_MODEL_INTAKE", "claude-haiku-4-5")

    config = load_model_config()

    assert config["decision"] == "claude-opus-4-5"
    assert config["intake"] == "claude-haiku-4-5"
    assert config["policy"] == "claude-sonnet-5"  # unset var keeps the tier default


def test_adapter_reads_retry_and_timeout_from_env(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RETRIES", "7")
    monkeypatch.setenv("LLM_TIMEOUT_S", "12.5")

    configured = LLMAdapter()

    assert configured._max_retries == 7
    assert configured._timeout_s == 12.5


def test_model_for_unknown_agent_raises_keyerror():
    adapter = make_adapter([])

    with pytest.raises(KeyError):
        adapter.model_for("nonexistent")


def test_backoff_delay_is_bounded_and_grows():
    adapter = make_adapter([], backoff_base_s=0.5)

    first = adapter._backoff_delay(0)
    late = adapter._backoff_delay(10)

    assert 0 <= first <= 0.5
    assert late <= adapter_module.BACKOFF_CAP_S  # hard cap
    # With full jitter we can only assert the ceiling grows to the cap.
    assert adapter._backoff_delay(20) <= adapter_module.BACKOFF_CAP_S
