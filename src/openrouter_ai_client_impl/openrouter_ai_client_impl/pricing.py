"""Approximate per-request cost estimation for OpenRouter models.

OpenRouter exposes per-model prices via its ``/api/v1/models`` catalog. To keep
the runtime path free of an extra network call (and to keep tests hermetic), we
hardcode prices for the small set of models we actually run, expressed as USD
per **one million tokens** for input and output respectively. Unknown models
return ``None`` from :func:`estimate_cost_usd` — we'd rather report "unknown"
than fabricate a number.

The table only needs to be accurate to within ~10%. Token telemetry is the
authoritative usage signal; cost is a derived convenience for dashboards and
the CLI ``/cost`` command. Update entries when we change the production model
roster or when OpenRouter publishes a price change worth tracking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_client_api import TokenUsage

_PER_MILLION = 1_000_000.0

# (input_usd_per_1M, output_usd_per_1M) keyed by OpenRouter model id.
# Free-tier models cost nothing in dollars but still consume tokens; we keep
# them in the table so token telemetry attributes still flow through the same
# pricing code path. Numbers reflect OpenRouter's published list pricing as
# of 2026-04 — refresh when the production roster changes.
_MODEL_PRICING_USD_PER_1M: dict[str, tuple[float, float]] = {
    # Free-tier defaults from openrouter_ai_client_impl.config.
    "openai/gpt-oss-120b:free": (0.0, 0.0),
    "meta-llama/llama-3.1-8b-instruct:free": (0.0, 0.0),
    "z-ai/glm-4.5-air:free": (0.0, 0.0),
    "nousresearch/hermes-3-llama-3.1-405b:free": (0.0, 0.0),
    "qwen/qwen3-coder:free": (0.0, 0.0),
    "google/gemma-4-31b-it:free": (0.0, 0.0),
    "google/gemma-3-27b-it:free": (0.0, 0.0),
    "nvidia/nemotron-3-super-120b-a12b:free": (0.0, 0.0),
    # Paid models commonly used in production overrides (see config.py).
    "openai/gpt-4o-mini": (0.15, 0.60),
    "openai/gpt-4o": (2.50, 10.00),
    "google/gemini-2.0-flash-001": (0.10, 0.40),
    "anthropic/claude-3.5-haiku": (0.80, 4.00),
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
}


def model_price(model: str) -> tuple[float, float] | None:
    """Return ``(input_usd_per_1M, output_usd_per_1M)`` for *model* or ``None``.

    Lookup is exact: callers should pass the OpenRouter id verbatim (matching
    what ``OpenRouterConfig.model`` resolves to). Unknown models return
    ``None`` so callers can distinguish "free" (0.0) from "we don't know".
    """
    return _MODEL_PRICING_USD_PER_1M.get(model)


def estimate_cost_usd(model: str, tokens: TokenUsage) -> float | None:
    """Estimate the USD cost of one response.

    Args:
        model: OpenRouter model id (e.g. ``"openai/gpt-4o-mini"``).
        tokens: Token accounting from the response.

    Returns:
        Estimated USD cost as a non-negative float, or ``None`` if the model
        is not in the price table. Returns ``0.0`` for free-tier models.

    """
    price = model_price(model)
    if price is None:
        return None
    input_price, output_price = price
    cost = (
        tokens.input_tokens * input_price + tokens.output_tokens * output_price
    ) / _PER_MILLION
    # Guard against negative inputs leaking through; cost is a non-negative
    # observation.
    return max(cost, 0.0)
