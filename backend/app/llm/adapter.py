"""Anthropic LLM adapter — the only module in the backend that imports `anthropic`.

Contract (ClaimOS LLM integration research, art_hxr3LsdW):
- complete_structured(): schema-constrained output via `messages.parse`, returning a
  validated Pydantic model instance.
- stream(): async generator of typed StreamEvent values, bridged to SSE by callers.
- Typed exceptions with exponential backoff + jitter on transient errors, bounded by a
  hard retry cap. Refusals and schema violations never retry.

Configuration (env):
- ANTHROPIC_API_KEY  — required on the first real call (client is built lazily, so
  importing this module never needs a key)
- LLM_MODEL_INTAKE / LLM_MODEL_POLICY / LLM_MODEL_DOCUMENT /
  LLM_MODEL_ELIGIBILITY / LLM_MODEL_DECISION — per-agent model aliases;
  defaults: claude-haiku-4-5 for intake/document extraction, claude-sonnet-5 for
  policy/eligibility/decision
- LLM_MAX_RETRIES    — retry cap on transient errors (default 3)
- LLM_TIMEOUT_S      — per-request timeout in seconds (default 60)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections.abc import AsyncIterator
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.usage import UsageLogger

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MAX_TOKENS = 2048
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT_S = 60.0
DEFAULT_BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 8.0

# Model tiering: Haiku for high-volume extraction, Sonnet for judgment-heavy stages.
DEFAULT_MODELS: dict[str, str] = {
    "intake": "claude-haiku-4-5",
    "document": "claude-haiku-4-5",
    "fraud": "claude-haiku-4-5",
    "policy": "claude-sonnet-5",
    "eligibility": "claude-sonnet-5",
    "decision": "claude-sonnet-5",
}


class LLMError(Exception):
    """Base class for adapter errors; the pipeline marks the agent step failed."""


class LLMTimeout(LLMError):
    """Request exceeded the configured timeout, through all retries."""


class LLMRefusal(LLMError):
    """The model declined the request (stop_reason == 'refusal'). Never retried."""


class LLMSchemaValidationError(LLMError):
    """The response did not satisfy the requested Pydantic schema."""


class LLMRateLimited(LLMError):
    """Rate limited (429) past the retry cap."""


class StreamEvent(BaseModel):
    """Typed stream event — the SSE bridge: type is 'text_delta' | 'done' | 'error'."""

    type: str
    text: str | None = None
    usage: dict | None = None


def load_model_config(env: dict[str, str] | None = None) -> dict[str, str]:
    """Per-agent model aliases from env, falling back to the tier defaults."""
    source = os.environ if env is None else env
    return {
        agent: source.get(f"LLM_MODEL_{agent.upper()}", default)
        for agent, default in DEFAULT_MODELS.items()
    }


def _is_temperature_deprecation(exc: Exception) -> bool:
    """True when the API rejected the call because temperature is deprecated.

    Message-based on purpose: the 400 body ("`temperature` is deprecated for
    this model") is stable across SDK versions, while the exception type is
    not — and the degradation path is harmless even on a false positive (the
    retry without the parameter either succeeds or surfaces the real error).
    """
    message = str(exc).lower()
    return "temperature" in message and "deprecated" in message


def _transient_kind(exc: Exception) -> str | None:
    """Classify an SDK error: None = non-retryable, else the transient category.

    Order matters: timeout and rate-limit errors are subclasses of the broader types.
    """
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limit"
    if isinstance(exc, anthropic.InternalServerError):
        return "server"
    if isinstance(exc, anthropic.APIStatusError) and exc.response.status_code >= 500:
        return "server"
    if isinstance(exc, anthropic.APIConnectionError):
        return "connection"
    return None


def _terminal_error(kind: str, exc: Exception) -> LLMError:
    """Map an exhausted transient category to its typed exception."""
    if kind == "timeout":
        return LLMTimeout(f"LLM request timed out past retry cap: {exc}")
    if kind == "rate_limit":
        return LLMRateLimited(f"LLM rate limited past retry cap: {exc}")
    return LLMError(f"LLM transient failure past retry cap ({kind}): {exc}")


class LLMAdapter:
    """Wraps anthropic.AsyncAnthropic; agents never see the SDK."""

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        models: dict[str, str] | None = None,
        max_retries: int | None = None,
        timeout_s: float | None = None,
        usage_logger: UsageLogger | None = None,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
    ):
        self._client = client
        self._models = models if models is not None else load_model_config()
        self._max_retries = (
            max_retries
            if max_retries is not None
            else int(os.environ.get("LLM_MAX_RETRIES", DEFAULT_MAX_RETRIES))
        )
        self._timeout_s = (
            timeout_s
            if timeout_s is not None
            else float(os.environ.get("LLM_TIMEOUT_S", DEFAULT_TIMEOUT_S))
        )
        self._usage = usage_logger if usage_logger is not None else UsageLogger()
        self._backoff_base_s = backoff_base_s
        # Models that reject the temperature parameter (deprecated on newer
        # Claude models): per-agent temperatures degrade to the model default.
        self._temperature_unsupported: set[str] = set()

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Lazily built SDK client — never constructed at import or boot time."""
        if self._client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise LLMError("ANTHROPIC_API_KEY is not configured")
            # SDK retries disabled on purpose: this adapter owns the retry policy
            # and the hard cap, so behavior is testable and bounded in one place.
            self._client = anthropic.AsyncAnthropic(
                api_key=api_key, max_retries=0, timeout=self._timeout_s
            )
        return self._client

    def model_for(self, agent: str) -> str:
        """Resolve the configured model alias for an agent (intake..decision)."""
        return self._models[agent]

    def _backoff_delay(self, attempt: int) -> float:
        """Full-jitter exponential backoff: uniform(0, min(cap, base * 2**attempt))."""
        return random.uniform(0, min(BACKOFF_CAP_S, self._backoff_base_s * 2**attempt))

    async def complete_structured(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        output_schema: type[T],
        agent: str | None = None,
        claim_id: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
    ) -> T:
        """Schema-guaranteed result.

        `system` is a string or a list of text content blocks (the prompt-caching
        preamble passes cache_control-marked blocks); `temperature` is omitted
        from the call when None. Raises LLMRefusal / LLMTimeout / LLMRateLimited /
        LLMSchemaValidationError / LLMError; retries transient failures with
        backoff + jitter up to the cap.
        """
        started = time.perf_counter()
        attempt = 0
        while True:
            use_temperature = (
                temperature is not None and model not in self._temperature_unsupported
            )
            try:
                call_kwargs: dict = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": messages,
                    "output_format": output_schema,
                }
                if use_temperature:
                    # SDK 1.6.0's messages.parse() has no temperature kwarg;
                    # extra_body is its documented escape hatch for params
                    # missing from the helper's typed signature.
                    call_kwargs["extra_body"] = {"temperature": temperature}
                response = await self.client.messages.parse(**call_kwargs)
                break
            except Exception as exc:
                if use_temperature and _is_temperature_deprecation(exc):
                    # Deterministic model-level 400, not a transient fault:
                    # remember it and retry immediately without the parameter
                    # instead of failing the claim.
                    self._temperature_unsupported.add(model)
                    logger.warning(
                        "temperature_unsupported model=%s — per-agent temperatures "
                        "dropped for this model; using the model default",
                        model,
                    )
                    continue
                kind = _transient_kind(exc)
                if kind is None:
                    raise LLMError(f"LLM request failed: {exc}") from exc
                if attempt >= self._max_retries:
                    raise _terminal_error(kind, exc) from exc
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "LLM transient error (%s), attempt %d/%d — retrying in %.2fs",
                    kind, attempt + 1, self._max_retries, delay,
                )
                attempt += 1
                await asyncio.sleep(delay)

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage", None)
        try:
            result = self._coerce_response(response, output_schema, agent)
        finally:
            await self._log_usage(
                agent=agent, model=model, usage=usage,
                latency_ms=latency_ms, claim_id=claim_id,
                call_type="complete_structured",
            )
        return result

    def _coerce_response(self, response, output_schema: type[T], agent: str | None) -> T:
        """Refusal check + one-point schema validation of the parsed output."""
        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMRefusal(
                f"LLM refused the request{f' for agent {agent!r}' if agent else ''}"
            )
        parsed = getattr(response, "parsed_output", None)
        if not isinstance(parsed, output_schema):
            try:
                parsed = output_schema.model_validate(parsed)
            except ValidationError as exc:
                raise LLMSchemaValidationError(
                    f"LLM output failed {output_schema.__name__} validation: {exc}"
                ) from exc
        return parsed

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        agent: str | None = None,
        claim_id: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> AsyncIterator[StreamEvent]:
        """Token deltas for SSE. Transient failures retry until the first delta is
        emitted; after that, and for terminal errors, an 'error' event is yielded."""
        started = time.perf_counter()
        emitted = False
        attempt = 0
        while True:
            try:
                async with self.client.messages.stream(
                    model=model, max_tokens=max_tokens, system=system, messages=messages
                ) as stream:
                    async for text in stream.text_stream:
                        emitted = True
                        yield StreamEvent(type="text_delta", text=text)
                    final = await stream.get_final_message()

                latency_ms = int((time.perf_counter() - started) * 1000)
                usage = getattr(final, "usage", None)
                await self._log_usage(
                    agent=agent, model=model, usage=usage,
                    latency_ms=latency_ms, claim_id=claim_id, call_type="stream",
                )
                if getattr(final, "stop_reason", None) == "refusal":
                    yield StreamEvent(type="error", text="LLM refused the request")
                    return
                usage_dict = usage.model_dump() if usage is not None else None
                yield StreamEvent(type="done", usage=usage_dict)
                return
            except Exception as exc:
                kind = _transient_kind(exc)
                if kind is None:
                    logger.exception("LLM stream failed")
                    yield StreamEvent(type="error", text=f"LLM stream failed: {exc}")
                    return
                if attempt >= self._max_retries or emitted:
                    # Retrying after deltas reached the consumer would duplicate text.
                    yield StreamEvent(
                        type="error",
                        text=f"LLM stream interrupted ({kind}): {exc}",
                    )
                    return
                delay = self._backoff_delay(attempt)
                logger.warning(
                    "LLM stream transient error (%s), attempt %d/%d — retrying in %.2fs",
                    kind, attempt + 1, self._max_retries, delay,
                )
                attempt += 1
                await asyncio.sleep(delay)

    async def _log_usage(
        self, *, agent, model, usage, latency_ms, claim_id, call_type
    ) -> None:
        await self._usage.log(
            agent=agent,
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            latency_ms=latency_ms,
            claim_id=claim_id,
            call_type=call_type,
        )


_adapter: LLMAdapter | None = None


def get_adapter() -> LLMAdapter:
    """Process-wide adapter (lazy client construction keeps import/boot key-free)."""
    global _adapter
    if _adapter is None:
        _adapter = LLMAdapter()
    return _adapter
