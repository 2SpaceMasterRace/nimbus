"""Tests for slack_bridge.client retry and signing behavior."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from slack_bridge.client import call_nimbus
from slack_bridge.models import NimbusTurnRequest

from slack_bridge import client as bridge_client

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit


@dataclass
class _TransportStub:
    """Programmable in-memory replacement for ``httpx.post`` during tests.

    Each test pushes one or more handlers onto ``handlers``. Every call to
    the patched ``httpx.post`` consumes the next handler in FIFO order
    and records the constructed request on ``requests`` so assertions can
    verify retry counts, signed headers, and request bodies.
    """

    requests: list[httpx.Request] = field(default_factory=list)
    handlers: list[Callable[[httpx.Request], httpx.Response]] = field(
        default_factory=list,
    )


_BASE_URL = "https://nimbus.example/api"


def _turn_request() -> NimbusTurnRequest:
    """Build a deterministic NimbusTurnRequest for client tests."""
    return NimbusTurnRequest(
        platform="slack",
        workspace_id="T1",
        channel_id="C1",
        message_id="m1",
        user_id="U1",
        text="hi",
        idempotency_key="slack:T1:event:E1",
        thread_id="m1",
        request_id="slack-E1",
    )


def _success_payload() -> dict[str, Any]:
    """Build a minimal valid Nimbus response payload."""
    return {
        "request_id": "slack-E1",
        "conversation_id": "conv-1",
        "text": "ok",
        "outcome": "reply",
        "confirmation_required": False,
        "suggested_next_actions": [],
        "model": "nimbus-runtime",
        "steps": 1,
        "fallback_used": False,
    }


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the env vars required by call_nimbus."""
    monkeypatch.setenv("AI_SERVER_BASE_URL", _BASE_URL)
    monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", "test-secret")


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the retry backoff sleeps with no-ops for fast unit tests."""
    monkeypatch.setattr(bridge_client.time, "sleep", lambda _: None)


@pytest.fixture
def transport_stub(monkeypatch: pytest.MonkeyPatch) -> _TransportStub:
    """Stub ``httpx.post`` with a programmable, FIFO handler chain."""
    stub = _TransportStub()

    def _post(
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        del timeout
        request = httpx.Request("POST", url, content=content, headers=headers)
        stub.requests.append(request)
        if not stub.handlers:
            msg = "no more stub handlers configured"
            raise AssertionError(msg)
        handler = stub.handlers.pop(0)
        return handler(request)

    monkeypatch.setattr(bridge_client.httpx, "post", _post)
    return stub


def _ok(payload: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    """Return a handler that responds with a 200 JSON payload."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            request=request,
            headers={"content-type": "application/json"},
        )

    return _handler


def _status(code: int) -> Callable[[httpx.Request], httpx.Response]:
    """Return a handler that responds with ``code`` and an empty body."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, request=request)

    return _handler


def _transport_error(message: str) -> Callable[[httpx.Request], httpx.Response]:
    """Return a handler that raises ``httpx.ConnectError``."""

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(message, request=request)

    return _handler


def test_call_nimbus_returns_parsed_result_on_success(
    env: None,
    transport_stub: _TransportStub,
) -> None:
    """A 200 response is parsed and returned without retries."""
    del env
    transport_stub.handlers.append(_ok(_success_payload()))
    result = call_nimbus(_turn_request())
    assert result.text == "ok"
    assert result.outcome == "reply"
    assert len(transport_stub.requests) == 1


def test_call_nimbus_retries_on_transport_error_then_succeeds(
    env: None,
    transport_stub: _TransportStub,
) -> None:
    """A transient transport error is retried and a later success returned."""
    del env
    transport_stub.handlers.extend(
        [
            _transport_error("connection reset"),
            _ok(_success_payload()),
        ],
    )
    result = call_nimbus(_turn_request())
    assert result.text == "ok"
    assert len(transport_stub.requests) == 2


def test_call_nimbus_retries_on_5xx_then_succeeds(
    env: None,
    transport_stub: _TransportStub,
) -> None:
    """A 5xx response is retried and the eventual success returned."""
    del env
    transport_stub.handlers.extend(
        [
            _status(503),
            _ok(_success_payload()),
        ],
    )
    result = call_nimbus(_turn_request())
    assert result.text == "ok"
    assert len(transport_stub.requests) == 2


def test_call_nimbus_does_not_retry_on_4xx(
    env: None,
    transport_stub: _TransportStub,
) -> None:
    """4xx responses are non-retryable and surface the HTTPStatusError immediately."""
    del env
    transport_stub.handlers.append(_status(409))
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        call_nimbus(_turn_request())
    assert exc_info.value.response.status_code == 409
    assert len(transport_stub.requests) == 1


def test_call_nimbus_raises_after_max_attempts(
    env: None,
    transport_stub: _TransportStub,
) -> None:
    """If every attempt fails transiently, the last error is re-raised."""
    del env
    transport_stub.handlers.extend(
        [
            _status(502),
            _status(503),
            _status(504),
        ],
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        call_nimbus(_turn_request())
    assert exc_info.value.response.status_code == 504
    assert len(transport_stub.requests) == 3


def test_call_nimbus_signs_each_attempt_with_a_fresh_nonce(
    env: None,
    transport_stub: _TransportStub,
) -> None:
    """Retried attempts must carry a fresh nonce so the server can re-validate."""
    del env
    transport_stub.handlers.extend(
        [
            _status(503),
            _ok(_success_payload()),
        ],
    )
    call_nimbus(_turn_request())
    nonces = [req.headers["X-Nimbus-Nonce"] for req in transport_stub.requests]
    assert len(nonces) == 2
    assert nonces[0] != nonces[1]


def test_call_nimbus_keeps_idempotency_key_stable_across_retries(
    env: None,
    transport_stub: _TransportStub,
) -> None:
    """The signed body (and idempotency_key in it) is byte-stable per call."""
    del env
    transport_stub.handlers.extend(
        [
            _status(500),
            _ok(_success_payload()),
        ],
    )
    call_nimbus(_turn_request())
    bodies = [req.read() for req in transport_stub.requests]
    assert bodies[0] == bodies[1]
    payload = json.loads(bodies[0])
    assert payload["idempotency_key"] == "slack:T1:event:E1"


def test_call_nimbus_requires_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing AI_SERVER_BASE_URL is a programming/config error."""
    monkeypatch.delenv("AI_SERVER_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="AI_SERVER_BASE_URL"):
        call_nimbus(_turn_request())


def test_sign_request_requires_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing AI_SERVER_SIGNING_SECRET is a programming/config error."""
    monkeypatch.delenv("AI_SERVER_SIGNING_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="AI_SERVER_SIGNING_SECRET"):
        bridge_client.sign_request(b"{}")
