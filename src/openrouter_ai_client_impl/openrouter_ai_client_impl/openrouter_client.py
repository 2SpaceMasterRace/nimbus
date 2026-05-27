"""Concrete :class:`~ai_client_api.AIClient` backed by OpenRouter via pydantic-ai.

The client delegates the agentic tool-calling loop to **pydantic-ai**'s
``Agent.run_sync()`` instead of managing it by hand. Our layer adds:

* Primary-to-fallback model switching on 429/5xx, with a visible
  ``model_fallback`` event so the CLI can display a ``↻`` indicator.
* Synchronous event dispatch (``request_started``, ``tool_call_started``,
  ``tool_call_completed``, ``request_completed``) to registered listeners.
* Tool-result sandboxing: results are wrapped in ``<tool_result
  source="untrusted">`` tags and truncated before being fed back to the model.
* A ring buffer of recent "raw" model responses for the CLI ``/debug`` command.
* Bidirectional translation between our ``Conversation`` format and
  pydantic-ai's ``ModelMessage`` list.

Design notes:

* ``Agent`` is constructed fresh for every ``send_message`` call so that tools,
  system prompt, and model are always in sync with the current config. The
  overhead is negligible; the expensive part is the HTTP round-trip.
* Tool results returned by handlers are strings (possibly JSON-ified dicts).
  pydantic-ai feeds them back to the model verbatim. We sandbox them before
  returning from the handler so the model-facing copy is always truncated.
* We keep our own fallback logic (``_run_with_fallback``) instead of pydantic-
  ai's ``FallbackModel`` because the built-in fallback is silent — we need to
  emit a ``model_fallback`` event so the CLI can show the ``↻`` glyph.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import TYPE_CHECKING, Any, cast

import openai
import orjson
import structlog

try:
    import sentry_sdk as _sentry_sdk
except ImportError:  # pragma: no cover - sentry is an optional runtime dependency
    _sentry_sdk = None  # type: ignore[assignment]
from pydantic_ai import Agent, AgentRunResultEvent, RunContext
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import Tool as PAITool
from pydantic_ai.usage import UsageLimits
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from ai_client_api import (
    AgentEvent,
    AIAuthenticationError,
    AIClient,
    AIProviderError,
    AIRateLimitError,
    AIResponse,
    AIStepBudgetExceededError,
    AIStreamEvent,
    AITimeoutError,
    AIToolArgsInvalidError,
    Conversation,
    EventListener,
    Role,
    StopReason,
    TokenUsage,
    Tool,
    ToolCallRecord,
    ToolCallRequest,
    normalize_tools,
)
from openrouter_ai_client_impl.config import (
    DEFAULT_MAX_STEPS,
    DEFAULT_SYSTEM_PROMPT,
    OpenRouterConfig,
)
from openrouter_ai_client_impl.pricing import estimate_cost_usd

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from pydantic_ai.models import Model as PAIModel

log: Any = structlog.get_logger()

# Cap each tool result (as fed back to the model) to this many chars.
_TOOL_RESULT_MAX_CHARS = 4000

# Minimum value of ``max_steps`` we'll accept.
_MIN_MAX_STEPS = 1

# HTTP status codes >= this are treated as retryable via the fallback model.
_HTTP_SERVER_ERROR_MIN = 500

# Regex matching ASCII control characters that must be stripped from tool
# results before feeding them back to the model (FM7: prompt injection via
# tool results).  We keep \t (\x09), \n (\x0a), and \r (\x0d) because they
# are legitimate whitespace.  Everything else in the C0 block, plus DEL
# (\x7f), is stripped.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# pydantic-ai sometimes wraps 429 as ModelHTTPError rather than the openai
# RateLimitError, so we check the status code explicitly in that branch.
_HTTP_RATE_LIMIT_STATUS = 429

# Exception types where a retry-with-backoff is the right first move. These
# are transport-layer failures — the request never reached the model or never
# got a response — so retrying the same call is safe and idempotent. Crucially
# we do NOT retry RateLimitError or 5xx here: those still flow to the existing
# fallback-model branch, which is a stronger response than another attempt
# against the same model. Auth errors propagate immediately (never retryable).
_TRANSIENT_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    openai.APIConnectionError,
    openai.APITimeoutError,
)

# Exponential backoff bounds for transient transport errors. The 0.5s..4s
# window is small on purpose: this loop sits in the user-facing turn path, so
# we'd rather give up early and try the fallback model than burn 30s
# retrying the primary.
_RETRY_BACKOFF_MULTIPLIER = 0.5
_RETRY_BACKOFF_MAX_SECONDS = 4.0


def _log_retry_attempt(state: RetryCallState) -> None:
    """Log each retry attempt so the dashboard can correlate spikes."""
    outcome = state.outcome
    if outcome is None or not outcome.failed:
        return
    log.warning(
        "openrouter_transient_retry",
        attempt=state.attempt_number,
        error_type=type(outcome.exception()).__name__,
    )


class OpenRouterClient(AIClient):
    """OpenRouter-backed AI client powered by pydantic-ai."""

    def __init__(
        self,
        config: OpenRouterConfig,
        *,
        pai_model: PAIModel | None = None,
        pai_fallback_model: PAIModel | None = None,
    ) -> None:
        """Construct a client.

        Args:
            config: Immutable configuration. See :class:`OpenRouterConfig`.
            pai_model: Optional pre-built pydantic-ai model for the primary
                slot. Tests inject a ``FunctionModel`` here so no real HTTP
                is needed; production code leaves it ``None``.
            pai_fallback_model: Same as ``pai_model`` but for the fallback
                slot. Only consulted when ``config.fallback_model`` is set.

        """
        self._config = config
        self._pai_model = pai_model
        self._pai_fallback_model = pai_fallback_model
        self._listeners: list[EventListener] = []
        # Ring buffer for /debug: recent model response summaries.
        self._last_raw_completions: list[dict[str, Any]] = []
        self._max_raw_completions = 5

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def on_event(self, listener: EventListener) -> None:
        """Register an event listener. Invoked synchronously in order."""
        self._listeners.append(listener)

    def emit(self, event: AgentEvent) -> None:
        """Dispatch an event to every registered listener.

        Listener exceptions are caught and logged; they must never interrupt
        the loop.
        """
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - listener isolation is the whole point
                log.warning("event_listener_raised", kind=event.kind, exc_info=True)

    def last_raw_completions(self) -> list[dict[str, Any]]:
        """Return a snapshot of recent model response summaries.

        Used by the CLI's ``/debug`` command to show what the model actually
        returned — invaluable when tool calls stop firing.
        """
        return list(self._last_raw_completions)

    def ping(self) -> bool:
        """Probe the provider with a minimal call. Never raises."""
        try:
            model = self._build_model(self._config.model)
            agent: Agent[None, str] = Agent(model, system_prompt="ping")
            agent.run_sync("ping")
        except Exception:  # noqa: BLE001 - ping returns False on any failure by contract
            log.debug("ping_failed", exc_info=True)
            return False
        return True

    def send_message(
        self,
        prompt: str | Conversation,
        *,
        tools: Sequence[Tool] | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        dry_run: bool = False,
        stream: bool = False,  # noqa: ARG002 - legacy sync flag; use stream_message() for true provider streaming
    ) -> AIResponse:
        """Run the agentic loop and return a terminal response."""
        if max_steps < _MIN_MAX_STEPS:
            msg = "max_steps must be >= 1"
            raise ValueError(msg)

        conv = self._as_conversation(prompt)
        tool_tuple = normalize_tools(tools)

        request_id = f"req-{int(time.time() * 1000)}"
        self.emit(
            AgentEvent(
                kind="request_started",
                payload={"request_id": request_id, "session_id": conv.session_id},
            )
        )

        t0 = time.monotonic()
        records: list[ToolCallRecord] = []

        # Extract the last user message as the pydantic-ai prompt; convert
        # all prior messages into pydantic-ai history format.
        user_prompt, prior_history = self._split_conv(conv)

        # Build pydantic-ai tools that emit our events and populate ``records``.
        pai_tools = [
            self._make_pai_tool(t, dry_run=dry_run, records=records) for t in tool_tuple
        ]

        # Get the system prompt from the conversation.
        conv_system = conv.system or DEFAULT_SYSTEM_PROMPT

        result, model_used, fallback_used = self._run_with_fallback(
            user_prompt=user_prompt,
            prior_history=prior_history,
            pai_tools=pai_tools,
            conv_system=conv_system,
            model_name=self._config.model,
            fallback_used=False,
            max_steps=max_steps,
        )

        # Apply new messages back to the conversation object so callers (the
        # CLI) see the complete updated history.
        final_text, step_count = self._apply_new_messages(conv, result.new_messages())

        # Capture raw summaries for /debug.
        self._capture_raw_from_result(result.new_messages(), model=model_used)

        usage_info = result.usage()
        usage = TokenUsage(
            input_tokens=usage_info.input_tokens or 0,
            output_tokens=usage_info.output_tokens or 0,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        stop_reason: StopReason = "end_turn"

        response = AIResponse(
            text=final_text,
            model=model_used,
            tokens=usage,
            tool_calls=tuple(records),
            latency_ms=latency_ms,
            stop_reason=stop_reason,
            steps=step_count,
            fallback_used=fallback_used,
            cost_usd_estimate=estimate_cost_usd(model_used, usage),
        )
        self.emit(
            AgentEvent(
                kind="request_completed",
                payload={
                    "request_id": request_id,
                    "latency_ms": latency_ms,
                    "tokens": usage.total,
                    "model": model_used,
                },
            )
        )
        return response

    async def stream_message(
        self,
        prompt: str | Conversation,
        *,
        tools: Sequence[Tool] | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        dry_run: bool = False,
    ) -> AsyncIterator[AIStreamEvent]:
        """Run the agentic loop and yield provider-backed stream events."""
        if max_steps < _MIN_MAX_STEPS:
            msg = "max_steps must be >= 1"
            raise ValueError(msg)

        conv = self._as_conversation(prompt)
        tool_tuple = normalize_tools(tools)
        request_id = f"req-{int(time.time() * 1000)}"
        sequence = 1

        start_event = AgentEvent(
            kind="request_started",
            payload={"request_id": request_id, "session_id": conv.session_id},
        )
        self.emit(start_event)
        yield AIStreamEvent(
            kind="request_started",
            sequence=sequence,
            payload=start_event.payload,
        )
        sequence += 1

        t0 = time.monotonic()
        records: list[ToolCallRecord] = []
        user_prompt, prior_history = self._split_conv(conv)
        pai_tools = [
            self._make_pai_tool(t, dry_run=dry_run, records=records) for t in tool_tuple
        ]
        conv_system = conv.system or DEFAULT_SYSTEM_PROMPT
        result: Any | None = None
        model_used = self._config.model
        fallback_used = False

        try:
            async for event in self._stream_pai_events(
                user_prompt=user_prompt,
                prior_history=prior_history,
                pai_tools=pai_tools,
                conv_system=conv_system,
                model_name=self._config.model,
                max_steps=max_steps,
            ):
                translated = _stream_event_from_pai(sequence, event)
                if isinstance(event, AgentRunResultEvent):
                    result = event.result
                if translated is not None:
                    yield translated
                    sequence += 1
        except Exception as exc:
            error = _translate_provider_exception(exc, max_steps=max_steps)
            yield AIStreamEvent(
                kind="error",
                sequence=sequence,
                payload={"request_id": request_id, "error": str(error)},
            )
            raise error from exc

        if result is None:
            msg = "stream completed without a final run result"
            error = AIProviderError(msg)
            yield AIStreamEvent(
                kind="error",
                sequence=sequence,
                payload={"request_id": request_id, "error": msg},
            )
            raise error

        final_text, step_count = self._apply_new_messages(conv, result.new_messages())
        self._capture_raw_from_result(result.new_messages(), model=model_used)
        usage_info = result.usage()
        usage = TokenUsage(
            input_tokens=usage_info.input_tokens or 0,
            output_tokens=usage_info.output_tokens or 0,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        response = AIResponse(
            text=final_text,
            model=model_used,
            tokens=usage,
            tool_calls=tuple(records),
            latency_ms=latency_ms,
            stop_reason="end_turn",
            steps=step_count,
            fallback_used=fallback_used,
            cost_usd_estimate=estimate_cost_usd(model_used, usage),
        )
        completed_event = AgentEvent(
            kind="request_completed",
            payload={
                "request_id": request_id,
                "latency_ms": latency_ms,
                "tokens": usage.total,
                "model": model_used,
            },
        )
        self.emit(completed_event)
        yield AIStreamEvent(
            kind="request_completed",
            sequence=sequence,
            payload={**completed_event.payload, "response": response},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_model(self, model_name: str) -> PAIModel:
        """Return the injected test model or build a real ``OpenAIModel``."""
        if self._pai_model is not None and model_name == self._config.model:
            return self._pai_model
        if (
            self._pai_fallback_model is not None
            and model_name == self._config.fallback_model
        ):
            return self._pai_fallback_model
        default_headers: dict[str, str] = {}
        if self._config.app_referer:
            default_headers["HTTP-Referer"] = self._config.app_referer
        if self._config.app_title:
            default_headers["X-Title"] = self._config.app_title
        # Thread attribution headers into the underlying openai client so
        # OpenRouter's dashboard correctly attributes requests to this app.
        # OpenAIProvider accepts a pre-built openai.AsyncOpenAI instance so
        # we can pass default_headers without patching internal plumbing.
        # An empty dict is harmless — the SDK treats it as "no extra headers".
        openai_client = openai.AsyncOpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            timeout=self._config.timeout_seconds,
            default_headers=default_headers,
        )
        provider = OpenAIProvider(openai_client=openai_client)
        return OpenAIModel(model_name, provider=provider)

    def _make_pai_tool(
        self,
        tool: Tool,
        *,
        dry_run: bool,
        records: list[ToolCallRecord],
    ) -> PAITool:
        """Wrap an :class:`~ai_client_api.Tool` as a pydantic-ai tool.

        The wrapper emits lifecycle events, handles dry-run mode, captures
        handler exceptions as structured records, and sandboxes results.
        """
        emit = self.emit

        def handler(ctx: RunContext[None], **kwargs: Any) -> str:  # noqa: ANN401
            _ts = int(time.monotonic() * 1000)
            call_id = ctx.tool_call_id or f"call-{tool.name}-{_ts}"
            t0 = time.monotonic()
            safe_arguments = _redact_tool_arguments(kwargs)
            emit(
                AgentEvent(
                    kind="tool_call_started",
                    payload={
                        "name": tool.name,
                        "arguments": safe_arguments,
                        "is_destructive": tool.is_destructive,
                    },
                )
            )
            if tool.is_destructive:
                _record_destructive_attempt(tool_name=tool.name, arguments=kwargs)
            if dry_run:
                summary = f"dry_run: would call {tool.name} with {kwargs!r}"
                latency_ms = int((time.monotonic() - t0) * 1000)
                emit(
                    AgentEvent(
                        kind="tool_call_completed",
                        payload={"name": tool.name, "success": True, "dry_run": True},
                    )
                )
                records.append(
                    ToolCallRecord(
                        id=call_id,
                        name=tool.name,
                        arguments=safe_arguments,
                        result_summary=summary,
                        success=True,
                        latency_ms=latency_ms,
                    )
                )
                return summary
            try:
                raw_result = tool.handler(**kwargs)
                summary = _format_result(raw_result)
                latency_ms = int((time.monotonic() - t0) * 1000)
                emit(
                    AgentEvent(
                        kind="tool_call_completed",
                        payload={
                            "name": tool.name,
                            "success": True,
                            "latency_ms": latency_ms,
                        },
                    )
                )
                records.append(
                    ToolCallRecord(
                        id=call_id,
                        name=tool.name,
                        arguments=safe_arguments,
                        result_summary=summary,
                        success=True,
                        latency_ms=latency_ms,
                    )
                )
                return _sandbox_result(summary)
            except AIToolArgsInvalidError as err:
                reason = "args_invalid"
                summary = f"{reason}: {err}"
                latency_ms = int((time.monotonic() - t0) * 1000)
                emit(
                    AgentEvent(
                        kind="tool_call_completed",
                        payload={"name": tool.name, "success": False, "reason": reason},
                    )
                )
                records.append(
                    ToolCallRecord(
                        id=call_id,
                        name=tool.name,
                        arguments=safe_arguments,
                        result_summary=summary,
                        success=False,
                        latency_ms=latency_ms,
                    )
                )
                # FM7: tool result text re-enters the model context, so error
                # summaries must be sandboxed exactly like success summaries —
                # exception messages can carry control characters or fake
                # tool-result markers crafted by an upstream attacker.
                return _sandbox_result(summary)
            except Exception as err:  # noqa: BLE001 - any tool error becomes a structured record
                reason = "error"
                summary = f"{reason}: {err}"
                latency_ms = int((time.monotonic() - t0) * 1000)
                emit(
                    AgentEvent(
                        kind="tool_call_completed",
                        payload={"name": tool.name, "success": False, "reason": reason},
                    )
                )
                records.append(
                    ToolCallRecord(
                        id=call_id,
                        name=tool.name,
                        arguments=safe_arguments,
                        result_summary=summary,
                        success=False,
                        latency_ms=latency_ms,
                    )
                )
                # FM7: see comment on the AIToolArgsInvalidError branch above.
                return _sandbox_result(summary)

        return PAITool.from_schema(
            function=handler,
            name=tool.name,
            description=tool.description,
            json_schema=dict(tool.parameters_schema),
            takes_ctx=True,
        )

    def _agent_run_with_retry(
        self,
        agent: Agent[None, str],
        user_prompt: str,
        prior_history: list[ModelMessage],
        limits: UsageLimits,
    ) -> Any:  # noqa: ANN401 - pydantic-ai's AgentRunResult is internally typed; we treat it as opaque here
        """Run ``agent.run_sync`` with retry-with-backoff on transient transport errors.

        Retries are bounded by ``self._config.max_retries`` (total attempts,
        including the first). Only :data:`_TRANSIENT_TRANSPORT_ERRORS` types
        trigger a retry; every other exception — auth, rate limit, HTTP
        status, usage-limit-exceeded — propagates to the caller on the first
        failure so the existing fallback / translation logic can handle it
        unchanged.

        After the attempt budget is exhausted, the last transport exception
        is re-raised (``reraise=True``). The outer try/except in
        :meth:`_run_with_fallback` then catches it and routes to the fallback
        model — preserving prior behavior for the "primary really is down"
        case.
        """
        retryer = Retrying(
            stop=stop_after_attempt(self._config.max_retries),
            wait=wait_random_exponential(
                multiplier=_RETRY_BACKOFF_MULTIPLIER,
                max=_RETRY_BACKOFF_MAX_SECONDS,
            ),
            retry=retry_if_exception_type(_TRANSIENT_TRANSPORT_ERRORS),
            before_sleep=_log_retry_attempt,
            # Explicit ``time.sleep`` so tests can monkeypatch it; tenacity's
            # default is captured at import time which makes patching brittle.
            sleep=time.sleep,
            reraise=True,
        )
        return retryer(
            agent.run_sync,
            user_prompt,
            message_history=prior_history or None,
            usage_limits=limits,
        )

    def _run_with_fallback(  # noqa: C901, PLR0913 - fallback/error mapping is intentionally centralized
        self,
        *,
        user_prompt: str,
        prior_history: list[ModelMessage],
        pai_tools: list[PAITool],
        conv_system: str,
        model_name: str,
        fallback_used: bool,
        max_steps: int,
    ) -> tuple[Any, str, bool]:
        """Run ``agent.run_sync`` with primary→fallback on 429/5xx."""
        limits = UsageLimits(request_limit=max_steps)
        model = self._build_model(model_name)
        agent: Agent[None, str] = Agent(
            model,
            tools=pai_tools,
            system_prompt=conv_system,
        )
        try:
            result = self._agent_run_with_retry(
                agent, user_prompt, prior_history, limits
            )
        except UsageLimitExceeded as err:
            msg = f"Agent loop exceeded max_steps={max_steps}"
            raise AIStepBudgetExceededError(msg) from err
        except openai.AuthenticationError as err:
            raise AIAuthenticationError(str(err)) from err
        except openai.APITimeoutError as err:
            raise AITimeoutError(str(err)) from err
        except openai.APIConnectionError as err:
            return self._try_fallback(
                err=err,
                reason="connection_error",
                user_prompt=user_prompt,
                prior_history=prior_history,
                pai_tools=pai_tools,
                conv_system=conv_system,
                from_model=model_name,
                fallback_used=fallback_used,
                limits=limits,
            )
        except openai.RateLimitError as err:
            return self._try_fallback(
                err=err,
                reason="rate_limit",
                user_prompt=user_prompt,
                prior_history=prior_history,
                pai_tools=pai_tools,
                conv_system=conv_system,
                from_model=model_name,
                fallback_used=fallback_used,
                limits=limits,
            )
        except (openai.APIStatusError, ModelHTTPError) as err:
            status = _http_status(err)
            # pydantic-ai wraps 429 as ModelHTTPError instead of
            # openai.RateLimitError; handle it explicitly here so callers
            # get AIRateLimitError and the fallback path is triggered.
            if status == _HTTP_RATE_LIMIT_STATUS:
                return self._try_fallback(
                    err=err,
                    reason="rate_limit",
                    user_prompt=user_prompt,
                    prior_history=prior_history,
                    pai_tools=pai_tools,
                    conv_system=conv_system,
                    from_model=model_name,
                    fallback_used=fallback_used,
                    limits=limits,
                )
            if status >= _HTTP_SERVER_ERROR_MIN:
                return self._try_fallback(
                    err=err,
                    reason="server_error",
                    user_prompt=user_prompt,
                    prior_history=prior_history,
                    pai_tools=pai_tools,
                    conv_system=conv_system,
                    from_model=model_name,
                    fallback_used=fallback_used,
                    limits=limits,
                )
            msg = f"Provider returned HTTP {status}: {err}"
            raise AIProviderError(msg) from err
        except UnexpectedModelBehavior as err:
            # OpenRouter sometimes returns HTTP 200 with no choices.
            return self._try_fallback(
                err=err,
                reason="empty_choices",
                user_prompt=user_prompt,
                prior_history=prior_history,
                pai_tools=pai_tools,
                conv_system=conv_system,
                from_model=model_name,
                fallback_used=fallback_used,
                limits=limits,
            )
        else:
            return result, model_name, fallback_used

    async def _stream_pai_events(  # noqa: PLR0913 - mirrors pydantic-ai run args
        self,
        *,
        user_prompt: str,
        prior_history: list[ModelMessage],
        pai_tools: list[PAITool],
        conv_system: str,
        model_name: str,
        max_steps: int,
    ) -> AsyncIterator[object]:
        """Yield pydantic-ai stream events for one model run."""
        limits = UsageLimits(request_limit=max_steps)
        model = self._build_model(model_name)
        agent: Agent[None, str] = Agent(
            model,
            tools=pai_tools,
            system_prompt=conv_system,
        )
        async for event in agent.run_stream_events(
            user_prompt,
            message_history=prior_history or None,
            usage_limits=limits,
        ):
            yield event

    def _try_fallback(  # noqa: C901, PLR0913 - fallback/error mapping is intentionally centralized
        self,
        *,
        err: Exception,
        reason: str,
        user_prompt: str,
        prior_history: list[ModelMessage],
        pai_tools: list[PAITool],
        conv_system: str,
        from_model: str,
        fallback_used: bool,
        limits: UsageLimits,
    ) -> tuple[Any, str, bool]:
        """Emit a ``model_fallback`` event and retry with the fallback model."""
        if fallback_used or not self._config.fallback_model:
            if reason == "rate_limit":
                raise AIRateLimitError(str(err)) from err
            raise AIProviderError(str(err)) from err

        to_model = self._config.fallback_model
        self.emit(
            AgentEvent(
                kind="model_fallback",
                payload={
                    "from_model": from_model,
                    "to_model": to_model,
                    "reason": reason,
                },
            )
        )
        fallback_model = self._build_model(to_model)
        agent: Agent[None, str] = Agent(
            fallback_model,
            tools=pai_tools,
            system_prompt=conv_system,
        )
        try:
            result = self._agent_run_with_retry(
                agent, user_prompt, prior_history, limits
            )
        except openai.AuthenticationError as ferr:
            raise AIAuthenticationError(str(ferr)) from ferr
        except openai.RateLimitError as ferr:
            raise AIRateLimitError(str(ferr)) from ferr
        except openai.APITimeoutError as ferr:
            raise AITimeoutError(str(ferr)) from ferr
        except openai.APIConnectionError as ferr:
            msg = f"Both primary and fallback models failed. Last: {ferr}"
            raise AIProviderError(msg) from ferr
        except (openai.APIStatusError, ModelHTTPError) as ferr:
            fstatus = _http_status(ferr)
            # pydantic-ai can wrap the fallback 429 as ModelHTTPError too.
            if fstatus == _HTTP_RATE_LIMIT_STATUS:
                raise AIRateLimitError(str(ferr)) from ferr
            msg = f"Both primary and fallback models failed. Last: {ferr}"
            raise AIProviderError(msg) from ferr
        except UnexpectedModelBehavior as ferr:
            msg = f"Both primary and fallback models failed. Last: {ferr}"
            raise AIProviderError(msg) from ferr
        else:
            return result, to_model, True

    def _as_conversation(self, prompt: str | Conversation) -> Conversation:
        if isinstance(prompt, Conversation):
            return prompt
        conv = Conversation(system=self._config.system_prompt or DEFAULT_SYSTEM_PROMPT)
        conv.add_user(prompt)
        return conv

    def _split_conv(self, conv: Conversation) -> tuple[str, list[ModelMessage]]:
        """Extract the last user message and convert prior history.

        Returns ``(user_prompt, prior_history_for_pydantic_ai)``.
        """
        messages = list(conv.messages())
        # Find the last user message.
        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role is Role.USER:
                last_user_idx = i
                break
        if last_user_idx < 0:
            return "", []
        user_prompt = messages[last_user_idx].content or ""
        prior = messages[:last_user_idx]
        return user_prompt, _conv_to_pai_history(prior)

    def _apply_new_messages(
        self,
        conv: Conversation,
        new_messages: list[ModelMessage],
    ) -> tuple[str, int]:
        """Mutate *conv* with the assistant turns produced by pydantic-ai.

        Returns ``(final_text, step_count)`` where ``step_count`` is the
        number of model-response rounds (≥ 1).
        """
        final_text = ""
        step_count = 0
        for msg in new_messages:
            if isinstance(msg, ModelRequest):
                tool_returns = [p for p in msg.parts if isinstance(p, ToolReturnPart)]
                for part in tool_returns:
                    conv.add_tool_result(
                        tool_call_id=part.tool_call_id,
                        content=str(part.content),
                        name=part.tool_name,
                    )
                # ModelRequests with UserPromptPart / SystemPromptPart are
                # skipped — the user message is already in ``conv``.
            elif isinstance(msg, ModelResponse):
                text_parts = [p for p in msg.parts if isinstance(p, TextPart)]
                tc_parts = [p for p in msg.parts if isinstance(p, ToolCallPart)]
                text = "".join(p.content for p in text_parts)
                step_count += 1
                if tc_parts:
                    tc_requests = tuple(
                        ToolCallRequest(
                            id=p.tool_call_id or f"tc-{p.tool_name}-{i}",
                            name=p.tool_name,
                            arguments=p.args_as_dict(),
                        )
                        for i, p in enumerate(tc_parts)
                    )
                    conv.add_assistant(text, tool_calls=tc_requests)
                else:
                    final_text = text
                    conv.add_assistant(final_text)
        return final_text, max(step_count, 1)

    def _capture_raw_from_result(
        self, new_messages: list[ModelMessage], *, model: str
    ) -> None:
        """Populate the debug ring-buffer from pydantic-ai messages."""
        for msg in new_messages:
            if not isinstance(msg, ModelResponse):
                continue
            text_parts = [p for p in msg.parts if isinstance(p, TextPart)]
            tc_parts = [p for p in msg.parts if isinstance(p, ToolCallPart)]
            entry: dict[str, Any] = {
                "model": model,
                "content": "".join(p.content for p in text_parts) or None,
                "tool_calls": [
                    {"name": p.tool_name, "arguments": p.args_as_dict()}
                    for p in tc_parts
                ],
                "finish_reason": "tool_calls" if tc_parts else "stop",
            }
            self._last_raw_completions.append(entry)
            if len(self._last_raw_completions) > self._max_raw_completions:
                self._last_raw_completions.pop(0)


# ---------------------------------------------------------------------------
# Conversation ↔ pydantic-ai message format conversion
# ---------------------------------------------------------------------------


def _conv_to_pai_history(messages: list[Any]) -> list[ModelMessage]:
    """Convert a prefix of our ``Conversation.messages()`` to pydantic-ai format.

    System messages are skipped (the Agent's ``system_prompt`` handles them).
    Consecutive TOOL-role messages are bundled into a single ``ModelRequest``
    with multiple ``ToolReturnPart`` objects, matching pydantic-ai's expected
    wire format.
    """
    result: list[ModelMessage] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.role is Role.SYSTEM:
            i += 1
        elif msg.role is Role.USER:
            result.append(ModelRequest(parts=[UserPromptPart(msg.content or "")]))
            i += 1
        elif msg.role is Role.ASSISTANT:
            if msg.tool_calls:
                parts: list[Any] = []
                if msg.content:
                    parts.append(TextPart(msg.content))
                parts.extend(
                    ToolCallPart(
                        tool_name=tc.name,
                        args=cast("dict[str, Any]", dict(tc.arguments)),
                        tool_call_id=tc.id,
                    )
                    for tc in msg.tool_calls
                )
                result.append(ModelResponse(parts=parts))
            else:
                result.append(ModelResponse(parts=[TextPart(msg.content or "")]))
            i += 1
        elif msg.role is Role.TOOL:
            # Group consecutive TOOL messages into one ModelRequest.
            tool_parts: list[ToolReturnPart] = []
            while i < len(messages) and messages[i].role is Role.TOOL:
                tm = messages[i]
                tool_parts.append(
                    ToolReturnPart(
                        tool_name=tm.name or "",
                        content=tm.content or "",
                        tool_call_id=tm.tool_call_id or "",
                    )
                )
                i += 1
            result.append(ModelRequest(parts=tool_parts))
        else:
            i += 1
    return result


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _http_status(err: Exception) -> int:
    """Extract the HTTP status code from an openai or pydantic-ai exception."""
    if isinstance(err, openai.APIStatusError):
        return err.status_code
    if isinstance(err, ModelHTTPError):
        return err.status_code
    return 0


def _translate_provider_exception(
    exc: Exception,
    *,
    max_steps: int,
) -> Exception:
    """Map provider/pydantic exceptions to the AI client error hierarchy."""
    if isinstance(exc, AIProviderError | AIRateLimitError | AITimeoutError):
        translated: Exception = exc
    elif isinstance(exc, UsageLimitExceeded):
        translated = AIStepBudgetExceededError(
            f"Agent loop exceeded max_steps={max_steps}"
        )
    elif isinstance(exc, openai.AuthenticationError):
        translated = AIAuthenticationError(str(exc))
    elif isinstance(exc, openai.APITimeoutError):
        translated = AITimeoutError(str(exc))
    elif isinstance(exc, openai.RateLimitError):
        translated = AIRateLimitError(str(exc))
    elif isinstance(exc, openai.APIConnectionError):
        translated = AIProviderError(str(exc))
    elif isinstance(exc, openai.APIStatusError | ModelHTTPError):
        status = _http_status(exc)
        if status == _HTTP_RATE_LIMIT_STATUS:
            translated = AIRateLimitError(str(exc))
        else:
            translated = AIProviderError(f"Provider returned HTTP {status}: {exc}")
    elif isinstance(exc, UnexpectedModelBehavior):
        translated = AIProviderError(str(exc))
    else:
        translated = exc
    return translated


def _stream_event_from_pai(sequence: int, event: object) -> AIStreamEvent | None:
    """Translate pydantic-ai stream events into the provider-neutral stream."""
    if isinstance(event, PartStartEvent):
        return _part_start_stream_event(sequence, event)
    if isinstance(event, PartDeltaEvent):
        return _part_delta_stream_event(sequence, event)
    if isinstance(event, PartEndEvent):
        return _part_end_stream_event(sequence, event)
    if isinstance(event, FunctionToolCallEvent):
        return _tool_call_stream_event(sequence, event)
    if isinstance(event, FunctionToolResultEvent):
        return _tool_result_stream_event(sequence, event)
    return None


def _part_start_stream_event(
    sequence: int,
    event: PartStartEvent,
) -> AIStreamEvent | None:
    """Translate a provider part-start event."""
    part = event.part
    if isinstance(part, TextPart) and part.content:
        return AIStreamEvent(
            kind="text_delta",
            sequence=sequence,
            payload={"delta": part.content, "part_index": event.index},
        )
    if isinstance(part, ThinkingPart):
        content = getattr(part, "content", None)
        if isinstance(content, str) and content:
            return AIStreamEvent(
                kind="reasoning_delta",
                sequence=sequence,
                payload={"delta": content, "part_index": event.index},
            )
    return None


def _part_delta_stream_event(
    sequence: int,
    event: PartDeltaEvent,
) -> AIStreamEvent | None:
    """Translate a provider part-delta event."""
    delta = event.delta
    if isinstance(delta, TextPartDelta) and delta.content_delta:
        return AIStreamEvent(
            kind="text_delta",
            sequence=sequence,
            payload={"delta": delta.content_delta, "part_index": event.index},
        )
    if isinstance(delta, ThinkingPartDelta) and delta.content_delta:
        return AIStreamEvent(
            kind="reasoning_delta",
            sequence=sequence,
            payload={"delta": delta.content_delta, "part_index": event.index},
        )
    return None


def _part_end_stream_event(
    sequence: int,
    event: PartEndEvent,
) -> AIStreamEvent | None:
    """Translate a provider part-end event."""
    if not isinstance(event.part, TextPart):
        return None
    return AIStreamEvent(
        kind="text_completed",
        sequence=sequence,
        payload={
            "part_index": event.index,
            "text": event.part.content,
        },
    )


def _tool_call_stream_event(
    sequence: int,
    event: FunctionToolCallEvent,
) -> AIStreamEvent:
    """Translate a provider tool-call-start event."""
    return AIStreamEvent(
        kind="tool_call_started",
        sequence=sequence,
        payload={
            "tool_call_id": event.tool_call_id,
            "name": event.part.tool_name,
            "arguments": event.part.args_as_dict(),
            "args_valid": event.args_valid,
        },
    )


def _tool_result_stream_event(
    sequence: int,
    event: FunctionToolResultEvent,
) -> AIStreamEvent:
    """Translate a provider tool-call-result event."""
    return AIStreamEvent(
        kind="tool_call_completed",
        sequence=sequence,
        payload={
            "tool_call_id": event.tool_call_id,
            "content": event.content if isinstance(event.content, str) else None,
        },
    )


def _record_destructive_attempt(
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Log and breadcrumb a destructive-tool attempt for the audit dashboard."""
    safe_arguments = _redact_tool_arguments(arguments)
    if _sentry_sdk is not None:
        _sentry_sdk.add_breadcrumb(
            category="ai.tool.destructive",
            level="warning",
            message=f"destructive tool invoked: {tool_name}",
            data={"arguments": safe_arguments},
        )
    log.info(
        "destructive_tool_attempt",
        tool_name=tool_name,
        argument_keys=sorted(arguments),
    )


def _fingerprint(value: object) -> str:
    text = str(value)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}:len:{len(text)}"


