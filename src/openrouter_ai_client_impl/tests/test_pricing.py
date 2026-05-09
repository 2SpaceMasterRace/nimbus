"""Unit tests for the OpenRouter pricing table and cost estimator."""

from __future__ import annotations

import pytest
from openrouter_ai_client_impl.pricing import (
    estimate_cost_usd,
    model_price,
)

from ai_client_api import TokenUsage

pytestmark = pytest.mark.unit


def test_known_paid_model_returns_table_price() -> None:
    """Looking up a model we explicitly priced returns its tuple."""
    assert model_price("openai/gpt-4o-mini") == (0.15, 0.60)


def test_known_free_model_returns_zero_zero() -> None:
    """Free-tier defaults sit in the table as ($0/M, $0/M)."""
    assert model_price("openai/gpt-oss-120b:free") == (0.0, 0.0)


def test_unknown_model_returns_none_not_zero() -> None:
    """We must not silently fabricate $0 for models we don't know.

    Returning ``None`` lets the caller distinguish "really free" from "we have
    no idea" — important for dashboards that would otherwise misreport spend.
    """
    assert model_price("vendor/never-heard-of-it") is None


def test_estimate_uses_per_million_token_rates() -> None:
    """The estimator divides token counts by 1M before applying the rate.

    ``gpt-4o-mini`` is priced at $0.15/M input, $0.60/M output. A response
    with 1M input + 500K output should cost exactly $0.15 + $0.30 = $0.45.
    """
    cost = estimate_cost_usd(
        "openai/gpt-4o-mini",
        TokenUsage(input_tokens=1_000_000, output_tokens=500_000),
    )
    assert cost == pytest.approx(0.45)


def test_estimate_handles_small_token_counts_without_underflow() -> None:
    """A typical small response (~100 in / ~200 out) yields a sub-cent estimate."""
    cost = estimate_cost_usd(
        "openai/gpt-4o-mini",
        TokenUsage(input_tokens=100, output_tokens=200),
    )
    # 100 * 0.15 / 1e6 + 200 * 0.60 / 1e6 = 0.000015 + 0.00012 = 0.000135
    assert cost == pytest.approx(0.000135)


def test_estimate_returns_zero_for_free_models() -> None:
    """Free-tier models always cost exactly $0, regardless of token count."""
    cost = estimate_cost_usd(
        "openai/gpt-oss-120b:free",
        TokenUsage(input_tokens=10_000, output_tokens=20_000),
    )
    assert cost == 0.0


def test_estimate_returns_none_for_unknown_model() -> None:
    """An unpriced model returns ``None`` rather than 0.0 or a guess."""
    cost = estimate_cost_usd(
        "private/internal-eval-model",
        TokenUsage(input_tokens=100, output_tokens=100),
    )
    assert cost is None


def test_estimate_handles_zero_tokens() -> None:
    """A response with zero token usage is a legal $0.00 observation."""
    cost = estimate_cost_usd(
        "openai/gpt-4o-mini",
        TokenUsage(input_tokens=0, output_tokens=0),
    )
    assert cost == 0.0


def test_estimate_clamps_negative_to_zero() -> None:
    """Defensive: if a provider ever reports negative usage, cost is still >= 0.

    Reported provider usage has historically had edge cases (off-by-one,
    cache adjustments). We treat cost as a non-negative observation so
    histogram sums never go negative.
    """
    cost = estimate_cost_usd(
        "openai/gpt-4o-mini",
        TokenUsage(input_tokens=-100, output_tokens=-50),
    )
    assert cost == 0.0
