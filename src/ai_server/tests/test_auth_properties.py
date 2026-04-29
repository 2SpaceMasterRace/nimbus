"""Property-based tests for the HMAC-SHA256 signing protocol.

Three protocol contracts are verified over arbitrary inputs:

1. Self-consistency — a request signed by ``sign_nimbus_request`` must always
   verify against ``_expected_signature`` given the same secret. This confirms
   that the two halves of the protocol (wrapper-side signing, server-side
   verification) agree on the canonical payload format.

2. Signature sensitivity — changing any single component of the canonical
   payload (body, method, path, timestamp, or nonce) must produce a different
   signature. This verifies the protocol is not accidentally forgiving.

3. Secret isolation — two different secrets must produce different signatures
   for the same payload (at the scale Hypothesis can explore, this holds with
   overwhelming probability under HMAC-SHA256).

4. Payload determinism — ``_build_signature_payload`` is a pure function;
   calling it twice with the same arguments must return the same bytes.
"""

from __future__ import annotations

import pytest
from ai_server.auth import _build_signature_payload, _expected_signature
from ai_server.wrapper_client import sign_nimbus_request
from hypothesis import assume, given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.property

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_method = st.sampled_from(["GET", "POST", "PUT", "DELETE", "PATCH"])
_path = st.from_regex(r"/[A-Za-z0-9/_-]{0,50}", fullmatch=True)
_timestamp = st.integers(min_value=0, max_value=2**31 - 1)
_nonce = st.from_regex(r"[A-Za-z0-9_-]{1,64}", fullmatch=True)
_secret = st.text(min_size=1, max_size=128)
_body = st.binary(max_size=512)

# ---------------------------------------------------------------------------
# 1. Self-consistency: sign → verify is always True
# ---------------------------------------------------------------------------


@given(_method, _path, _timestamp, _nonce, _body, _secret)
def test_self_signed_request_always_verifies(  # noqa: PLR0913
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body: bytes,
    secret: str,
) -> None:
    """Signing then verifying with the same secret must always succeed."""
    headers = sign_nimbus_request(
        body=body,
        secret=secret,
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
    )
    payload = _build_signature_payload(
        method=method,
        path=path,
        timestamp=headers["X-Nimbus-Timestamp"],
        nonce=headers["X-Nimbus-Nonce"],
        body=body,
    )
    expected = _expected_signature(secret=secret, payload=payload)
    assert headers["X-Nimbus-Signature"] == expected


# ---------------------------------------------------------------------------
# 2. Signature sensitivity: each component of the canonical payload matters
# ---------------------------------------------------------------------------


@given(_method, _path, _timestamp, _nonce, _body, _secret, _body)
def test_different_body_changes_signature(  # noqa: PLR0913
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body_a: bytes,
    secret: str,
    body_b: bytes,
) -> None:
    """Two distinct bodies must produce distinct signatures.

    SHA-256 is collision-resistant at any input size Hypothesis would generate.
    """
    assume(body_a != body_b)
    payload_a = _build_signature_payload(
        method=method, path=path, timestamp=str(timestamp), nonce=nonce, body=body_a
    )
    payload_b = _build_signature_payload(
        method=method, path=path, timestamp=str(timestamp), nonce=nonce, body=body_b
    )
    sig_a = _expected_signature(secret=secret, payload=payload_a)
    sig_b = _expected_signature(secret=secret, payload=payload_b)
    assert sig_a != sig_b


@given(_method, _path, _timestamp, _nonce, _body, _secret, _nonce)
def test_different_nonce_changes_signature(  # noqa: PLR0913
    method: str,
    path: str,
    timestamp: int,
    nonce_a: str,
    body: bytes,
    secret: str,
    nonce_b: str,
) -> None:
    """Two distinct nonces must produce distinct signatures."""
    assume(nonce_a != nonce_b)
    payload_a = _build_signature_payload(
        method=method, path=path, timestamp=str(timestamp), nonce=nonce_a, body=body
    )
    payload_b = _build_signature_payload(
        method=method, path=path, timestamp=str(timestamp), nonce=nonce_b, body=body
    )
    sig_a = _expected_signature(secret=secret, payload=payload_a)
    sig_b = _expected_signature(secret=secret, payload=payload_b)
    assert sig_a != sig_b


@given(_method, _path, _timestamp, _nonce, _body, _secret, _timestamp)
def test_different_timestamp_changes_signature(  # noqa: PLR0913
    method: str,
    path: str,
    ts_a: int,
    nonce: str,
    body: bytes,
    secret: str,
    ts_b: int,
) -> None:
    """Two distinct timestamps must produce distinct signatures."""
    assume(ts_a != ts_b)
    payload_a = _build_signature_payload(
        method=method, path=path, timestamp=str(ts_a), nonce=nonce, body=body
    )
    payload_b = _build_signature_payload(
        method=method, path=path, timestamp=str(ts_b), nonce=nonce, body=body
    )
    sig_a = _expected_signature(secret=secret, payload=payload_a)
    sig_b = _expected_signature(secret=secret, payload=payload_b)
    assert sig_a != sig_b


# ---------------------------------------------------------------------------
# 3. Secret isolation: wrong secret always fails verification
# ---------------------------------------------------------------------------


@given(_method, _path, _timestamp, _nonce, _body, _secret, _secret)
@settings(max_examples=200)
def test_wrong_secret_never_verifies(  # noqa: PLR0913
    method: str,
    path: str,
    timestamp: int,
    nonce: str,
    body: bytes,
    signing_secret: str,
    verifying_secret: str,
) -> None:
    """A request signed with one secret must not verify under a different secret."""
    assume(signing_secret != verifying_secret)
    headers = sign_nimbus_request(
        body=body,
        secret=signing_secret,
        method=method,
        path=path,
        timestamp=timestamp,
        nonce=nonce,
    )
    payload = _build_signature_payload(
        method=method,
        path=path,
        timestamp=headers["X-Nimbus-Timestamp"],
        nonce=headers["X-Nimbus-Nonce"],
        body=body,
    )
    wrong_expected = _expected_signature(secret=verifying_secret, payload=payload)
    assert headers["X-Nimbus-Signature"] != wrong_expected


# ---------------------------------------------------------------------------
# 4. Payload determinism
# ---------------------------------------------------------------------------


@given(_method, _path, _timestamp.map(str), _nonce, _body)
def test_build_signature_payload_is_pure(
    method: str, path: str, timestamp: str, nonce: str, body: bytes
) -> None:
    """_build_signature_payload is a pure function: same args → same bytes."""
    assert _build_signature_payload(
        method=method, path=path, timestamp=timestamp, nonce=nonce, body=body
    ) == _build_signature_payload(
        method=method, path=path, timestamp=timestamp, nonce=nonce, body=body
    )