def _redact_tool_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Return tool arguments safe for events, breadcrumbs, and audit previews."""
    sensitive_keys = {
        "api_key",
        "authorization",
        "aws_access_key_id",
        "aws_secret_access_key",
        "bot_token",
        "content",
        "openrouter_api_key",
        "secret",
        "token",
    }
    path_like_keys = {
        "dest_path",
        "file_name",
        "local_path",
        "object_name",
        "remote_path",
        "save_as",
        "source_path",
    }
    redacted: dict[str, Any] = {}
    for key, value in arguments.items():
        normalized = str(key).lower()
        if normalized in sensitive_keys or normalized.endswith(("_key", "_secret")):
            redacted[str(key)] = "<redacted>"
        elif normalized in path_like_keys:
            redacted[str(key)] = _fingerprint(value)
        else:
            redacted[str(key)] = value
    return redacted


def _format_result(result: object) -> str:
    """Serialize a handler return value to a human-readable string."""
    if isinstance(result, str):
        return result
    try:
        # orjson is ~10x faster than stdlib json for large payloads (e.g. the
        # base64 content field from read_file at the 10 MB ceiling).  It
        # returns bytes; decode to str for the tool-result string.
        return orjson.dumps(result, default=str).decode()
    except (TypeError, orjson.JSONEncodeError):
        return str(result)


def _sandbox_result(text: str) -> str:
    r"""Wrap, sanitize, and truncate a tool result before feeding it to the model.

    Steps applied in order:

    1. Strip ASCII control characters (C0 block minus tab/LF/CR, plus DEL).
       These can be used for prompt injection — e.g. a file that starts with
       ``\x1b[H\x1b[2J`` to clear the terminal, or a crafted tool result that
       exploits a model's special-token handling.
    2. Truncate to ``_TOOL_RESULT_MAX_CHARS`` with a ``...[truncated]`` suffix.
    3. Wrap in ``<tool_result source="untrusted">`` so the system prompt can
       instruct the model to treat this block as untrusted data.
    """
    text = _CONTROL_CHARS_RE.sub("", text)
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        text = text[:_TOOL_RESULT_MAX_CHARS] + "\n...[truncated]"
    return f'<tool_result source="untrusted">\n{text}\n</tool_result>'


def get_client_impl(*, interactive: bool = False) -> OpenRouterClient:  # noqa: ARG001 - reserved for factory compatibility
    """Concrete factory that returns an :class:`OpenRouterClient` from env."""
    return OpenRouterClient(OpenRouterConfig.from_env())
